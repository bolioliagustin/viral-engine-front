"""
S3: Transcription Cache Service
Caches video transcriptions in Supabase to avoid re-transcribing already processed videos.
Saves ~$0.006/min on Whisper API costs per cache hit.

W4: la cache se lee además de escribirse, y guarda el Transcript completo por
(video_id, fuente, modelo). La tabla `transcription_cache` tiene PK `video_id`
(text), así que sin migración la clave compuesta va en esa columna:
  - captions de YouTube (Supadata): `video_id` a secas (igual que siempre)
  - Whisper del audio completo:     `video_id:whisper_full:whisper-large-v3-turbo`
Además queda una copia local en `downloads/`.

W18 (docs/briefs/W18-cache-integra.md, job fb287cba):
  - Todo transcript lleva una **Huella del transcript** (`transcript_fingerprint`,
    CONTEXT.md): fuente, modelo, idioma, pista de audio y hash del texto de sus
    Líneas. `analysis_cache` la guarda y la compara.
  - La copia local se usa SOLO si Supabase no se pudo consultar (error o sin
    cliente). Si Supabase responde "no hay fila", la copia local está vieja
    (en el VPS `downloads/` es el volumen `worker_downloads`, que sobrevive a
    los deploys) y se borra.
  - Guardia de idioma: con `TRANSCRIPT_LANGUAGE`, un transcript cacheado cuyo
    idioma dominante es otro se descarta.
"""
import hashlib
import json
import os
import re
from pathlib import Path

from services.supabase_client import get_supabase

_LOCAL_DIR = Path(__file__).parent.parent / "downloads"

# Umbral de la guardia de idioma: el idioma esperado tiene que ser el dominante
# entre las palabras reconocidas. Con menos de este mínimo de palabras
# reconocidas no se decide (transcript chico o sin palabras funcionales).
_LANG_GUARD_MIN_WORDS = 30


def transcript_cache_key(video_id: str, source: str | None = None, model: str | None = None) -> str:
    """Clave en `transcription_cache.video_id`. Supadata sigue siendo el `video_id` pelado."""
    if not source or source == "supadata":
        return video_id
    return f"{video_id}:{source}:{model or 'whisper'}"


def _local_path(video_id: str, source: str | None, model: str | None) -> Path | None:
    if not source or source == "supadata":
        return None
    safe_model = (model or "whisper").replace("/", "_")
    return _LOCAL_DIR / f"{video_id}_transcript_{source}_{safe_model}.json"


# ─── Huella del transcript (W18) ────────────────────────────────────────────
def _lang2(lang: str | None) -> str:
    return (lang or "").strip().lower().split("-")[0]


def transcript_fingerprint(transcript: dict | None) -> str:
    """
    Huella estable del transcript: `fuente|modelo|idioma|pista|sha1-16`.

    El hash cubre el texto y el inicio (redondeado a 0,1 s) de cada Línea
    (`lines`; si no hay, `segments`). Es determinística: no entra ninguna
    fecha ni el orden de las claves del dict. Para captions el modelo y la
    pista quedan vacíos. Dos transcripts del mismo video sobre pistas de
    audio distintas (doblaje contra original) dan huellas distintas aunque
    compartan fuente y modelo, porque cambia el texto.
    """
    t = transcript or {}
    source = (t.get("source") or "supadata").strip().lower()
    model = "" if source == "supadata" else str(t.get("model") or "")
    track = str(t.get("audio_track") or "")
    items = t.get("lines") or t.get("segments") or []
    h = hashlib.sha1()
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            start = f"{float(it.get('start') or 0):.1f}"
        except (TypeError, ValueError):
            start = "0.0"
        h.update(f"{start}\t{(it.get('text') or '').strip()}\n".encode("utf-8"))
    return f"{source}|{model}|{_lang2(t.get('language'))}|{track}|{h.hexdigest()[:16]}"


# ─── Guardia de idioma (W18) ────────────────────────────────────────────────
# Palabras funcionales muy frecuentes y casi exclusivas de cada idioma. Alcanza
# para distinguir un doblaje en inglés de un programa en español (fb287cba);
# no pretende ser un detector general.
_LANG_STOPWORDS = {
    "es": {"el", "los", "las", "del", "una", "que", "y", "pero", "porque", "como",
           "está", "es", "yo", "vos", "usted", "entonces", "muy", "más", "cuando",
           "también", "tiene", "hay", "fue", "eso", "esto", "nada", "algo", "con"},
    "en": {"the", "and", "you", "that", "is", "was", "what", "this", "with", "have",
           "it's", "i'm", "don't", "they", "we", "he", "she", "of", "to", "but",
           "because", "just", "so", "like", "there", "are", "were", "be", "not"},
    "pt": {"não", "você", "é", "uma", "eu", "isso", "então", "mais", "muito", "também",
           "tem", "foi", "ele", "ela", "para", "com", "mas", "porque", "está", "aqui"},
}
_WORD_RE = re.compile(r"[a-záéíóúñüçãõâêô']+")


def _language_counts(transcript: dict | None) -> dict[str, int]:
    """Palabras funcionales reconocidas por idioma. Una palabra que está en
    más de una lista (p. ej. "com") no cuenta para ninguna."""
    t = transcript or {}
    text = t.get("text") or " ".join(
        (it.get("text") or "") for it in (t.get("lines") or t.get("segments") or [])
        if isinstance(it, dict)
    )
    counts = {lang: 0 for lang in _LANG_STOPWORDS}
    for w in _WORD_RE.findall(text.lower()):
        owners = [lang for lang, sw in _LANG_STOPWORDS.items() if w in sw]
        if len(owners) == 1:
            counts[owners[0]] += 1
    return counts


def dominant_language(transcript: dict | None) -> tuple[str | None, float, int]:
    """
    Idioma dominante por mayoría de palabras funcionales reconocidas.

    Returns (idioma, fracción de las palabras reconocidas que son de ese
    idioma, cantidad de palabras reconocidas).
    """
    counts = _language_counts(transcript)
    total = sum(counts.values())
    if not total:
        return None, 0.0, 0
    lang = max(counts, key=lambda k: counts[k])
    return lang, counts[lang] / total, total


def language_mismatch(transcript: dict | None, expected: str | None = None) -> str | None:
    """
    Motivo legible si el transcript no está mayoritariamente en el idioma
    esperado (`TRANSCRIPT_LANGUAGE` por default), o None si pasa o no se
    puede decidir. Solo se aplica cuando el idioma esperado está en la
    tabla de palabras funcionales.
    """
    expected = _lang2(expected if expected is not None else os.getenv("TRANSCRIPT_LANGUAGE"))
    if not expected or expected not in _LANG_STOPWORDS:
        return None
    counts = _language_counts(transcript)
    total = sum(counts.values())
    if total < _LANG_GUARD_MIN_WORDS:
        return None
    lang = max(counts, key=lambda k: counts[k])
    if lang == expected:
        return None
    return (
        f"idioma dominante {lang} ({counts[lang] / total * 100:.0f} % de {total} palabras "
        f"reconocidas; {expected}: {counts[expected] / total * 100:.0f} %), se esperaba {expected}"
    )


def _accept_cached(transcript: dict, where: str) -> dict | None:
    """Guardia de idioma sobre un transcript leído de la caché (W18)."""
    reason = language_mismatch(transcript)
    if reason:
        print(f"🚫 Transcript cacheado ({where}) descartado por idioma: {reason}. Se recalcula.")
        return None
    return transcript


def get_cached_transcript(video_id: str, source: str | None = None, model: str | None = None) -> dict | None:
    """
    Check if a transcript exists in cache for the given video_id.
    Returns the cached transcript dict or None if not found.

    `source`/`model` (W4) buscan el Transcript completo de esa fuente; sin
    ellos, el comportamiento es el de siempre (captions por `video_id`).

    W18: la copia local se usa solo si Supabase no se pudo consultar (lanzó
    un error o no hay cliente). Si Supabase respondió sin fila, la copia
    local se ignora y se borra.
    """
    key = transcript_cache_key(video_id, source, model)
    local = _local_path(video_id, source, model)
    supabase = get_supabase()
    supabase_answered = False
    if supabase:
        try:
            result = supabase.table("transcription_cache") \
                .select("transcript, language, duration_seconds") \
                .eq("video_id", key) \
                .limit(1) \
                .execute()
            supabase_answered = True

            if result.data and len(result.data) > 0:
                cached = result.data[0]
                transcript = cached["transcript"]
                # transcript is stored as JSON string
                if isinstance(transcript, str):
                    transcript = json.loads(transcript)
                return _accept_cached(transcript, "Supabase")
        except Exception as e:
            print(f"⚠️ Cache lookup failed (non-fatal): {e}")

    if supabase_answered:
        if local and local.exists():
            print(f"🗑️ Copia local {local.name} ignorada: Supabase no tiene la fila (copia vieja)")
            try:
                local.unlink()
            except Exception:
                pass
        return None

    if local and local.exists():
        try:
            with open(local, encoding="utf-8") as f:
                transcript = json.load(f)
            print(f"♻️ Transcript {source} desde cache local (Supabase no disponible): {local.name}")
            return _accept_cached(transcript, "local")
        except Exception as e:
            print(f"⚠️ Cache local ilegible (non-fatal): {e}")

    return None


def save_transcript(
    video_id: str,
    transcript: dict,
    language: str = None,
    duration_seconds: float = None,
    source: str | None = None,
    model: str | None = None,
) -> bool:
    """
    Save a transcript to cache for future reuse.
    Returns True if saved successfully (en Supabase o en local).

    W18: el transcript se guarda con su huella (`fingerprint`), igual en
    Supabase que en la copia local.
    """
    key = transcript_cache_key(video_id, source, model)
    saved = False
    transcript = {**transcript, "fingerprint": transcript_fingerprint(transcript)}

    supabase = get_supabase()
    if supabase:
        try:
            supabase.table("transcription_cache").upsert({
                "video_id": key,
                "transcript": json.dumps(transcript, ensure_ascii=False),
                "language": language,
                "duration_seconds": duration_seconds,
            }).execute()
            saved = True
        except Exception as e:
            print(f"⚠️ Cache save failed (non-fatal): {e}")

    local = _local_path(video_id, source, model)
    if local:
        try:
            local.parent.mkdir(parents=True, exist_ok=True)
            with open(local, "w", encoding="utf-8") as f:
                json.dump(transcript, f, ensure_ascii=False)
            saved = True
        except Exception as e:
            print(f"⚠️ Cache local save failed (non-fatal): {e}")

    return saved
