"""
Video validation utilities
"""
import re
from typing import Optional


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


def is_youtube_clip_fallback(clip_url: str | None) -> bool:
    """True si el clip_url es un deep-link de YouTube (no MP4 en R2)."""
    if not clip_url:
        return True
    u = clip_url.lower()
    return "youtube.com/watch" in u or "youtu.be/" in u


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
    min_duration: int = 10,
    max_duration: int = 60,
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
