"""
Video validation utilities
"""
import re
import unicodedata
from typing import Optional

# Duración de un Momento (docs/PLAN_CALIDAD.md §8-9, análisis Opus Clip
# 18-sep-2026, `docs/ANALISIS_OPUS_CLIP.md`): 7 de los 10 mejores clips de
# Opus sobre nuestro propio golden set (podcast_general_01) duran más de
# 60 s — su #1 dura 96 s y empieza donde el tope viejo nos obligaba a
# cortar. Menos de 15 s no alcanza para plantear una idea.
CLIP_MIN_DURATION_SEC = 15.0
CLIP_MAX_DURATION_SEC = 120.0


def validate_video_duration(duration_seconds: int, max_duration: int = 7200) -> None:
    """
    Validate video duration is within allowed limits.

    Args:
        duration_seconds: Video duration in seconds
        max_duration: Maximum allowed duration (default 2 hours)

    Raises:
        ValueError: If video exceeds max duration
    """
    if duration_seconds > max_duration:
        hours = max_duration / 3600
        raise ValueError(
            f"Video demasiado largo (máximo {hours:.1f} horas, "
            f"tienes {duration_seconds/3600:.1f} horas)"
        )


def _moment_overlap_ratio(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """Fraction of the shorter clip covered by temporal overlap."""
    overlap = max(0.0, min(a_end, b_end) - max(a_start, b_start))
    shorter = min(a_end - a_start, b_end - b_start)
    if shorter <= 0:
        return 0.0
    return overlap / shorter


def filter_overlapping_moments(
    viral_moments: list,
    max_overlap_ratio: float = 0.5,
) -> list:
    """
    Drop moments that overlap >50% (by time) with an already-kept moment.
    Preserves original order; first occurrence wins.
    """
    kept = []
    for i, moment in enumerate(viral_moments):
        start = getattr(moment, 'start_time', None)
        end = getattr(moment, 'end_time', None)
        if start is None or end is None:
            kept.append(moment)
            continue

        a_start, a_end = float(start), float(end)
        rejected = False
        for prev in kept:
            b_start = float(prev.start_time)
            b_end = float(prev.end_time)
            ratio = _moment_overlap_ratio(a_start, a_end, b_start, b_end)
            if ratio > max_overlap_ratio:
                hook = getattr(moment, 'hook', 'Unknown')[:30]
                print(
                    f"⚠️ Skipping moment {i+1} '{hook}' "
                    f"(>{max_overlap_ratio*100:.0f}% overlap with earlier moment)"
                )
                rejected = True
                break
        if not rejected:
            kept.append(moment)
    return kept


def _normalize_phrase(text: str) -> str:
    """Lowercase alphanumeric phrase for fuzzy comparison."""
    return re.sub(r'[^\w\s]', '', (text or '').lower()).strip()


def _subsequence_word_match(needle_words: list[str], haystack_words: list[str]) -> bool:
    """True si needle_words aparecen en orden (no necesariamente contiguos) en haystack."""
    if not needle_words:
        return True
    if not haystack_words:
        return False
    j = 0
    for hw in haystack_words:
        if j < len(needle_words) and (
            hw == needle_words[j]
            or needle_words[j] in hw
            or hw in needle_words[j]
        ):
            j += 1
    return j >= len(needle_words)


def phrase_anchor_in_clip(
    claim: str,
    clip_text: str,
    *,
    min_words: int = 3,
    window: str = "full",
) -> bool:
    """
    Verifica que las palabras clave del claim aparecen en orden dentro del clip.

    Más tolerante que validate_against_transcript (que exige match al inicio/fin
  exacto). Útil en golden set: Gemini suele citar frases reales pero desplazadas.
    """
    claim_words = _normalize_phrase(claim).split()
    clip_words = _normalize_phrase(clip_text).split()
    if not claim_words or not clip_words:
        return True
    n = min(min_words, len(claim_words))
    if window == "start":
        needle = claim_words[:n]
        haystack = clip_words[: max(n * 4, 20)]
    elif window == "end":
        needle = claim_words[-n:]
        haystack = clip_words[-max(n * 4, 20) :]
    else:
        needle = claim_words[:n]
        haystack = clip_words
    return _subsequence_word_match(needle, haystack)


def evaluate_moment_phrase_metrics(moment, transcript: dict) -> dict:
    """
    Métricas de verificación de frases para eval/golden set.

    Returns:
        strict_pass, anchor_pass, last_phrase_punct
    """
    verification = getattr(moment, "verification", None)
    start_s = getattr(moment, "start_time", None)
    end_s = getattr(moment, "end_time", None)
    if not verification or start_s is None or end_s is None:
        return {
            "strict_pass": True,
            "anchor_pass": True,
            "last_phrase_punct": True,
            "has_verification": False,
        }

    segments = transcript.get("segments") or []
    clip_text = _words_in_range(segments, float(start_s), float(end_s))
    last = getattr(verification, "last_phrase_in_audio", "") or ""

    first_claim = getattr(verification, "first_phrase_in_audio", "") or ""
    last_claim = last
    anchor_ok = True
    if first_claim.strip():
        anchor_ok = phrase_anchor_in_clip(first_claim, clip_text, window="full")
    if anchor_ok and last_claim.strip():
        anchor_ok = phrase_anchor_in_clip(last_claim, clip_text, window="full")

    return {
        "strict_pass": validate_against_transcript(moment, transcript),
        "anchor_pass": anchor_ok,
        "last_phrase_punct": (
            not last.strip() or last.strip()[-1] in ".?!…"
        ),
        "has_verification": True,
    }


def _words_in_range(segments: list, start_s: float, end_s: float) -> str:
    """Concatenate transcript text within [start_s, end_s]."""
    parts = []
    for sg in segments:
        sg_start = float(sg.get("start", 0))
        sg_end = float(sg.get("end", sg_start))
        if sg_end < start_s or sg_start > end_s:
            continue
        txt = (sg.get("text") or "").strip()
        if txt:
            parts.append(txt)
    return " ".join(parts)


def validate_against_transcript(moment, transcript: dict) -> bool:
    """
    Verify first_phrase_in_audio / last_phrase_in_audio against YT transcript.

    Logs warnings on mismatch; returns True if verification passes or is absent.
    """
    verification = getattr(moment, 'verification', None)
    if not verification:
        return True

    start_s = getattr(moment, 'start_time', None)
    end_s = getattr(moment, 'end_time', None)
    if start_s is None or end_s is None:
        return True

    segments = transcript.get("segments") or []
    clip_text = _normalize_phrase(_words_in_range(segments, float(start_s), float(end_s)))
    if not clip_text:
        return True

    clip_words = clip_text.split()
    ok = True
    hook = getattr(moment, 'hook', 'Unknown')[:30]

    first_claim = _normalize_phrase(getattr(verification, 'first_phrase_in_audio', '') or '')
    if first_claim:
        claim_words = first_claim.split()[:8]
        actual_first = " ".join(clip_words[:len(claim_words)])
        if claim_words and actual_first and claim_words[0] not in actual_first:
            print(
                f"⚠️ [{hook}] first_phrase mismatch: "
                f"claimed '{first_claim[:50]}' vs transcript '{actual_first[:50]}'"
            )
            ok = False

    last_claim = _normalize_phrase(getattr(verification, 'last_phrase_in_audio', '') or '')
    if last_claim and len(clip_words) >= 3:
        claim_words = last_claim.split()[-8:]
        actual_last = " ".join(clip_words[-len(claim_words):])
        if claim_words and actual_last and claim_words[-1] not in actual_last:
            print(
                f"⚠️ [{hook}] last_phrase mismatch: "
                f"claimed '{last_claim[-50:]}' vs transcript '{actual_last[-50:]}'"
            )
            ok = False

    return ok


def verify_phrases_against_whisper(moment, words: list[dict]) -> dict:
    """
    Post-Whisper check: compare verification claims to actual clip words.

    Fase 4: devuelve dict {"first_ok": bool, "last_ok": bool, "failed": bool}.
    `failed` es True solo cuando AMBAS frases (first Y last) no matchean —
    señal fuerte de que el corte no corresponde al momento elegido.
    El dict es truthy siempre; usar las keys, no el valor booleano.
    """
    result = {"first_ok": True, "last_ok": True, "failed": False}
    verification = getattr(moment, 'verification', None)
    if not verification or not words:
        return result

    whisper_text = " ".join((w.get("word") or "").strip() for w in words)
    norm = _normalize_phrase(whisper_text)
    if not norm:
        return result

    wlist = norm.split()
    hook = getattr(moment, 'hook', 'Unknown')[:30]

    first_claim = _normalize_phrase(getattr(verification, 'first_phrase_in_audio', '') or '')
    if first_claim:
        cw = first_claim.split()[:5]
        actual = " ".join(wlist[:len(cw)])
        if cw and cw[0] not in actual:
            print(f"⚠️ [{hook}] Whisper first_phrase mismatch: '{first_claim[:40]}' vs '{actual[:40]}'")
            result["first_ok"] = False

    last_claim = _normalize_phrase(getattr(verification, 'last_phrase_in_audio', '') or '')
    if last_claim and len(wlist) >= 3:
        cw = last_claim.split()[-5:]
        actual = " ".join(wlist[-len(cw):])
        if cw and cw[-1] not in actual:
            print(f"⚠️ [{hook}] Whisper last_phrase mismatch: '{last_claim[-40:]}' vs '{actual[-40:]}'")
            result["last_ok"] = False

    result["failed"] = (
        not result["first_ok"]
        or not result["last_ok"]
    )
    return result


def phrases_from_whisper_words(words: list[dict], n_words: int = 8) -> tuple[str, str]:
    """Deriva first/last phrase desde palabras Whisper post-snap."""
    tokens = [(w.get("word") or "").strip() for w in (words or [])]
    tokens = [t for t in tokens if t]
    if not tokens:
        return "", ""
    first = " ".join(tokens[:n_words])
    last = " ".join(tokens[-n_words:])
    return first, last


def sync_verification_phrases_from_words(moment, words: list[dict]) -> None:
    """Actualiza verification phrases del momento con el audio real post-trim."""
    verification = getattr(moment, "verification", None)
    if not verification or not words:
        return
    first, last = phrases_from_whisper_words(words)
    if first:
        verification.first_phrase_in_audio = first
    if last:
        verification.last_phrase_in_audio = last


def verify_phrases_after_snap(
    moment,
    words: list[dict],
    snap_trim_start: float,
    clip_duration: float,
    significant_snap_threshold: float = 0.5,
) -> dict:
    """
    Verifica frases post-snap: actualiza claims desde Whisper y compara.
    Si no hubo snap significativo, usa las frases originales del análisis.
    """
    significant_snap = snap_trim_start >= significant_snap_threshold
    if significant_snap and words:
        sync_verification_phrases_from_words(moment, words)
    return verify_phrases_against_whisper(moment, words)


def _fuzzy_window_match(target_words: list[str], norm_words: list[tuple], max_words: int = 8) -> Optional[float]:
    """Busca target_words en norm_words; devuelve timestamp del match o None."""
    if not target_words or not norm_words:
        return None
    target = target_words[:max_words]
    n = len(target)
    if n < 2:
        return None
    for i in range(len(norm_words) - n + 1):
        window = [norm_words[i + j][0] for j in range(n)]
        matches = sum(1 for a, b in zip(target, window) if a and a == b)
        if matches >= max(2, int(round(n * 0.6))):
            return norm_words[i][1]
    return None


def find_hook_start_in_words(
    words: list[dict],
    hook: str = "",
    overlay: str = "",
    first_phrase: str = "",
    clip_duration: float = 0.0,
    search_ratio: float = 0.4,
) -> Optional[float]:
    """
    Busca el inicio del hook en las primeras search_ratio del clip.
    Prioridad: overlay > hook > first_phrase.
    """
    if not words:
        return None
    limit = clip_duration * search_ratio if clip_duration > 0 else float(words[-1].get("end", 60)) * search_ratio
    norm_words = [
        (_normalize_phrase(w.get("word") or ""), float(w.get("start", 0)))
        for w in words
        if float(w.get("start", 0)) <= limit
    ]
    for phrase in (overlay, hook, first_phrase):
        if not phrase or len(phrase.strip()) < 4:
            continue
        tokens = _normalize_phrase(phrase).split()
        # Overlay suele ser corto (2-4 palabras)
        mw = min(6, max(2, len(tokens)))
        t = _fuzzy_window_match(tokens, norm_words, max_words=mw)
        if t is not None:
            return t
    return None


def hook_keyword_overlap(head_words: list[str], hook: str) -> float:
    """Fracción de palabras del head que aparecen en el hook (0-1)."""
    if not head_words or not hook:
        return 0.0
    hook_set = set(_normalize_phrase(hook).split())
    head_set = set(_normalize_phrase(" ".join(head_words)).split())
    head_set -= {"", "el", "la", "los", "las", "de", "en", "y", "a", "que", "es", "un", "una"}
    if not head_set:
        return 1.0
    return len(head_set & hook_set) / len(head_set)


# ── Plausibilidad de la Transcripción del clip (guardas W3, PLAN_CALIDAD §1.3 C3) ──
# Umbrales calibrados con dos casos reales:
#  (a) job 1b1007c4 m=1: 73 palabras en 33 s (densidad 2.21, normal) pero
#      amontonadas en los últimos ~9 s → densidad efectiva ~8 w/s. Timestamps
#      corridos de whisper-1: el texto es bueno, los tiempos no.
#  (b) job 4bb4561b m=5: "O R m Y TleK E" en 27 s (densidad 0.23), y clips con
#      "en realidad los modelos están bastante bien en realidad los…" repetido:
#      el segmento descargado no tiene habla (audio desincronizado o alucinación).
# El habla real en español sostiene 2–4 palabras/s; >5 sostenidas es imposible.
WHISPER_MAX_EFFECTIVE_DENSITY = 5.0     # palabras/s sobre el tramo con habla
WHISPER_MIN_DENSITY = 1.2               # palabras/s sobre el clip entero
WHISPER_MIN_UNIQUE_RATIO = 0.35         # palabras únicas / total
WHISPER_MAX_REPEATED_RATIO = 0.40       # fracción del texto cubierta por una secuencia repetida
WHISPER_MIN_UNIQUE_WORDS = 8
WHISPER_MIN_SPEECH_SPAN_SEC = 1.0       # piso para no disparar con 2 palabras en 0.3 s
WHISPER_REPEAT_MIN_NGRAM = 4


def longest_repeated_run(tokens: list[str], min_len: int = WHISPER_REPEAT_MIN_NGRAM) -> int:
    """
    Longitud (en palabras) de la secuencia más larga de ≥ min_len palabras que
    aparece verbatim al menos dos veces en `tokens`. 0 si no hay ninguna.

    Detecta el patrón "frase repetida en loop" que deja Whisper cuando el
    audio no tiene habla clara. O(n²) sobre ≤ unos cientos de palabras: barato.
    """
    n = len(tokens)
    if n < min_len * 2:
        return 0
    best = 0
    seen: dict[tuple, int] = {}
    # Buscar el n-grama de tamaño min_len repetido y extender desde ahí.
    for i in range(n - min_len + 1):
        key = tuple(tokens[i:i + min_len])
        if key in seen:
            j = seen[key]
            length = min_len
            while i + length < n and tokens[j + length] == tokens[i + length]:
                length += 1
            best = max(best, length)
        else:
            seen[key] = i
    return best


def assess_whisper_words(words: list[dict], clip_duration: float) -> dict:
    """
    Evalúa si las palabras Whisper de un clip son plausibles como habla real
    con timestamps reales. Devuelve métricas + `plausible` + `reasons`.

    reasons ⊆ {"timestamps_suspect", "bad_segment"}:
      - timestamps_suspect: el texto parece habla pero los tiempos están
        comprimidos (densidad efectiva > 5 w/s). No hay que recortar por
        "silencio" en base a esos tiempos.
      - bad_segment: el segmento no tiene habla plausible (muy pocas palabras,
        texto repetido o casi sin vocabulario). No sirve ni para subtítulos.
    """
    words = words or []
    n = len(words)
    duration = max(float(clip_duration or 0.0), 0.0)
    tokens = [_normalize_phrase(w.get("word") or "") for w in words]
    tokens = [t for t in tokens if t]

    if n == 0 or duration <= 0:
        return {
            "n_words": n,
            "density": 0.0,
            "speech_span": 0.0,
            "leading_gap": 0.0,
            "effective_density": 0.0,
            "unique_ratio": 0.0,
            "unique_words": 0,
            "repeated_run": 0,
            "repeated_ratio": 0.0,
            "timestamps_suspect": False,
            "bad_segment": True,
            "plausible": False,
            "reasons": ["bad_segment"],
        }

    starts = [float(w.get("start", 0.0)) for w in words]
    ends = [float(w.get("end", s)) for w, s in zip(words, starts)]
    first_start = min(starts)
    last_end = max(ends)
    speech_span = max(0.0, last_end - first_start)
    density = n / duration
    effective_density = n / max(speech_span, WHISPER_MIN_SPEECH_SPAN_SEC)
    unique_words = len(set(tokens))
    unique_ratio = (unique_words / len(tokens)) if tokens else 0.0
    repeated_run = longest_repeated_run(tokens)
    repeated_ratio = (repeated_run / len(tokens)) if tokens else 0.0

    reasons: list[str] = []
    timestamps_suspect = effective_density > WHISPER_MAX_EFFECTIVE_DENSITY
    bad_segment = (
        density < WHISPER_MIN_DENSITY
        or unique_ratio < WHISPER_MIN_UNIQUE_RATIO
        or repeated_ratio > WHISPER_MAX_REPEATED_RATIO
        or unique_words < WHISPER_MIN_UNIQUE_WORDS
    )
    if timestamps_suspect:
        reasons.append("timestamps_suspect")
    if bad_segment:
        reasons.append("bad_segment")

    return {
        "n_words": n,
        "density": round(density, 3),
        "speech_span": round(speech_span, 3),
        "leading_gap": round(first_start, 3),
        "effective_density": round(effective_density, 3),
        "unique_ratio": round(unique_ratio, 3),
        "unique_words": unique_words,
        "repeated_run": repeated_run,
        "repeated_ratio": round(repeated_ratio, 3),
        "timestamps_suspect": timestamps_suspect,
        "bad_segment": bad_segment,
        "plausible": not reasons,
        "reasons": reasons,
    }


# ── Cortes anclados a las frases del modelo (W1, PLAN_CALIDAD §4; causas C1/C2) ──
# La Pasada A elige start_time/end_time sobre bloques de captions de 3–30 s,
# pero sus first_phrase_in_audio / last_phrase_in_audio son texto real. Acá
# la verdad son las frases: se localizan en la Transcripción del segmento
# ancho y el clip va de inicio de oración de la primera al fin de oración de
# la última. El número es solo la pista de qué descargar.

_SENTENCE_END_CHARS_V = (".", "?", "!", "…", "。", "؟")
LOCATE_MIN_SCORE = 0.75          # tolera 1 de cada 4 palabras distinta
# W2-C: frases largas (≥6 palabras) tienen más superficie para que UNA
# palabra difiera por una transcripción distinta (Pasada A lee Supadata,
# el matching corre sobre Whisper) sin que la frase deje de ser la misma
# ("hantavirus" vs "antavirus"): tolerar 1 de cada 3 en vez de 1 de cada 4.
# Frases cortas (<6 palabras) se quedan en LOCATE_MIN_SCORE — con pocas
# palabras, relajar el umbral las vuelve ambiguas.
LOCATE_LONG_PHRASE_WORDS = 6
LOCATE_MIN_SCORE_LONG = round(2 / 3, 3)
LOCATE_MAX_WORDS = 12            # ventana máxima de la frase a buscar
_SENTENCE_MAX_LOOKBACK_SEC = 10.0   # oración "infinita" sin puntuación: no retroceder más
_SENTENCE_MAX_LOOKAHEAD_SEC = 10.0


_ES_NUMBER_WORDS = {
    "0": "cero", "1": "uno", "2": "dos", "3": "tres", "4": "cuatro", "5": "cinco",
    "6": "seis", "7": "siete", "8": "ocho", "9": "nueve", "10": "diez", "11": "once",
    "12": "doce", "13": "trece", "14": "catorce", "15": "quince", "16": "dieciseis",
    "17": "diecisiete", "18": "dieciocho", "19": "diecinueve", "20": "veinte",
    "30": "treinta", "40": "cuarenta", "50": "cincuenta", "60": "sesenta",
    "70": "setenta", "80": "ochenta", "90": "noventa", "100": "cien", "1000": "mil",
}


def _fold_token(text: str) -> str:
    """
    Minúsculas, sin acentos ni puntuación (Whisper y Gemini difieren en ambos);
    los números chicos en cifra pasan a palabra ("5" → "cinco": Whisper escribe
    "hack número 5" y el modelo cita "hack número cinco").

    W2-C: la "h" inicial se cae (es muda en español y Whisper a veces la omite
    o la inventa — "hantavirus" transcribe como "antavirus"), así que ambas
    formas foldean igual y matchean.
    """
    norm = _normalize_phrase(text)
    norm = unicodedata.normalize("NFD", norm)
    norm = "".join(ch for ch in norm if unicodedata.category(ch) != "Mn")
    norm = _ES_NUMBER_WORDS.get(norm, norm)
    if len(norm) > 1 and norm[0] == "h":
        norm = norm[1:]
    return norm


_ALNUM_SPLIT_RE = re.compile(r"^([a-z]+)(\d+)$")


def _expand_alnum_token(tok: str) -> list[str]:
    """
    "r0" → ["r", "cero"]: sigla y número pegados sin espacio (común en
    términos técnicos — R0, T1 — que Whisper y la Pasada A pueden tokenizar
    distinto: "R0" en una transcripción, "R cero" en la otra). Sin el patrón,
    devuelve el token tal cual (no-op para el resto de las palabras).
    """
    m = _ALNUM_SPLIT_RE.match(tok)
    if not m:
        return [tok]
    letters, digits = m.groups()
    number_word = _ES_NUMBER_WORDS.get(digits)
    if not number_word:
        return [tok]
    return [letters, number_word]


def _tokens_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    # "modelos" vs "modelo", "claude" vs "claudecode": prefijo compartido largo
    if len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a)):
        return True
    return False


def _window_alignment(target: list[str], window: list[str]) -> tuple[int, int]:
    """
    Alinea `target` contra `window` en orden (greedy, tolera inserciones).
    Devuelve (matches, idx_último_match_en_window) — (0, -1) si nada matchea.
    """
    matches = 0
    last_k = -1
    k = 0
    for t in target:
        # buscar t desde k, pero sin saltar más de 2 palabras (inserción corta)
        found = -1
        for kk in range(k, min(len(window), k + 3)):
            if _tokens_match(t, window[kk]):
                found = kk
                break
        if found >= 0:
            matches += 1
            last_k = found
            k = found + 1
        else:
            # palabra distinta: avanzar una posición igual (sustitución)
            k += 1
        if k > len(window):
            break
    return matches, last_k


def locate_phrase(
    words: list[dict],
    phrase: str,
    *,
    prefer: str = "first",
    start_idx: int = 0,
    min_score: float | None = None,
    max_words: int = LOCATE_MAX_WORDS,
) -> Optional[dict]:
    """
    Busca `phrase` en las palabras Whisper (desde `start_idx`) con matching
    fuzzy en orden: normaliza acentos/puntuación, tolera 1 de cada 4 palabras
    distinta (1 de cada 3 en frases ≥ LOCATE_LONG_PHRASE_WORDS, W2-C) y hasta
    2 palabras insertadas. Si la frase aparece más de una vez, prefer="first"
    devuelve la primera aparición y "last" la última.

    `min_score=None` (default) usa el umbral según la longitud de la frase;
    pasar un valor explícito lo fija sin importar la longitud.

    Returns: {"start_idx", "end_idx", "score"} (índices en `words`) o None.
    """
    if not words or not phrase:
        return None
    raw_target = [t for t in (_fold_token(t) for t in phrase.split()) if t]
    target: list[str] = []
    for t in raw_target:
        target.extend(_expand_alnum_token(t))
    if len(target) > max_words:
        target = target[:max_words] if prefer == "first" else target[-max_words:]
    n = len(target)
    if n == 0:
        return None
    if min_score is None:
        min_score = LOCATE_MIN_SCORE_LONG if n >= LOCATE_LONG_PHRASE_WORDS else LOCATE_MIN_SCORE
    if n < 2:
        min_score = 1.0

    # `tokens` puede ser más largo que `words` (un token "r0" expande a dos:
    # "r", "cero"); `token_word_idx` mapea cada posición de `tokens` de vuelta
    # al índice real en `words` para que start_idx/end_idx sigan siendo
    # índices de `words`, como siempre. Para el 99% de las palabras (sin el
    # patrón letra+dígito pegado) es 1:1, igual que antes.
    tokens: list[str] = []
    token_word_idx: list[int] = []
    for wi, w in enumerate(words):
        folded = _fold_token(w.get("word") or "")
        for sub in (_expand_alnum_token(folded) if folded else [folded]):
            tokens.append(sub)
            token_word_idx.append(wi)
    total = len(tokens)
    tok_start = next((i for i, wi in enumerate(token_word_idx) if wi >= start_idx), total)
    slack = 2
    candidates: list[tuple[int, int, float]] = []   # (tok_start, tok_end, score)
    for i in range(tok_start, total):
        window = tokens[i:i + n + slack]
        if not window or not _tokens_match(target[0], window[0]):
            # exigir que la primera palabra de la frase ancle la ventana evita
            # matches "flotantes" en texto repetitivo
            continue
        matches, last_k = _window_alignment(target, window)
        score = matches / n
        if score >= min_score and last_k >= 0:
            candidates.append((i, i + last_k, score))

    if not candidates:
        # Segundo intento: sin exigir la primera palabra (puede ser la distinta)
        for i in range(tok_start, total):
            window = tokens[i:i + n + slack]
            if not window:
                continue
            matches, last_k = _window_alignment(target, window)
            score = matches / n
            if score >= min_score and last_k >= 0:
                candidates.append((i, i + last_k, score))
    if not candidates:
        return None

    # Agrupar candidatos solapados (ventanas vecinas de la misma aparición)
    groups: list[list[tuple[int, int, float]]] = []
    for c in candidates:
        if groups and c[0] <= groups[-1][-1][1]:
            groups[-1].append(c)
        else:
            groups.append([c])
    group = groups[0] if prefer == "first" else groups[-1]
    best = max(group, key=lambda c: (c[2], -c[0]))
    return {
        "start_idx": token_word_idx[best[0]],
        "end_idx": token_word_idx[best[1]],
        "score": round(best[2], 3),
    }


def _boundary_set(words: list[dict], segments: list[dict] | None) -> set[float]:
    from services.clip_generator import detect_sentence_boundaries
    return set(detect_sentence_boundaries(words, segments))


def _sentence_start_indices(words: list[dict], boundaries: set[float]) -> list[int]:
    """Índices de palabra donde empieza una oración (0 y las que siguen a un boundary)."""
    starts = [0] if words else []
    for k in range(len(words) - 1):
        if round(float(words[k].get("end", 0)), 3) in boundaries:
            starts.append(k + 1)
    return starts


def sentence_bounds_around(
    words: list[dict],
    idx: int,
    segments: list[dict] | None = None,
    *,
    max_lookback_sec: float = _SENTENCE_MAX_LOOKBACK_SEC,
    max_lookahead_sec: float = _SENTENCE_MAX_LOOKAHEAD_SEC,
) -> tuple[int, int]:
    """
    (start_idx, end_idx) de la oración que contiene la palabra `idx`.

    Retrocede hasta la palabra anterior que cierra oración (. ? ! …, gap
    > 0,6 s o fin de segmento Whisper con puntuación — detect_sentence_boundaries)
    y avanza hasta el cierre de la oración. Si no hay cierre en
    `max_lookback_sec` / `max_lookahead_sec` (Whisper sin puntuación), se
    queda en la palabra más lejana dentro de ese rango: una "oración" de 40 s
    no es una oración.
    """
    if not words:
        return 0, 0
    idx = max(0, min(idx, len(words) - 1))
    boundaries = _boundary_set(words, segments)
    anchor_start = float(words[idx].get("start", 0))
    anchor_end = float(words[idx].get("end", anchor_start))

    start_idx = idx
    k = idx - 1
    while k >= 0:
        if round(float(words[k].get("end", 0)), 3) in boundaries:
            break
        if anchor_start - float(words[k].get("start", 0)) > max_lookback_sec:
            break
        start_idx = k
        k -= 1

    end_idx = idx
    k = idx
    while k < len(words):
        we = float(words[k].get("end", 0))
        if we - anchor_end > max_lookahead_sec and k > idx:
            break
        end_idx = k
        if round(we, 3) in boundaries:
            break
        k += 1
    return start_idx, end_idx


def _first_alpha_is_lower(text: str) -> bool:
    for ch in text or "":
        if ch.isalpha():
            return ch.islower()
    return False


def compute_clip_bounds(
    words: list[dict],
    first_phrase: str | None,
    last_phrase: str | None,
    *,
    seg_start_abs: float,
    seg_end_abs: float,
    video_duration: float,
    min_s: float = CLIP_MIN_DURATION_SEC,
    max_s: float = CLIP_MAX_DURATION_SEC,
    hint_start_abs: float | None = None,
    hint_end_abs: float | None = None,
    segments: list[dict] | None = None,
    hook: str = "",
    overlay: str = "",
    start_pad: float = 0.25,
    end_pad: float = 0.40,
) -> dict:
    """
    Decide los límites del Clip dentro del segmento ancho a partir de las
    frases de Verificación del modelo. Función pura (sin FFmpeg ni red).

    `words` están en la línea de tiempo del segmento (0 = seg_start_abs).
    `hint_start_abs` / `hint_end_abs` son el start_time / end_time numéricos
    de la Pasada A (solo respaldo cuando una frase no aparece).

    Los pads (0,25 s antes / 0,40 s después) nunca cruzan la palabra vecina:
    con habla continua Whisper deja las palabras pegadas y el pad metería la
    última sílaba de la oración anterior (o la primera de la siguiente).

    Returns {"start_rel", "end_rel", "flags", "evidence"}; flags ⊆
    {hook_not_found, payoff_not_found}; evidence incluye
    `extend_recommended` (la última frase no está y el segmento no llega al
    final del video: vale la pena re-descargar con más margen).
    """
    seg_duration = max(0.0, float(seg_end_abs) - float(seg_start_abs))
    hint_start_rel = (
        min(max(0.0, float(hint_start_abs) - seg_start_abs), seg_duration)
        if hint_start_abs is not None else 0.0
    )
    hint_end_rel = (
        min(max(0.0, float(hint_end_abs) - seg_start_abs), seg_duration)
        if hint_end_abs is not None else seg_duration
    )
    flags: list[str] = []
    evidence: dict = {
        "first_found": False, "last_found": False,
        "first_idx": None, "last_idx": None,
        "first_score": None, "last_score": None,
        "extend_recommended": False,
        "start_source": None, "end_source": None,
        "seg_duration": round(seg_duration, 3),
    }

    if not words:
        # Sin palabras no hay oraciones: el numérico es lo único que hay
        flags += ["hook_not_found", "payoff_not_found"]
        evidence["start_source"] = evidence["end_source"] = "hint"
        return {
            "start_rel": hint_start_rel,
            "end_rel": max(hint_end_rel, min(seg_duration, hint_start_rel + min_s)),
            "flags": flags,
            "evidence": evidence,
        }

    n = len(words)
    boundaries = _boundary_set(words, segments)
    sentence_starts = _sentence_start_indices(words, boundaries)
    w_start = [float(w.get("start", 0)) for w in words]
    w_end = [float(w.get("end", 0)) for w in words]
    sentence_end_idxs = sorted(
        {k for k in range(n) if round(w_end[k], 3) in boundaries} | {n - 1}
    )

    def _start_at(idx: int) -> float:
        """Inicio del clip para arrancar en la palabra idx, sin pisar la anterior."""
        t = w_start[idx] - start_pad
        if idx > 0:
            t = max(t, w_end[idx - 1])
        return max(0.0, min(t, w_start[idx]))

    def _end_at(idx: int) -> float:
        """Fin del clip para terminar en la palabra idx, sin pisar la siguiente."""
        t = w_end[idx] + end_pad
        if idx + 1 < n:
            nxt = w_start[idx + 1]
            t = min(t, nxt) if nxt > w_end[idx] else w_end[idx]
        return min(seg_duration, t)

    # ── a) primera frase → inicio de oración ────────────────────────────────
    fi = locate_phrase(words, first_phrase or "", prefer="first") if first_phrase else None
    if fi:
        s_idx, _ = sentence_bounds_around(words, fi["start_idx"], segments)
        start_idx = s_idx
        evidence.update(first_found=True, first_idx=fi["start_idx"],
                        first_score=fi["score"], start_source="first_phrase")
        evidence["first_phrase_rel_start"] = round(w_start[fi["start_idx"]], 3)
        evidence["first_matched_text"] = " ".join(
            (w.get("word") or "").strip() for w in words[fi["start_idx"]:fi["end_idx"] + 1]
        )
    else:
        flags.append("hook_not_found")
        start_idx = None
        # Respaldo 1: ancla de hook/overlay cerca del start_time numérico
        if hook or overlay:
            win_lo = hint_start_rel - 3.0
            win_hi = hint_start_rel + max(8.0, 0.4 * max(0.0, hint_end_rel - hint_start_rel))
            window = [w for w in words if win_lo <= float(w.get("start", 0)) <= win_hi]
            t = find_hook_start_in_words(
                window, hook=hook, overlay=overlay, first_phrase="",
                clip_duration=0.0, search_ratio=1.0,
            ) if window else None
            if t is not None:
                idx = next(k for k in range(n) if abs(w_start[k] - t) < 1e-6)
                start_idx, _ = sentence_bounds_around(words, idx, segments)
                evidence["start_source"] = "hook_anchor"
        # Respaldo 2: inicio de oración más cercano ≥ start_time − 3 s
        if start_idx is None:
            after = [k for k in sentence_starts if w_start[k] >= hint_start_rel - 3.0]
            if after:
                start_idx = after[0]
                evidence["start_source"] = "sentence_after_hint"
            else:
                start_idx = sentence_starts[-1] if sentence_starts else 0
                evidence["start_source"] = "last_sentence_start"

    # ── g) nunca arrancar en minúscula si hay inicio de oración ≤ 2 s antes ──
    if _first_alpha_is_lower(words[start_idx].get("word") or ""):
        earlier = [
            k for k in sentence_starts
            if k < start_idx and 0.0 <= w_start[start_idx] - w_start[k] <= 2.0
        ]
        if earlier:
            start_idx = earlier[-1]
            evidence["lowercase_fix"] = True

    start_rel = _start_at(start_idx)

    # ── b) última frase, buscada solo DESPUÉS de la primera ─────────────────
    search_from = (fi["end_idx"] + 1) if fi else start_idx
    li = (
        locate_phrase(words, last_phrase or "", prefer="last", start_idx=search_from)
        if last_phrase else None
    )
    if li:
        _, e_idx = sentence_bounds_around(words, li["end_idx"], segments)
        end_idx = e_idx
        end_rel = _end_at(end_idx)
        evidence.update(last_found=True, last_idx=li["end_idx"],
                        last_score=li["score"], end_source="last_phrase")
        evidence["last_phrase_rel_end"] = round(w_end[li["end_idx"]], 3)
        evidence["last_matched_text"] = " ".join(
            (w.get("word") or "").strip() for w in words[li["start_idx"]:li["end_idx"] + 1]
        )
        payoff_end_rel = w_end[li["end_idx"]]
    else:
        flags.append("payoff_not_found")
        payoff_end_rel = None
        evidence["extend_recommended"] = bool(
            last_phrase and float(seg_end_abs) < float(video_duration) - 0.5
        )
        # Fin de oración que deje [min_s, max_s] desde start, el primero en o
        # después del end_time numérico (el remate suele estar justo después).
        lo, hi = start_rel + min_s, start_rel + max_s
        fitting = [k for k in sentence_end_idxs if lo <= _end_at(k) <= hi]
        after_hint = [k for k in fitting if w_end[k] >= hint_end_rel - 0.5]
        if after_hint:
            end_idx = after_hint[0]
            evidence["end_source"] = "sentence_after_hint"
        elif fitting:
            end_idx = fitting[-1]
            evidence["end_source"] = "last_fitting_sentence"
        else:
            end_idx = None
            evidence["end_source"] = "hint"
        end_rel = _end_at(end_idx) if end_idx is not None else min(
            seg_duration, max(hint_end_rel, start_rel + min_s)
        )

    # ── f) duración mínima: extender el fin al siguiente fin de oración ─────
    if end_rel - start_rel < min_s:
        for k in sentence_end_idxs:
            cand = _end_at(k)
            if cand > end_rel:
                end_rel = cand
                evidence["extended_for_min"] = True
                if end_rel - start_rel >= min_s:
                    break
        if end_rel - start_rel < min_s and seg_duration - start_rel >= min_s:
            end_rel = min(seg_duration, start_rel + min_s)
            evidence["extended_for_min"] = True

    # ── f) duración máxima ───────────────────────────────────────────────────
    if end_rel - start_rel > max_s:
        if payoff_end_rel is not None:
            # Preferir mover el START al siguiente inicio de oración antes que
            # perder el remate (decisión del brief W1): el primero que deje
            # ≤ max_s. Si eso deja afuera la primera frase, se flaggea.
            phrase_start_idx = fi["start_idx"] if fi else start_idx
            for k in sentence_starts:
                if k <= start_idx:
                    continue
                cand_start = _start_at(k)
                if max_s >= end_rel - cand_start >= min_s:
                    start_idx = k
                    start_rel = cand_start
                    evidence["start_moved_for_max"] = True
                    if k > phrase_start_idx:
                        evidence["hook_dropped_for_max"] = True
                        if "hook_not_found" not in flags:
                            flags.append("hook_not_found")
                    break
        if end_rel - start_rel > max_s:
            limit = start_rel + max_s
            fitting = [k for k in sentence_end_idxs if start_rel + min_s <= _end_at(k) <= limit]
            if fitting:
                end_rel = _end_at(fitting[-1])
            else:
                end_rel = min(seg_duration, limit)
            evidence["end_cut_for_max"] = True
            if payoff_end_rel is not None and payoff_end_rel > end_rel:
                evidence["payoff_dropped_for_max"] = True
                if "payoff_not_found" not in flags:
                    flags.append("payoff_not_found")

    start_rel = round(max(0.0, min(start_rel, seg_duration)), 3)
    end_rel = round(max(start_rel, min(end_rel, seg_duration)), 3)
    evidence["duration"] = round(end_rel - start_rel, 3)
    return {"start_rel": start_rel, "end_rel": end_rel, "flags": flags, "evidence": evidence}


def is_youtube_clip_fallback(clip_url: str | None) -> bool:
    """True si el clip_url es un deep-link de YouTube (no MP4 en R2)."""
    if not clip_url:
        return True
    u = clip_url.lower()
    return "youtube.com/watch" in u or "youtu.be/" in u


# W2-C: `late_hook` es informativo (no integra verification_failed, ver
# verification_failed_from_flags) y se mide en palabras además de segundos —
# W1 ancla el clip al INICIO DE ORACIÓN de la primera frase, no a su primera
# palabra, así que unas palabras/segundos de setup antes del hook citado son
# normales, no un corte tardío. 12 palabras u 8 s (lo que se cumpla primero)
# es bastante más que una oración de setup típica.
LATE_HOOK_MAX_WORDS = 12
LATE_HOOK_MAX_SEC = 8.0


def hook_delay_metrics(
    words: list[dict] | None,
    start_rel: float,
    first_phrase_rel_start: float | None,
) -> tuple[Optional[float], Optional[int]]:
    """
    Segundos y palabras entre el inicio del clip (`start_rel`, en la línea de
    tiempo del segmento ancho) y la primera palabra de `first_phrase_in_audio`
    (`first_phrase_rel_start`, misma línea de tiempo — evidence de
    compute_clip_bounds). (None, None) si la frase no se ancló.
    """
    if first_phrase_rel_start is None:
        return None, None
    delay_sec = float(first_phrase_rel_start) - float(start_rel)
    words_before = sum(
        1 for w in (words or [])
        if float(start_rel) <= float(w.get("start", 0)) < float(first_phrase_rel_start)
    )
    return delay_sec, words_before


def is_late_hook(
    delay_sec: float | None,
    words_before: int | None,
    *,
    max_words: int = LATE_HOOK_MAX_WORDS,
    max_sec: float = LATE_HOOK_MAX_SEC,
) -> bool:
    """True si el hook citado tarda demasiado en aparecer (W2-C: informativo,
    no forma parte de verification_failed)."""
    if delay_sec is None:
        return False
    return delay_sec > max_sec or (words_before is not None and words_before > max_words)


def verification_failed_from_flags(hook_not_found: bool, payoff_not_found: bool) -> bool:
    """
    Verificación (CONTEXT.md): el clip contiene lo que dice contener —
    `first_phrase_in_audio` y `last_phrase_in_audio` están dentro de él.

    W2-C (docs/PLAN_CALIDAD.md §9): antes `verification_failed` también se
    disparaba con `incomplete_tail` y `late_hook`, pero esas dos ya no
    significan "corte roto" desde W1: el clip ancla al INICIO DE ORACIÓN de
    la primera frase, no a su primera palabra, así que 3-8 s de contexto
    antes del hook citado es normal (no tardío), y la cola post-snap puede
    quedar "incompleta" por diseño cuando el remate se ancló bien. Con la
    semántica vieja, un candidato bien cortado perdía puntos en el ranking
    de W2 contra uno peor cortado por señales que no medían lo que decían
    medir (caso real: podcast_general_01, candidato 1010-1050s, mismo tema
    que el clip mejor puntuado de Opus Clip sobre este video).

    Ahora `verification_failed` es SOLO `hook_not_found or payoff_not_found`
    (alguna de las dos frases no se pudo anclar ni con el matching difuso de
    `locate_phrase`, o quedó afuera al ajustar la duración) — eso sí es "el
    clip no tiene lo que dice tener". `incomplete_tail`/`late_hook` siguen
    persistiendo como flags informativos en `clip_quality_issues`.
    """
    return bool(hook_not_found or payoff_not_found)


def build_clip_quality_issues(
    *,
    verification_info: dict | None = None,
    incomplete_tail: bool = False,
    late_hook: bool = False,
    clip_not_rendered: bool = False,
    clip_generation_error: str | None = None,
    timestamps_suspect: bool = False,
    bad_segment: bool = False,
    min_duration_reverted: bool = False,
    payoff_not_found: bool = False,
    hook_not_found: bool = False,
    margin_extended: bool = False,
    margin_extension_failed: bool = False,
    subs_disabled_timestamps: bool = False,
) -> list[str]:
    """
    Lista de flags de calidad para persistir en content_results.clip_quality_issues.

    Flags: incomplete_tail, late_hook, whisper_mismatch_first, whisper_mismatch_last,
    clip_not_rendered, clip_generation_failed, y las guardas W3:
      - timestamps_suspect: Whisper devolvió texto normal con tiempos comprimidos;
        el snap por silencio se omitió (ver assess_whisper_words).
      - bad_segment: el segmento descargado no tiene habla plausible; el clip se
        renderizó sin subtítulos aunque se reintentó la descarga.
      - min_duration_reverted: el refinamiento dejó el clip < 15 s y se
        volvió a límites anteriores (ver enforce_min_duration).
    Cortes anclados a frases (W1, compute_clip_bounds):
      - hook_not_found: first_phrase_in_audio no apareció en el segmento; el
        inicio se decidió por ancla de hook o por el start_time numérico.
      - payoff_not_found: last_phrase_in_audio no apareció (ni tras extender el
        margen) o no entró en los 120 s; el fin es un fin de oración de respaldo.
      - margin_extended: se re-descargó el segmento con +25 s al final para
        buscar la última frase.
      - margin_extension_failed: la última frase no aparece y la re-descarga
        con +25 s no consiguió más video (sin proxy/estrategia disponible);
        se siguió con el mejor segmento ya descargado en vez de perder el clip.
      - subs_disabled_timestamps: los dos proveedores Whisper dieron timestamps
        sospechosos; el clip se renderizó sin subtítulos.
    """
    issues: list[str] = []
    if incomplete_tail:
        issues.append("incomplete_tail")
    if late_hook:
        issues.append("late_hook")
    if timestamps_suspect:
        issues.append("timestamps_suspect")
    if bad_segment:
        issues.append("bad_segment")
    if min_duration_reverted:
        issues.append("min_duration_reverted")
    if hook_not_found:
        issues.append("hook_not_found")
    if payoff_not_found:
        issues.append("payoff_not_found")
    if margin_extended:
        issues.append("margin_extended")
    if margin_extension_failed:
        issues.append("margin_extension_failed")
    if subs_disabled_timestamps:
        issues.append("subs_disabled_timestamps")
    if verification_info:
        if not verification_info.get("first_ok", True):
            issues.append("whisper_mismatch_first")
        if not verification_info.get("last_ok", True):
            issues.append("whisper_mismatch_last")
    if clip_not_rendered:
        issues.append("clip_not_rendered")
    if clip_generation_error:
        issues.append("clip_generation_failed")
    return issues


def find_phrase_start_in_words(words: list[dict], phrase: str, max_words: int = 6) -> Optional[float]:
    """
    Fase 3: busca la frase (first_phrase_in_audio) en los whisper words del
    clip y devuelve el timestamp de inicio del match, o None.

    Match fuzzy: compara las primeras `max_words` palabras normalizadas de la
    frase contra ventanas consecutivas del clip.
    """
    if not words or not phrase:
        return None

    target = _normalize_phrase(phrase).split()[:max_words]
    if len(target) < 2:
        return None

    norm_words = []
    for w in words:
        nw = _normalize_phrase(w.get("word") or "")
        norm_words.append((nw, float(w.get("start", 0))))

    n = len(target)
    for i in range(len(norm_words) - n + 1):
        window = [norm_words[i + j][0] for j in range(n)]
        matches = sum(1 for a, b in zip(target, window) if a and a == b)
        # >= 70% de las palabras de la frase matchean en orden
        if matches >= max(2, int(round(n * 0.7))):
            return norm_words[i][1]
    return None


def _snap_end_to_segment_boundary(
    transcript: dict,
    start: float,
    hard_max_end: float,
    min_end: float,
) -> Optional[float]:
    """
    Fase 3: al truncar un momento a max_duration, elegir el fin de segmento
    de transcript más cercano <= hard_max_end (corta en fin de frase en vez
    de corte seco a mitad de oración).
    """
    segments = (transcript or {}).get("segments") or []
    best = None
    for sg in segments:
        sg_end = float(sg.get("end", 0))
        if min_end <= sg_end <= hard_max_end:
            if best is None or sg_end > best:
                best = sg_end
    return best


def validate_durations(
    viral_moments: list,
    min_duration: float = CLIP_MIN_DURATION_SEC,
    max_duration: float = CLIP_MAX_DURATION_SEC,
    transcript: dict = None,
) -> list:
    """
    Filter viral moments by duration; trim moments exceeding max_duration.

    Fase 3: si se pasa `transcript`, el truncado a max_duration se ajusta al
    boundary de segmento (fin de frase) más cercano <= max_duration en lugar
    de cortar seco.
    """
    valid_moments = []
    for i, moment in enumerate(viral_moments):
        start = getattr(moment, 'start_time', None)
        end = getattr(moment, 'end_time', None)
        hook = getattr(moment, 'hook', 'Unknown')[:30]

        if start is None or end is None:
            print(f"⚠️ Skipping moment {i+1} '{hook}' (missing timestamps: start={start}, end={end})")
            continue

        duration = end - start
        if duration < min_duration:
            print(f"⚠️ Skipping moment {i+1} '{hook}' (too short: {duration}s < {min_duration}s)")
            continue

        if duration > max_duration:
            new_end = start + max_duration
            snapped = _snap_end_to_segment_boundary(
                transcript,
                start=float(start),
                hard_max_end=float(start + max_duration),
                min_end=float(start + min_duration),
            )
            boundary_note = ""
            if snapped is not None:
                new_end = int(snapped)
                boundary_note = " (snap a fin de frase)"
            print(
                f"⚠️ Trimming moment {i+1} '{hook}' "
                f"from {duration}s to {new_end - start}s (end {end} → {new_end}){boundary_note}"
            )
            moment.end_time = new_end

        valid_moments.append(moment)

    return valid_moments
