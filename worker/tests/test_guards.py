"""
Guardas de sanidad sobre Whisper y segmentos (W3, docs/PLAN_CALIDAD.md §4).

Reproduce los dos casos reales que motivaron las guardas:
  (a) job 1b1007c4 m=1: 73 palabras en 33 s con timestamps corridos (habla
      "amontonada" en los últimos ~9 s). El snap creía ver 24 s de silencio
      y recortaba el clip a 9 s con 72 palabras (densidad 7.98 w/s).
  (b) job 4bb4561b m=5: segmento sin habla, Whisper devolvió "O R m Y TleK E"
      en 27 s (densidad 0.23) y el clip se entregó sin ningún flag.
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.validation import (  # noqa: E402
    assess_whisper_words,
    build_clip_quality_issues,
    longest_repeated_run,
)
from services.clip_generator import (  # noqa: E402
    snap_trim_bounds,
    shift_words_timeline,
    filter_whisper_words,
    refine_bounds_to_sentences,
    enforce_min_duration,
)


# ── Fixtures sintéticas ──────────────────────────────────────────────────────

def _uniform_words(n: int, start: float, end: float, prefix: str = "w") -> list[dict]:
    """n palabras distintas repartidas uniformemente en [start, end]."""
    step = (end - start) / n
    return [
        {"word": f"{prefix}{i}", "start": start + i * step, "end": start + i * step + step * 0.7}
        for i in range(n)
    ]


def _phrase_words(text: str, start: float, end: float) -> list[dict]:
    """Palabras de `text` repartidas uniformemente en [start, end]."""
    tokens = text.split()
    step = (end - start) / len(tokens)
    return [
        {"word": t, "start": start + i * step, "end": start + i * step + step * 0.7}
        for i, t in enumerate(tokens)
    ]


def _case_a_words() -> list[dict]:
    """Caso (a): 73 palabras en un clip de 33 s, todas entre 24.3 y 33.0 s."""
    return _uniform_words(73, 24.3, 33.0)


CASE_A_DURATION = 33.0

CASE_B_TEXT = "O R m Y TleK E"
CASE_B_DURATION = 27.0

REPEATED_TEXT = " ".join(["en realidad los modelos están bastante bien"] * 6) + " en realidad los"


def _healthy_words() -> list[dict]:
    """Clip sano: 30 s, 80 palabras uniformes, 3 oraciones con puntuación."""
    words = _uniform_words(80, 0.4, 29.6)
    words[0]["word"] = "Hola"
    words[26]["word"] = "primera."
    words[27]["word"] = "Segunda"
    words[53]["word"] = "frase."
    words[54]["word"] = "Tercera"
    words[79]["word"] = "final."
    return words


# ── assess_whisper_words ─────────────────────────────────────────────────────

class TestAssessWhisperWords:

    def test_case_a_timestamps_suspect_not_bad_segment(self):
        result = assess_whisper_words(_case_a_words(), CASE_A_DURATION)
        assert result["density"] == pytest.approx(73 / 33, abs=0.05)   # 2.21, normal
        assert result["leading_gap"] == pytest.approx(24.3, abs=0.01)
        assert result["effective_density"] > 5.0                         # ~8.4 w/s
        assert "timestamps_suspect" in result["reasons"]
        assert "bad_segment" not in result["reasons"]
        assert result["timestamps_suspect"] is True
        assert result["bad_segment"] is False
        assert result["plausible"] is False

    def test_case_b_garbage_is_bad_segment(self):
        words = _phrase_words(CASE_B_TEXT, 3.0, 20.0)
        result = assess_whisper_words(words, CASE_B_DURATION)
        assert result["density"] < 1.2                                  # 0.22
        assert result["unique_words"] < 8
        assert "bad_segment" in result["reasons"]
        assert result["bad_segment"] is True
        assert result["plausible"] is False

    def test_repeated_text_is_bad_segment_even_with_normal_density(self):
        # Texto repetido en loop a 1.5 w/s: la densidad global no lo delata,
        # pero el vocabulario sí (unique_ratio) y la secuencia repetida también.
        words = _phrase_words(REPEATED_TEXT, 0.5, 29.5)
        result = assess_whisper_words(words, 30.0)
        assert result["density"] >= 1.2
        assert result["unique_ratio"] < 0.35
        assert result["repeated_run"] >= 4
        assert result["repeated_ratio"] > 0.40
        assert "bad_segment" in result["reasons"]
        assert "timestamps_suspect" not in result["reasons"]

    def test_real_repeated_case_low_density(self):
        # Clips m2/m5 del job 1b1007c4: densidad 0.64–0.70 con frase repetida.
        text = " ".join(["en realidad los modelos están bastante bien"] * 3)
        words = _phrase_words(text, 1.0, 31.0)   # 21 palabras / 32 s = 0.66
        result = assess_whisper_words(words, 32.0)
        assert result["density"] == pytest.approx(0.66, abs=0.02)
        assert result["bad_segment"] is True

    def test_healthy_clip_is_plausible(self):
        result = assess_whisper_words(_healthy_words(), 30.0)
        assert result["plausible"] is True
        assert result["reasons"] == []
        assert result["density"] == pytest.approx(80 / 30, abs=0.01)
        assert result["effective_density"] < 5.0
        assert result["unique_ratio"] > 0.9
        assert result["repeated_run"] == 0

    def test_empty_words_is_bad_segment(self):
        result = assess_whisper_words([], 30.0)
        assert result["bad_segment"] is True
        assert result["plausible"] is False

    def test_few_words_short_span_not_suspect(self):
        # 2 palabras en 0.3 s no deben disparar timestamps_suspect (piso 1 s)
        words = [{"word": "sí", "start": 5.0, "end": 5.1}, {"word": "claro", "start": 5.15, "end": 5.3}]
        result = assess_whisper_words(words, 10.0)
        assert result["timestamps_suspect"] is False
        assert result["bad_segment"] is True   # pero sí es un segmento sin habla

    def test_slow_speaker_is_not_bad_segment(self):
        # 1.3 w/s con vocabulario variado: hablante pausado, no basura.
        words = _uniform_words(39, 0.5, 29.5)
        result = assess_whisper_words(words, 30.0)
        assert result["bad_segment"] is False
        assert result["plausible"] is True


class TestLongestRepeatedRun:

    def test_no_repeat(self):
        assert longest_repeated_run(list("abcdefghij")) == 0

    def test_short_repeat_below_min_is_ignored(self):
        # "no no no no" → tri-grama repetido solapado, < 4 → 0
        assert longest_repeated_run(["no", "no", "no", "no"]) == 0

    def test_exact_repeat(self):
        toks = "en realidad los modelos están bastante bien".split()
        assert longest_repeated_run(toks + ["y"] + toks) == 7

    def test_repeat_extends_past_min(self):
        toks = list("abcdefgh")
        assert longest_repeated_run(toks + toks) >= 8


# ── snap_trim_bounds con guarda ──────────────────────────────────────────────

class TestSnapGuard:

    def test_case_a_snap_does_not_trim(self, capsys, caplog):
        # Si otro test importó main, print() va al logger del worker: mirar ambos.
        with caplog.at_level("INFO"):
            start, end = snap_trim_bounds(_case_a_words(), CASE_A_DURATION)
        assert (start, end) == (0.0, CASE_A_DURATION)
        out = capsys.readouterr().out + caplog.text
        assert "Snap omitido" in out and "sospechosos" in out

    def test_case_a_full_chain_keeps_density_normal(self):
        """Log real: 33 s → 9 s, 73 → 72 palabras, 7.98 w/s. Ahora: nada cambia."""
        words = _case_a_words()
        trim_start, trim_end = snap_trim_bounds(words, CASE_A_DURATION)
        s_start, s_end = refine_bounds_to_sentences(words, CASE_A_DURATION)
        trim_start = max(trim_start, s_start)
        trim_end = min(trim_end, s_end)
        new_duration = trim_end - trim_start
        assert new_duration == pytest.approx(CASE_A_DURATION)
        shifted = shift_words_timeline(words, trim_start, clip_duration=new_duration)
        kept = filter_whisper_words(shifted, new_duration)
        assert len(kept) == 73
        assert len(kept) / new_duration < 5.0

    def test_leading_gap_over_40pct_with_normal_density_is_suspect(self):
        # 60 palabras en 30 s (2 w/s) pero arrancando en 14 s (47 %): la
        # densidad efectiva (3.75) no dispara, el hueco inicial sí.
        words = _uniform_words(60, 14.0, 30.0)
        start, end = snap_trim_bounds(words, 30.0)
        assert (start, end) == (0.0, 30.0)

    def test_real_leading_silence_low_density_still_trims(self):
        # Intro/silencio real de 20 s en un clip de 30 s con 12 palabras
        # (densidad 0.4): la región descartada no tiene palabras → se recorta
        # (misma conducta que test_fix_ghost_leading_word_reanchors_speech).
        words = _uniform_words(12, 20.4, 25.2)
        start, _ = snap_trim_bounds(words, 30.0, min_words_after_trim=2)
        assert start == pytest.approx(20.1, abs=0.01)

    def test_moderate_leading_silence_trims_as_before(self):
        # 3 s de silencio inicial en un clip de 10 s: conducta original intacta
        words = [
            {"word": "hola", "start": 3.0, "end": 3.4},
            {"word": "mundo", "start": 3.5, "end": 4.0},
            {"word": "como", "start": 4.1, "end": 4.5},
            {"word": "estas", "start": 4.6, "end": 5.0},
            {"word": "hoy", "start": 5.1, "end": 9.5},
        ]
        start, end = snap_trim_bounds(words, 10.0, min_words_after_trim=2)
        assert start == pytest.approx(2.7)
        assert end == 10.0

    def test_leading_trim_over_40pct_with_words_inside_is_refused(self):
        # Palabras desordenadas: una cae dentro de la región que se descartaría.
        # El snap no puede tirar habla real por "silencio".
        words = _uniform_words(10, 16.0, 30.0) + [{"word": "temprana", "start": 2.0, "end": 2.4}]
        start, _ = snap_trim_bounds(words, 30.0, min_words_after_trim=2)
        assert start == 0.0

    def test_healthy_clip_bounds_unchanged(self):
        assert snap_trim_bounds(_healthy_words(), 30.0) == (0.0, 30.0)


# ── shift_words_timeline + filter_whisper_words ─────────────────────────────

class TestShiftAndFilter:

    def test_shift_24s_keeps_only_words_inside_clip(self):
        words = _uniform_words(73, 0.0, 33.0)          # 2.21 w/s uniformes
        offset = 24.0
        new_duration = 33.0 - offset
        shifted = shift_words_timeline(words, offset, clip_duration=new_duration)
        kept = filter_whisper_words(shifted, new_duration)
        assert 18 <= len(kept) <= 22
        assert len(kept) / new_duration == pytest.approx(2.2, abs=0.3)
        assert all(w["end"] > 0 for w in kept)
        assert all(w["start"] >= 0 for w in kept)
        assert all(w["start"] < new_duration for w in kept)

    def test_shift_drops_end_le_zero_and_clamps_crossing_word(self):
        words = [
            {"word": "antes", "start": 1.0, "end": 1.5},
            {"word": "cruza", "start": 1.9, "end": 2.6},
            {"word": "dentro", "start": 3.0, "end": 3.4},
        ]
        shifted = shift_words_timeline(words, 2.0, clip_duration=5.0)
        texts = [w["word"] for w in shifted]
        assert texts == ["cruza", "dentro"]
        assert shifted[0]["start"] == 0.0
        assert shifted[0]["end"] == pytest.approx(0.6)

    def test_shift_unsorted_input_drops_out_of_range_without_break(self):
        words = [
            {"word": "tarde", "start": 40.0, "end": 40.4},   # fuera del clip
            {"word": "dentro", "start": 5.0, "end": 5.4},
        ]
        shifted = shift_words_timeline(words, 0.0, clip_duration=10.0)
        assert [w["word"] for w in shifted] == ["dentro"]

    def test_never_more_words_than_fit_in_range(self):
        words = _uniform_words(50, 0.0, 50.0)
        shifted = shift_words_timeline(words, 30.0, clip_duration=10.0)
        kept = filter_whisper_words(shifted, 10.0)
        inside = [w for w in words if w["end"] > 30.0 and w["start"] < 40.0]
        assert len(kept) <= len(inside)


# ── enforce_min_duration ─────────────────────────────────────────────────────

class TestEnforceMinDuration:

    def test_final_ok_is_returned_untouched(self):
        bounds, reverted = enforce_min_duration([(0.0, 30.0), (2.0, 28.0), (3.5, 21.0)])
        assert bounds == (3.5, 21.0)
        assert reverted is False

    def test_reverts_to_last_candidate_meeting_min(self):
        bounds, reverted = enforce_min_duration([(0.0, 30.0), (2.0, 28.0), (12.0, 24.0)])
        assert bounds == (2.0, 28.0)
        assert reverted is True

    def test_reverts_to_original_when_no_candidate_meets_min(self):
        bounds, reverted = enforce_min_duration([(0.0, 12.0), (1.0, 11.0), (2.0, 9.0)])
        assert bounds == (0.0, 12.0)
        assert reverted is True

    def test_exact_min_is_accepted(self):
        bounds, reverted = enforce_min_duration([(0.0, 30.0), (5.0, 20.0)])
        assert bounds == (5.0, 20.0)
        assert reverted is False

    def test_requires_candidates(self):
        with pytest.raises(ValueError):
            enforce_min_duration([])


# ── build_clip_quality_issues ───────────────────────────────────────────────

class TestQualityFlags:

    def test_new_flags_are_emitted(self):
        issues = build_clip_quality_issues(
            timestamps_suspect=True, bad_segment=True, min_duration_reverted=True,
        )
        assert issues == ["timestamps_suspect", "bad_segment", "min_duration_reverted"]

    def test_no_flags_by_default(self):
        assert build_clip_quality_issues() == []

    def test_sync_retry_triggers_on_bad_segment(self):
        from main import _should_sync_retry_download
        assert _should_sync_retry_download(1.0, bad_segment=True) is True
        assert _should_sync_retry_download(None, bad_segment=True) is True
        assert _should_sync_retry_download(1.0) is False
        assert _should_sync_retry_download(0.85) is True


# ── Clip sano: nada cambia ──────────────────────────────────────────────────

class TestHealthyClipUnchanged:

    def test_healthy_clip_passes_all_guards(self):
        words = _healthy_words()
        duration = 30.0
        assessment = assess_whisper_words(words, duration)
        assert assessment["plausible"] is True

        snap = snap_trim_bounds(words, duration)
        assert snap == (0.0, duration)
        refined = refine_bounds_to_sentences(words, duration, max_duration=60.0)
        assert refined == (0.0, duration)

        bounds, reverted = enforce_min_duration([(0.0, duration), snap, refined])
        assert bounds == (0.0, duration)
        assert reverted is False

        issues = build_clip_quality_issues(
            timestamps_suspect=assessment["timestamps_suspect"],
            bad_segment=assessment["bad_segment"],
            min_duration_reverted=reverted,
        )
        assert issues == []
