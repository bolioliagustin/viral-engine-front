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


def validate_viral_moment_duration(
    moment,
    min_duration: int = 10,
    max_duration: int = 60,
) -> bool:
    """
    Validate that a viral moment meets duration requirements.

    Args:
        moment: Viral moment object with start_time and end_time
        min_duration: Minimum duration in seconds (default 10s)
        max_duration: Maximum duration in seconds (default 60s)

    Returns:
        True if valid, False if out of range or missing timestamps
    """
    if not hasattr(moment, 'start_time') or not hasattr(moment, 'end_time'):
        return False

    if moment.start_time is None or moment.end_time is None:
        return False

    duration = moment.end_time - moment.start_time
    return min_duration <= duration <= max_duration


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

    result["failed"] = not result["first_ok"] and not result["last_ok"]
    return result


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
