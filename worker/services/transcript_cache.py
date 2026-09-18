"""
S3: Transcription Cache Service
Caches video transcriptions in Supabase to avoid re-transcribing already processed videos.
Saves ~$0.006/min on Whisper API costs per cache hit.

W4: la cache se lee además de escribirse, y guarda el Transcript completo por
(video_id, fuente, modelo). La tabla `transcription_cache` tiene PK `video_id`
(text), así que sin migración la clave compuesta va en esa columna:
  - captions de YouTube (Supadata): `video_id` a secas (igual que siempre)
  - Whisper del audio completo:     `video_id:whisper_full:whisper-large-v3-turbo`
Además queda una copia local en `downloads/` para que la segunda corrida no
llame a Whisper aunque Supabase no esté disponible.
"""
import json
import os
from pathlib import Path

from services.supabase_client import get_supabase

_LOCAL_DIR = Path(__file__).parent.parent / "downloads"


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


def get_cached_transcript(video_id: str, source: str | None = None, model: str | None = None) -> dict | None:
    """
    Check if a transcript exists in cache for the given video_id.
    Returns the cached transcript dict or None if not found.

    `source`/`model` (W4) buscan el Transcript completo de esa fuente; sin
    ellos, el comportamiento es el de siempre (captions por `video_id`).
    """
    key = transcript_cache_key(video_id, source, model)
    supabase = get_supabase()
    if supabase:
        try:
            result = supabase.table("transcription_cache") \
                .select("transcript, language, duration_seconds") \
                .eq("video_id", key) \
                .limit(1) \
                .execute()

            if result.data and len(result.data) > 0:
                cached = result.data[0]
                transcript = cached["transcript"]
                # transcript is stored as JSON string
                if isinstance(transcript, str):
                    transcript = json.loads(transcript)
                return transcript
        except Exception as e:
            print(f"⚠️ Cache lookup failed (non-fatal): {e}")

    local = _local_path(video_id, source, model)
    if local and local.exists():
        try:
            with open(local, encoding="utf-8") as f:
                transcript = json.load(f)
            print(f"♻️ Transcript {source} desde cache local: {local.name}")
            return transcript
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
    """
    key = transcript_cache_key(video_id, source, model)
    saved = False

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
