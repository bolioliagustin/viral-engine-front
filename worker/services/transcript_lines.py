"""
Transcript por palabra → Líneas (oraciones) con tiempos, silencios y wpm.

W4 (docs/PLAN_CALIDAD.md §4 y §9, causa C1 de §1.3): la Pasada A elige
momentos sobre bloques de captions de 3–30 s sin puntuación, así que no puede
proponer límites a nivel de oración. Opus Clip trabaja con un transcript por
palabra con puntuación, mayúsculas y tokens de silencio con duración
(docs/ANALISIS_OPUS_CLIP.md §2.1); este módulo produce ese transcript.

Todo lo de acá es puro (sin red ni disco) salvo la sección "Respaldo" del
final, que llama al modelo barato sobre los tramos que Whisper dejó sin
puntuar. Entrada: el resultado crudo de Whisper (`words[]` y `segments[]`
con `text` puntuado; Groq trae las palabras ya puntuadas, OpenAI peladas).
Salida:

- `words`: palabras con puntuación y mayúsculas (las que faltaban se pegan
  desde el texto de los segmentos), más tokens `__silence` (start/end) en los
  huecos ≥ 0,3 s. Los consumidores actuales de palabras (subtítulos, anclas
  de W1, guardas de W3) deben pasar por `words_without_silence()`.
- `lines`: Líneas (oraciones) con start/end y texto. Una Línea termina en
  . ? ! … o en una pausa ≥ 1,5 s (opcionalmente, en un cambio de segmento de
  Whisper que coincide con un fin de cláusula: ver `build_lines`).
- `wpm`: palabras por minuto sobre la duración total del audio.

Vocabulario (CONTEXT.md): Transcript, Línea, Pasada A.
"""
from __future__ import annotations

import os
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Dict, List, Optional

SILENCE_TOKEN = "__silence"
SILENCE_MIN_GAP_SEC = 0.3     # hueco mínimo entre palabras para marcar silencio (Opus: desde 0,07 s; nosotros 0,3)
LINE_PAUSE_SEC = 1.5          # pausa que cierra una Línea aunque no haya puntuación (ver build_lines)
LINE_SOFT_MAX_WORDS = 60      # a partir de acá, una coma también cierra la Línea
LINE_HARD_MAX_WORDS = 120     # sin puntuación ni pausa: corte forzado
SENTENCE_END_CHARS = ".?!…"

_CLOSING_CHARS = "\"'»)]}”’"
_LEADING_PUNCT = "¿¡\"'«([{“‘—–-"
_TRAILING_PUNCT = ".,;:!?…\"'»)]}”’—–-"

FULL_TRANSCRIPT_SOURCES = ("whisper_full", "hybrid")


# ── Tokens de silencio ──────────────────────────────────────────────────────
def is_silence(word: dict | None) -> bool:
    """True si el token es un `__silence` (no una palabra dicha)."""
    return bool(word) and word.get("word") == SILENCE_TOKEN


def words_without_silence(words: List[dict] | None) -> List[dict]:
    """Palabras dichas, sin los tokens `__silence`. Es lo que deben consumir
    subtítulos, anclas (W1) y guardas (W3): nada cambia para ellos."""
    return [w for w in (words or []) if not is_silence(w)]


def insert_silence_tokens(words: List[dict], min_gap: float = SILENCE_MIN_GAP_SEC) -> List[dict]:
    """
    Inserta un token `__silence` con start/end entre dos palabras consecutivas
    cuyo hueco es ≥ `min_gap`. Idempotente: descarta silencios previos y los
    recalcula. Las palabras se devuelven en el mismo orden (deben venir
    ordenadas por tiempo).
    """
    out: List[dict] = []
    prev: dict | None = None
    for w in words_without_silence(words):
        if prev is not None:
            prev_end = float(prev.get("end", 0))
            w_start = float(w.get("start", 0))
            gap = w_start - prev_end
            if gap >= min_gap:
                out.append({
                    "word": SILENCE_TOKEN,
                    "start": round(prev_end, 3),
                    "end": round(w_start, 3),
                })
        out.append(w)
        prev = w
    return out


def silence_stats(words: List[dict]) -> dict:
    """Cantidad y duración total/mediana de los tokens de silencio."""
    durs = sorted(
        float(w.get("end", 0)) - float(w.get("start", 0))
        for w in (words or []) if is_silence(w)
    )
    if not durs:
        return {"count": 0, "total_sec": 0.0, "median_sec": 0.0}
    mid = len(durs) // 2
    median = durs[mid] if len(durs) % 2 else (durs[mid - 1] + durs[mid]) / 2
    return {
        "count": len(durs),
        "total_sec": round(sum(durs), 3),
        "median_sec": round(median, 3),
    }


# ── Alineación de puntuación sobre palabras ─────────────────────────────────
def _norm(text: str | None) -> str:
    """Forma comparable de una palabra: sin acentos, minúsculas, solo alfanumérico."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", text)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"[^0-9a-z]+", "", t.lower())


def _split_token(token: str) -> tuple[str, str, str]:
    """'¿Sarampión?' → ('¿', 'Sarampión', '?')."""
    i, j = 0, len(token)
    while i < j and token[i] in _LEADING_PUNCT:
        i += 1
    while j > i and token[j - 1] in _TRAILING_PUNCT:
        j -= 1
    return token[:i], token[i:j], token[j:]


def _tokens_from_text(text: str, segment_index: int | None = None) -> List[tuple]:
    """
    Tokeniza texto puntuado en (segment_index, token). Un token que es solo
    puntuación ("...", "—") se pega como cola del token anterior.
    """
    tokens: List[tuple] = []
    for tok in (text or "").split():
        _, core, _ = _split_token(tok)
        if not core:
            if tokens:
                si, prev = tokens[-1]
                tokens[-1] = (si, prev + tok)
            continue
        tokens.append((segment_index, tok))
    return tokens


def _merge_word_and_token(word_text: str, token: str) -> str:
    """
    Combina la palabra de `words[]` con el token de `segments[].text` que le
    corresponde (misma palabra normalizada). La palabra manda: Groq devuelve
    `words[]` ya con puntuación y mayúsculas y su `text` a veces viene
    degradado (sin acentos, sin puntuación); OpenAI devuelve palabras peladas
    y `text` puntuado. Del token se toma solo lo que a la palabra le falta:
    signos iniciales/finales y la mayúscula (si no cambia la longitud, o sea
    sin acentos perdidos).
    """
    wl, wc, wt = _split_token(word_text)
    tl_, tc, tt = _split_token(token)
    core = wc
    if wc and tc and wc.islower() and not tc.islower() and len(tc) == len(wc):
        core = tc
    return (wl or tl_) + core + (wt or tt)


def align_punctuated_tokens(words: List[dict], tokens: List[tuple]) -> List[dict]:
    """
    Alinea una secuencia de tokens puntuados ((segment_index, token)) sobre
    `words` (misma secuencia de palabras). Devuelve copias de las palabras
    con la puntuación y mayúsculas que les faltaban tomadas del token
    (`_merge_word_and_token`) y `segment` = índice del segmento del que salió.

    Usa SequenceMatcher sobre la forma normalizada: tolera palabras que
    Whisper escribió distinto en `words` y en `segments[].text` (números,
    guiones, acentos perdidos) sin desalinear el resto. En un bloque
    `replace` la palabra se conserva tal cual (el token no es fiable) y solo
    se le asigna el segmento.
    """
    out = [dict(w) for w in (words or [])]
    if not out or not tokens:
        return out

    src = [_norm(w.get("word")) for w in out]
    dst = [_norm(_split_token(tok)[1]) for _, tok in tokens]
    sm = SequenceMatcher(None, src, dst, autojunk=False)

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                si, tok = tokens[j1 + k]
                out[i1 + k]["word"] = _merge_word_and_token(out[i1 + k].get("word") or "", tok)
                if si is not None:
                    out[i1 + k]["segment"] = si
        elif tag == "replace" and (i2 - i1) == (j2 - j1):
            for k in range(i2 - i1):
                si, _tok = tokens[j1 + k]
                if si is not None:
                    out[i1 + k]["segment"] = si

    # Palabras sin match heredan el segmento de la anterior (o de la siguiente)
    last_seg = None
    for w in out:
        if "segment" in w:
            last_seg = w["segment"]
        elif last_seg is not None:
            w["segment"] = last_seg
    next_seg = None
    for w in reversed(out):
        if w.get("segment") is not None:
            next_seg = w["segment"]
        elif next_seg is not None:
            w["segment"] = next_seg
    return out


def align_punctuation(words: List[dict], segments: List[dict]) -> List[dict]:
    """
    Pega la puntuación y las mayúsculas de `segments[].text` sobre `words[]`
    (Groq/OpenAI devuelven el texto puntuado por segmento pero las palabras
    sin puntuación). La puntuación queda en la última palabra de cada oración.
    """
    tokens: List[tuple] = []
    for si, seg in enumerate(segments or []):
        tokens.extend(_tokens_from_text(seg.get("text") or "", si))
    return align_punctuated_tokens(words, tokens)


def punctuation_rate(text: str) -> float:
    """Signos de fin de oración por palabra (heurística para detectar un
    proveedor que no puntúa: < 1 cada 80 palabras)."""
    n_words = len((text or "").split())
    if not n_words:
        return 0.0
    n_marks = sum((text or "").count(ch) for ch in SENTENCE_END_CHARS)
    return n_marks / n_words


def has_punctuation(segments: List[dict], min_rate: float = 1 / 80) -> bool:
    text = " ".join((sg.get("text") or "") for sg in (segments or []))
    return punctuation_rate(text) >= min_rate


# ── Líneas (oraciones con tiempos) ──────────────────────────────────────────
def ends_sentence(word_text: str | None) -> bool:
    """¿La palabra cierra una oración? (. ? ! …, tolerando comillas/paréntesis de cierre)."""
    t = (word_text or "").rstrip(_CLOSING_CHARS)
    return bool(t) and t[-1] in SENTENCE_END_CHARS


def _ends_clause(word_text: str | None) -> bool:
    t = (word_text or "").rstrip(_CLOSING_CHARS)
    return bool(t) and t[-1] in ",;:"


def build_lines(
    words: List[dict],
    pause_sec: float = LINE_PAUSE_SEC,
    soft_max_words: int = LINE_SOFT_MAX_WORDS,
    hard_max_words: int = LINE_HARD_MAX_WORDS,
    split_on_segment_change: bool = False,
) -> List[dict]:
    """
    Agrupa las palabras (sin silencios) en Líneas: cada una con `id`, `start`,
    `end`, `text` y `n_words`, compatible con el shape de `segments` que ya
    consumen validation.py y main.py.

    Una Línea termina cuando la palabra cierra oración (. ? ! …) o cuando la
    pausa hasta la siguiente palabra es ≥ `pause_sec`. Con
    `split_on_segment_change` también cierra al cambiar el segmento de Whisper
    si la palabra terminaba una cláusula (, ; :). Como red: a partir de
    `soft_max_words` una coma también cierra, y en `hard_max_words` se corta.

    Umbrales medidos en podcast_general_01 (Groq, sin prompt; % de Líneas que
    terminan en . ? ! … / % que arrancan en mayúscula): pausa 0,8 s → 88 % /
    85 % (parte oraciones por la mitad: en conversación hay pausas de 1 s
    dentro de una frase); 1,2 s → 96 % / 91 %; 1,5 s → 98 % / 93 %; sin
    pausa → 99 % / 94 %. Se queda en 1,5 s como red para tramos que ni
    Whisper ni el respaldo puntuaron. Cortar por cambio de segmento restaba
    3–4 puntos (los segmentos de Whisper cortan oraciones por la mitad), por
    eso está apagado.
    """
    ws = words_without_silence(words)
    lines: List[dict] = []
    current: List[dict] = []

    def _flush():
        if not current:
            return
        lines.append({
            "id": len(lines),
            "start": round(float(current[0].get("start", 0)), 3),
            "end": round(float(current[-1].get("end", 0)), 3),
            "text": " ".join((w.get("word") or "").strip() for w in current).strip(),
            "n_words": len(current),
        })
        current.clear()

    for i, w in enumerate(ws):
        current.append(w)
        nxt = ws[i + 1] if i + 1 < len(ws) else None
        text = w.get("word") or ""

        close = False
        if nxt is None or ends_sentence(text):
            close = True
        else:
            gap = float(nxt.get("start", 0)) - float(w.get("end", 0))
            if gap >= pause_sec:
                close = True
            elif (
                split_on_segment_change
                and _ends_clause(text)
                and w.get("segment") is not None
                and nxt.get("segment") is not None
                and w["segment"] != nxt["segment"]
            ):
                close = True
            elif len(current) >= soft_max_words and _ends_clause(text):
                close = True
            elif len(current) >= hard_max_words:
                close = True
        if close:
            _flush()

    _flush()
    return lines


def lines_punctuation_rate(lines: List[dict]) -> float:
    """% de Líneas que terminan en . ? ! … (métrica de aceptación de W4)."""
    if not lines:
        return 0.0
    ok = sum(1 for ln in lines if ends_sentence(ln.get("text")))
    return ok / len(lines)


def words_per_minute(words: List[dict], duration_sec: float | None = None) -> float:
    """Palabras dichas por minuto sobre la duración total (como Opus: 190 en
    podcast_general_01). Sin duración usa el rango de las palabras."""
    ws = words_without_silence(words)
    if not ws:
        return 0.0
    if not duration_sec or duration_sec <= 0:
        duration_sec = float(ws[-1].get("end", 0)) - float(ws[0].get("start", 0))
    if duration_sec <= 0:
        return 0.0
    return round(len(ws) / (duration_sec / 60.0), 1)


# ── Transcript completo y formato para la Pasada A ──────────────────────────
def build_full_transcript(
    raw: Dict,
    *,
    source: str,
    model: str,
    provider: str | None = None,
    duration_sec: float | None = None,
    split_on_segment_change: bool = False,
    align: bool = True,
    punctuate_runs=None,
) -> Dict:
    """
    Arma el Transcript de alta resolución a partir del resultado crudo de
    Whisper ya unido (`words[]` + `segments[]` en la línea de tiempo del
    video). `segments` del resultado son las Líneas, para que todo lo que hoy
    consume `transcript["segments"]` (clasificador, validate_durations,
    validate_against_transcript, prompt de contexto de Whisper) vea oraciones
    enteras sin cambiar de firma. `align=False` si las palabras ya traen la
    puntuación pegada. `punctuate_runs` (opcional, `words -> words`) corre
    después de alinear y antes de armar Líneas: es el respaldo para los tramos
    que Whisper dejó sin puntuar (`punctuate_unpunctuated_runs`).
    """
    # Se respeta el orden en que vino la secuencia de palabras: los tiempos
    # por palabra de Groq tienen jitter en los bordes de segmento y ordenar
    # por `start` reordenaba palabras ("Star universo Wars").
    words = [w for w in (raw.get("words") or []) if (w.get("word") or "").strip()]
    words = words_without_silence(words)
    segments = raw.get("segments") or []

    if align and segments:
        words = align_punctuation(words, segments)
    if punctuate_runs is not None:
        words = punctuate_runs(words)

    lines = build_lines(words, split_on_segment_change=split_on_segment_change)
    duration = float(duration_sec or raw.get("duration") or (lines[-1]["end"] if lines else 0))
    words = insert_silence_tokens(words)

    return {
        "text": " ".join(ln["text"] for ln in lines),
        "segments": [dict(ln) for ln in lines],
        "lines": lines,
        "words": words,
        "language": raw.get("language"),
        "duration": round(duration, 3),
        "wpm": words_per_minute(words, duration),
        "source": source,
        "model": model,
        "provider": provider,
    }


def has_full_transcript(transcript: Dict | None) -> bool:
    """¿El transcript trae Líneas de W4 (fuente whisper_full o hybrid)?"""
    if not transcript:
        return False
    return (
        transcript.get("source") in FULL_TRANSCRIPT_SOURCES
        and bool(transcript.get("lines"))
    )


def format_mmss(seconds: float) -> str:
    """1030.4 → '17:10'. Los minutos pueden pasar de 59 (video > 1 h): '77:03'."""
    total = int(round(max(0.0, float(seconds))))
    return f"{total // 60}:{total % 60:02d}"


def format_lines_for_prompt(lines: List[dict]) -> str:
    """
    Formato con que la Pasada A recibe el Transcript de W4: una Línea por
    renglón, `[mm:ss] Oración.` (el mm:ss es el inicio de la Línea). Reemplaza
    a los bloques `[s-e]: texto` de captions; el resto del prompt no cambia.
    """
    out = []
    for ln in lines or []:
        text = (ln.get("text") or "").strip()
        if not text:
            continue
        out.append(f"[{format_mmss(ln.get('start', 0))}] {text}")
    return "\n".join(out)


# ── Respaldo: puntuación con el modelo barato ───────────────────────────────
UNPUNCTUATED_RUN_MIN_WORDS = 40   # tramo sin . ? ! … que se manda a puntuar


def unpunctuated_runs(words: List[dict], min_run_words: int = UNPUNCTUATED_RUN_MIN_WORDS) -> List[tuple]:
    """
    Tramos [i, j) de palabras consecutivas sin ningún fin de oración de al
    menos `min_run_words` palabras: es donde Whisper entró en su modo
    degradado (sin puntuación, minúsculas). Un proveedor que no puntúa nada
    es el caso extremo: un solo tramo con todo el transcript.
    """
    runs: List[tuple] = []
    start = 0
    ws = words_without_silence(words)
    for i, w in enumerate(ws):
        if ends_sentence(w.get("word")):
            if i + 1 - start >= min_run_words:
                runs.append((start, i + 1))
            start = i + 1
    if len(ws) - start >= min_run_words:
        runs.append((start, len(ws)))
    return runs


_PUNCTUATE_SYSTEM = (
    "Sos un corrector de transcripciones. Recibís texto sin puntuación y "
    "devolvés EXACTAMENTE las mismas palabras, en el mismo orden, agregando "
    "solo puntuación (. , ; : ? ! ¿ ¡) y mayúsculas al inicio de cada oración "
    "y en nombres propios. No agregues, quites ni cambies palabras. No "
    "resumas. Respondé solo con el texto corregido."
)


def punctuate_words_with_llm(
    words: List[dict],
    *,
    language: str | None = None,
    client=None,
    model: str | None = None,
    batch_words: int = 400,
) -> List[dict]:
    """
    Respaldo cuando el proveedor de Whisper no trajo puntuación: manda las
    palabras por tramos de `batch_words` al modelo barato (el del
    clasificador), y alinea el texto puntuado sobre las palabras con el mismo
    alineador que usa `align_punctuation`. Si el modelo falla en un tramo, ese
    tramo queda sin puntuar (no fatal).
    """
    ws = words_without_silence(words)
    if not ws:
        return []
    if client is None:
        from openai import OpenAI
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )
    if model is None:
        from config.model_tiers import get_model
        model = get_model("classifier")

    from config.llm_chat import log_llm_usage

    out: List[dict] = []
    for b in range(0, len(ws), batch_words):
        batch = ws[b:b + batch_words]
        text = " ".join((w.get("word") or "").strip() for w in batch)
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _PUNCTUATE_SYSTEM},
                    {"role": "user", "content": f"Idioma: {language or 'el del texto'}.\n\n{text}"},
                ],
                temperature=0.0,
                max_tokens=int(len(text) * 0.6) + 200,
                timeout=60,
            )
            log_llm_usage("punctuate", model, response)
            punctuated = (response.choices[0].message.content or "").strip()
            if not punctuated:
                raise ValueError("respuesta vacía")
            out.extend(align_punctuated_tokens(batch, _tokens_from_text(punctuated, None)))
        except Exception as e:
            print(f"   ⚠️ Puntuación de respaldo falló en el tramo {b // batch_words + 1} ({str(e)[:80]}); queda sin puntuar")
            out.extend(dict(w) for w in batch)
    return out


def punctuate_unpunctuated_runs(
    words: List[dict],
    *,
    min_run_words: int = UNPUNCTUATED_RUN_MIN_WORDS,
    language: str | None = None,
    client=None,
    model: str | None = None,
    max_parallel: int = 4,
) -> List[dict]:
    """
    Respaldo por tramo: puntúa con el modelo barato solo los tramos de
    ≥ `min_run_words` palabras sin fin de oración (`unpunctuated_runs`) y
    deja el resto intacto. Devuelve la lista completa (sin silencios).
    """
    ws = words_without_silence(words)
    runs = unpunctuated_runs(ws, min_run_words)
    if not runs:
        return ws
    n_words = sum(j - i for i, j in runs)
    print(f"   ✍️ Puntuación de respaldo: {len(runs)} tramos sin puntuar ({n_words} palabras)")
    if client is None:
        from openai import OpenAI
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )
    if model is None:
        from config.model_tiers import get_model
        model = get_model("classifier")

    # Los tramos son independientes: en paralelo (cada llamada tarda ~5 s).
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(max_parallel, len(runs))) as pool:
        punctuated = list(pool.map(
            lambda r: punctuate_words_with_llm(ws[r[0]:r[1]], language=language, client=client, model=model),
            runs,
        ))
    out = list(ws)
    for (i, j), new_words in zip(runs, punctuated):
        out[i:j] = new_words
    return out
