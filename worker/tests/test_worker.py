"""
C2b: Worker Unit Tests
Tests core worker functions: env validation, cleanup, recovery, and processing pipeline.
"""
import os
import sys
import json
import time
import logging
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add worker root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestValidateEnv:
    """Tests for environment variable validation."""

    @patch.dict(os.environ, {
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_SERVICE_KEY": "test-key",
        "OPENROUTER_API_KEY": "test-router",
        "OPENAI_API_KEY": "test-openai",
    })
    def test_valid_env_passes(self):
        from config.validate_env import validate_env
        # Should not raise
        validate_env()

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_required_vars_exits(self):
        # Re-import to reset module state
        import importlib
        from config import validate_env as ve_module
        importlib.reload(ve_module)
        
        with pytest.raises(SystemExit):
            ve_module.validate_env()


class TestCleanupOldFiles:
    """Tests for the Q3 cleanup function."""

    def test_removes_old_files(self, tmp_path):
        """Files older than max_age should be deleted."""
        # Create an "old" file
        old_file = tmp_path / "old_video.mp4"
        old_file.write_text("old data")
        # Backdate the file mtime by 48 hours
        old_time = time.time() - (48 * 3600)
        os.utime(old_file, (old_time, old_time))

        # Create a "new" file
        new_file = tmp_path / "new_video.mp4"
        new_file.write_text("new data")

        # Import and call with our dirs
        from main import cleanup_old_files, DOWNLOADS_DIR, CLIPS_DIR
        
        # Patch the dirs to use tmp_path
        with patch("main.DOWNLOADS_DIR", tmp_path), \
             patch("main.CLIPS_DIR", tmp_path / "nonexistent"):
            cleanup_old_files(max_age_hours=24)

        assert not old_file.exists(), "Old file should be deleted"
        assert new_file.exists(), "New file should be preserved"

    def test_handles_empty_directory(self, tmp_path):
        """Should not crash if directories don't exist."""
        from main import cleanup_old_files
        
        with patch("main.DOWNLOADS_DIR", tmp_path / "nonexistent"), \
             patch("main.CLIPS_DIR", tmp_path / "also_nonexistent"):
            # Should not raise
            cleanup_old_files()


class TestYouTubeUrlValidation:
    """Tests for YouTube URL validation regex (mirrors backend tests)."""

    def test_valid_youtube_urls(self):
        import re
        youtube_regex = re.compile(
            r'^(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)[\w-]+'
        )

        valid_urls = [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        ]
        for url in valid_urls:
            assert youtube_regex.match(url), f"Should match: {url}"

    def test_invalid_youtube_urls(self):
        import re
        youtube_regex = re.compile(
            r'^(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)[\w-]+'
        )

        invalid_urls = [
            "https://vimeo.com/12345",
            "https://google.com",
            "not-a-url",
            "ftp://youtube.com/watch?v=abc123",
        ]
        for url in invalid_urls:
            assert not youtube_regex.match(url), f"Should NOT match: {url}"


class TestJobTimeout:
    """Tests for the C5 timeout mechanism."""

    def test_timeout_event_is_set(self):
        """The threading.Event should be set when timer fires."""
        import threading

        timed_out = threading.Event()
        timer = threading.Timer(0.1, lambda: timed_out.set())
        timer.start()
        time.sleep(0.3)
        assert timed_out.is_set(), "Timeout event should be set"

    def test_timeout_can_be_cancelled(self):
        """Cancelling timer should prevent event from being set."""
        import threading

        timed_out = threading.Event()
        timer = threading.Timer(0.5, lambda: timed_out.set())
        timer.start()
        timer.cancel()
        time.sleep(0.7)
        assert not timed_out.is_set(), "Cancelled timer should not set event"


class TestResolveVideoDuration:
    """Tests for partial-download duration estimation."""

    def test_prefers_max_of_sources(self):
        from main import _resolve_video_duration
        from types import SimpleNamespace

        video_info = {"duration": 1800}
        transcript = {"segments": [{"end": 3600}]}
        moments = [SimpleNamespace(end_time=90.0), SimpleNamespace(end_time=4500.0)]

        assert _resolve_video_duration(video_info, transcript, moments) == 4530.0

    def test_fallback_from_transcript_when_duration_zero(self):
        from main import _resolve_video_duration

        video_info = {"duration": 0}
        transcript = {"segments": [{"start": 0, "end": 120.5}]}

        assert _resolve_video_duration(video_info, transcript, []) == 125.5

    def test_unknown_duration_assumes_long_video(self):
        from main import _resolve_video_duration

        assert _resolve_video_duration({}, {"segments": []}, []) == 7200.0


class TestDownloadStrategy:
    """Tests for yt-dlp full vs partial download gating."""

    @patch.dict(os.environ, {"USE_RAPIDAPI_DOWNLOAD": "true"}, clear=False)
    def test_skips_full_when_rapidapi_forced(self):
        from main import _should_try_full_ytdlp_download
        assert _should_try_full_ytdlp_download(600) is False

    @patch.dict(os.environ, {}, clear=True)
    def test_skips_full_for_long_videos(self):
        from main import _should_try_full_ytdlp_download
        assert _should_try_full_ytdlp_download(4000) is False

    @patch.dict(os.environ, {}, clear=True)
    def test_allows_full_for_short_videos(self):
        from main import _should_try_full_ytdlp_download
        assert _should_try_full_ytdlp_download(1800) is True


class TestDrmDetection:
    """Tests for yt-dlp DRM / PO Token error detection."""

    def test_detects_drm_error(self):
        from services.downloader import is_ytdlp_drm_error
        assert is_ytdlp_drm_error(Exception("This video is DRM protected"))
        assert is_ytdlp_drm_error(Exception("ios client requires a GVS PO Token"))

    def test_ignores_generic_errors(self):
        from services.downloader import is_ytdlp_drm_error
        assert not is_ytdlp_drm_error(Exception("Connection timeout"))


class TestStreamUrlStrategy:
    """Tests for RapidAPI-first stream URL resolution."""

    @patch.dict(os.environ, {
        "ENVIRONMENT": "production",
        "RAPIDAPI_KEY": "test-key",
    }, clear=False)
    @patch("services.downloader.get_stream_urls_rapidapi")
    def test_production_uses_rapidapi_first(self, mock_rapid):
        from services.downloader import get_stream_urls
        mock_rapid.return_value = {"video_url": "v", "audio_url": "a", "video_id": "x"}
        result = get_stream_urls("https://youtube.com/watch?v=abc12345678")
        mock_rapid.assert_called_once()
        assert result["video_url"] == "v"


class TestSubtitleSync:
    """Tests for word-level subtitle timing."""

    def test_filter_whisper_keeps_words_in_range(self):
        from services.clip_generator import filter_whisper_words
        raw = [
            {"word": "hola", "start": 0.1, "end": 0.4},
            {"word": "mundo", "start": 0.5, "end": 0.9},
            {"word": "amara.org", "start": 1.0, "end": 1.2},
        ]
        kept = filter_whisper_words(raw, clip_duration=10.0)
        assert len(kept) == 2
        assert kept[0]["word"] == "hola"

    def test_words_to_srt_uses_word_boundaries(self):
        from services.clip_generator import _words_to_srt_entries
        words = [
            {"word": "uno", "start": 1.0, "end": 1.3},
            {"word": "dos", "start": 1.35, "end": 1.6},
            {"word": "tres", "start": 2.0, "end": 2.4},
        ]
        entries = _words_to_srt_entries(words, 0.0, 5.0, max_words_per_line=2)
        assert len(entries) == 2
        assert "00:00:01,000 --> 00:00:01,700" in entries[0]
        assert "uno dos" in entries[0]
        assert "tres" in entries[1]

    def test_words_to_srt_splits_on_long_gaps(self):
        from services.clip_generator import _words_to_srt_entries
        words = [
            {"word": "a", "start": 0.0, "end": 0.4},
            {"word": "b", "start": 0.5, "end": 0.9},
            {"word": "c", "start": 9.0, "end": 9.4},
            {"word": "d", "start": 9.5, "end": 9.9},
        ]
        entries = _words_to_srt_entries(words, 0.0, 12.0, max_words_per_line=4)
        assert len(entries) == 2
        assert "a b" in entries[0]
        assert "c d" in entries[1]
        assert "00:00:09," in entries[1]

    def test_words_to_srt_caps_chunk_duration(self):
        from services.clip_generator import _words_to_srt_entries
        words = [
            {"word": "w1", "start": 0.0, "end": 0.3},
            {"word": "w2", "start": 0.4, "end": 0.7},
            {"word": "w3", "start": 0.8, "end": 5.0},
            {"word": "w4", "start": 5.1, "end": 5.4},
        ]
        entries = _words_to_srt_entries(words, 0.0, 10.0, max_words_per_line=4)
        assert len(entries) >= 2
        assert entries[0].count("-->") == 1
        first_end = entries[0].split("\n")[1].split(" --> ")[1]
        assert first_end < "00:00:05,000"


class TestRapidApiPreference:
    """Tests for production RapidAPI-first download policy."""

    @patch.dict(os.environ, {
        "ENVIRONMENT": "production",
        "RAPIDAPI_KEY": "test-key",
    }, clear=False)
    def test_production_skips_ytdlp_clips(self):
        from main import _should_use_ytdlp_for_clips, _prefer_rapidapi_download
        assert _prefer_rapidapi_download() is True
        assert _should_use_ytdlp_for_clips() is False

    @patch.dict(os.environ, {}, clear=True)
    def test_dev_allows_ytdlp_clips_without_rapidapi(self):
        from main import _should_use_ytdlp_for_clips
        assert _should_use_ytdlp_for_clips() is True


class TestStructuredLogging:
    """Tests for S6 logging configuration."""

    def test_logger_creation(self):
        from config.logging_config import get_logger
        logger = get_logger("test")
        assert logger is not None

    def test_logger_with_extra_fields(self, capsys):
        from config.logging_config import get_logger
        logger = get_logger("test_extra")
        logger.info("test message", extra={"job_id": "123", "step": "download"})
        
        captured = capsys.readouterr()
        assert "test message" in captured.out


class TestApplyWordCorrections:
    """Tests for E.1 word_corrections pipeline."""

    def test_matches_by_timestamp_tolerance(self):
        from services.clip_generator import apply_word_corrections
        words = [
            {"word": "hola", "start": 1.0, "end": 1.3},
            {"word": "mundo", "start": 1.4, "end": 1.8},
        ]
        corrections = [
            {"start": 1.02, "end": 1.28, "original": "hola", "corrected": "ola"},
        ]
        result = apply_word_corrections(words, corrections)
        assert result[0]["word"] == "ola"
        assert result[1]["word"] == "mundo"

    def test_fallback_to_index(self):
        from services.clip_generator import apply_word_corrections
        words = [
            {"word": "foo", "start": 0.0, "end": 0.2},
            {"word": "bar", "start": 0.3, "end": 0.5},
        ]
        corrections = [
            {"start": 9.9, "end": 9.9, "index": 1, "corrected": "baz"},
        ]
        result = apply_word_corrections(words, corrections)
        assert result[1]["word"] == "baz"

    def test_empty_corrections_returns_copy(self):
        from services.clip_generator import apply_word_corrections
        words = [{"word": "test", "start": 0.0, "end": 0.3}]
        result = apply_word_corrections(words, [])
        assert result[0]["word"] == "test"
        assert result is not words


class TestSnapTrimAndCoverage:
    """Tests for Fase A snap trim and Fase D coverage metrics."""

    def test_snap_trim_bounds_trims_leading_silence(self):
        from services.clip_generator import snap_trim_bounds
        words = [
            {"word": "hola", "start": 3.0, "end": 3.4},
            {"word": "mundo", "start": 3.5, "end": 4.0},
            {"word": "como", "start": 4.1, "end": 4.5},
            {"word": "estas", "start": 4.6, "end": 5.0},
            {"word": "hoy", "start": 5.1, "end": 9.5},
        ]
        start, end = snap_trim_bounds(words, clip_duration=10.0, min_words_after_trim=2)
        assert start == pytest.approx(2.7)
        assert end == 10.0

    def test_snap_trim_bounds_trims_trailing_silence(self):
        from services.clip_generator import snap_trim_bounds
        words = [{"word": "fin", "start": 1.0, "end": 3.0}]
        start, end = snap_trim_bounds(words, clip_duration=10.0)
        assert start == 0.0
        assert end == pytest.approx(3.5)

    def test_shift_words_drops_pre_trim_words(self):
        from services.clip_generator import shift_words_timeline

        words = [
            {"word": "antes", "start": 0.5, "end": 1.0},
            {"word": "corte", "start": 1.2, "end": 1.6},
            {"word": "despues", "start": 3.0, "end": 3.4},
        ]
        shifted = shift_words_timeline(words, offset_sec=2.7, clip_duration=5.0)
        texts = [w["word"] for w in shifted]
        assert "antes" not in texts
        assert "corte" not in texts
        assert "despues" in texts
        assert shifted[0]["start"] == pytest.approx(0.3, abs=0.05)

    def test_shift_words_no_pileup_at_zero(self):
        from services.clip_generator import shift_words_timeline

        words = [
            {"word": "ghost", "start": 0.2, "end": 0.5},
            {"word": "real", "start": 3.0, "end": 3.4},
        ]
        shifted = shift_words_timeline(words, offset_sec=2.7, clip_duration=5.0)
        assert len(shifted) == 1
        assert shifted[0]["word"] == "real"

    def test_snap_shift_word_count_monotonic(self):
        from services.clip_generator import (
            snap_trim_bounds,
            shift_words_timeline,
            filter_whisper_words,
        )

        words = [
            {"word": f"w{i}", "start": 3.0 + i * 0.4, "end": 3.3 + i * 0.4}
            for i in range(20)
        ]
        trim_start, trim_end = snap_trim_bounds(words, clip_duration=15.0)
        new_duration = trim_end - trim_start
        shifted = shift_words_timeline(
            words, trim_start, clip_duration=new_duration
        )
        filtered = filter_whisper_words(shifted, new_duration)
        assert len(filtered) <= len(words)
        assert len(filtered) > 0

    def test_first_srt_chunk_duration_under_2s(self):
        from services.clip_generator import _words_to_srt_entries, first_srt_chunk_duration
        words = [
            {"word": "uno", "start": 0.4, "end": 0.7},
            {"word": "dos", "start": 0.8, "end": 1.1},
            {"word": "tres", "start": 1.2, "end": 1.5},
        ]
        entries = _words_to_srt_entries(words, 0.0, 5.0, max_words_per_line=4)
        dur = first_srt_chunk_duration(entries)
        assert dur is not None
        assert dur < 2.0

    def test_srt_coverage_metric(self):
        from services.clip_generator import srt_coverage_metric
        words = [
            {"word": f"w{i}", "start": i * 0.5, "end": i * 0.5 + 0.3}
            for i in range(20)
        ]
        coverage = srt_coverage_metric(words, clip_duration=10.0)
        assert 0.5 <= coverage <= 1.0


class TestOverlapFilter:
    """Tests for Fase A overlap rejection."""

    def test_rejects_high_overlap(self):
        from services.validation import filter_overlapping_moments
        from types import SimpleNamespace

        moments = [
            SimpleNamespace(start_time=10, end_time=50, hook="A"),
            SimpleNamespace(start_time=20, end_time=60, hook="B"),
            SimpleNamespace(start_time=100, end_time=140, hook="C"),
        ]
        kept = filter_overlapping_moments(moments, max_overlap_ratio=0.5)
        assert len(kept) == 2
        assert kept[0].hook == "A"
        assert kept[1].hook == "C"

    def test_validate_durations_enforces_max_60(self):
        from services.validation import validate_durations
        from types import SimpleNamespace

        moment = SimpleNamespace(start_time=0, end_time=90, hook="long")
        kept = validate_durations([moment], max_duration=60)
        assert len(kept) == 1
        assert kept[0].end_time == 60


class TestGoldenSetRegression:
    """Static checks against golden_set.json Lqq78q17jDY criteria."""

    def test_lqq78_case_has_subtitle_regression(self):
        golden_path = Path(__file__).parent.parent / "eval" / "golden_set.json"
        data = json.loads(golden_path.read_text(encoding="utf-8"))
        case = next(v for v in data["videos"] if v.get("youtube_id") == "Lqq78q17jDY")
        reg = case["subtitle_regression"]
        assert reg["first_srt_chunk_max_sec"] == 2.0
        assert reg["coverage_min"] == 0.9


class TestModelTiers:
    """Fase 1: resolución de modelos por tarea vía env."""

    @patch.dict(os.environ, {}, clear=True)
    def test_defaults_when_no_env(self):
        from config.model_tiers import get_model
        assert get_model("analysis") == "google/gemini-2.5-pro"
        assert get_model("copy") == "google/gemini-2.5-flash"
        assert get_model("judge") == "google/gemini-2.5-flash-lite"
        assert get_model("classifier") == "google/gemini-2.0-flash-001"

    @patch.dict(os.environ, {"MODEL_ANALYSIS": "anthropic/claude-sonnet-4.5"}, clear=True)
    def test_new_env_takes_precedence(self):
        from config.model_tiers import get_model
        assert get_model("analysis") == "anthropic/claude-sonnet-4.5"

    @patch.dict(os.environ, {"MODEL_COPY_WRITING": "google/gemini-3.5-flash"}, clear=True)
    def test_copy_writing_env_alias(self):
        from config.model_tiers import get_model
        assert get_model("copy") == "google/gemini-3.5-flash"

    @patch.dict(os.environ, {"OPENROUTER_MODEL": "google/gemini-2.0-flash-exp:free"}, clear=True)
    def test_legacy_env_fallback(self):
        from config.model_tiers import get_model, is_free_tier
        assert get_model("analysis") == "google/gemini-2.0-flash-exp:free"
        assert is_free_tier(get_model("analysis"))

    @patch.dict(os.environ, {
        "MODEL_ANALYSIS": "google/gemini-2.5-pro",
        "OPENROUTER_MODEL": "google/gemini-2.0-flash-exp:free",
    }, clear=True)
    def test_new_env_wins_over_legacy(self):
        from config.model_tiers import get_model
        assert get_model("analysis") == "google/gemini-2.5-pro"

    def test_temperatures_low_for_structural(self):
        from config.model_tiers import get_temperature
        assert get_temperature("analysis") <= 0.3
        assert get_temperature("judge") <= 0.3
        assert get_temperature("classifier") == 0.0
        assert get_temperature("copy") >= 0.5

    def test_language_instruction(self):
        from config.model_tiers import output_language_instruction, language_name
        assert "español" in output_language_instruction("es")
        assert "English" in output_language_instruction("en-US")
        assert language_name(None) == "español"


class TestMomentSelector:
    """Fase 2: sobre-generación + ranking de candidatos."""

    def test_target_moment_count(self):
        from services.moment_selector import target_moment_count
        assert target_moment_count(60) == 1
        assert target_moment_count(200) == 3
        assert target_moment_count(3600) == 5

    def test_candidate_count_overgenerates(self):
        from services.moment_selector import candidate_count, target_moment_count
        # video de 30 min → 12 candidatos (cap)
        assert candidate_count(1800, target_moment_count(1800)) == 12
        # video de 8 min → 8 candidatos
        assert candidate_count(480, target_moment_count(480)) == 8
        # nunca menos que target
        assert candidate_count(120, target_moment_count(120)) >= 3

    def test_rank_and_prune_keeps_best_in_chrono_order(self):
        from services.moment_selector import rank_and_prune_candidates
        result = {
            "viral_moments": [
                {"start_time": 10, "scores": {"hook": 5, "retention": 5, "shareability": 5}},
                {"start_time": 100, "scores": {"hook": 9, "retention": 9, "shareability": 9}},
                {"start_time": 50, "scores": {"hook": 8, "retention": 8, "shareability": 8}},
                {"start_time": 200, "scores": {"hook": 2, "retention": 2, "shareability": 2}},
            ]
        }
        pruned = rank_and_prune_candidates(result, target=2)
        moments = pruned["viral_moments"]
        assert len(moments) == 2
        # top 2 por score (100 y 50), en orden cronológico
        assert moments[0]["start_time"] == 50
        assert moments[1]["start_time"] == 100

    def test_content_pieces_optional_for_pass_a(self):
        from models.schemas import ViralMoment
        m = ViralMoment(
            start_time=10, end_time=40,
            hook="test", emotional_trigger="Curiosidad",
            content_pieces={},
        )
        assert m.content_pieces.twitter_thread is None


class TestSentenceSnap:
    """Fase 3: refinamiento de límites a boundaries de oración."""

    @staticmethod
    def _words(spec):
        """spec: list of (word, start, end)."""
        return [{"word": w, "start": s, "end": e} for w, s, e in spec]

    def test_detect_boundaries_punctuation_and_gaps(self):
        from services.clip_generator import detect_sentence_boundaries
        words = self._words([
            ("Hola", 0.0, 0.4), ("mundo.", 0.5, 1.0),
            ("Segunda", 1.2, 1.6), ("frase", 1.7, 2.1),  # gap 1.0s después
            ("tercera", 3.1, 3.5),
        ])
        bounds = detect_sentence_boundaries(words)
        assert 1.0 in bounds   # puntuación
        assert 2.1 in bounds   # gap > 0.6

    def test_tail_trim_incomplete_sentence(self):
        from services.clip_generator import refine_bounds_to_sentences
        # Oración completa hasta 24.0, luego fragmento cortado hasta 29.5
        words = self._words(
            [("Palabra", 0.0, 0.5)]
            + [(f"w{i}", 0.5 + i, 1.0 + i) for i in range(1, 23)]
            + [("final.", 23.5, 24.0)]
            + [("fragmento", 24.5, 25.0), ("cortado", 25.2, 29.5)]
        )
        start, end = refine_bounds_to_sentences(words, clip_duration=30.0)
        assert start == 0.0
        assert end < 30.0
        assert abs(end - 24.4) < 0.01  # boundary 24.0 + end_pad 0.4

    def test_complete_first_sentence_not_dropped(self):
        from services.clip_generator import refine_bounds_to_sentences
        # Clip que arranca en inicio de oración (mayúscula) — no dropear head
        words = self._words([
            ("Hola.", 0.0, 1.0),
            ("Segunda", 1.2, 12.0), ("frase", 12.1, 20.0), ("larga.", 20.1, 25.0),
        ])
        start, _ = refine_bounds_to_sentences(words, clip_duration=25.0)
        assert start == 0.0

    def test_mid_sentence_head_dropped(self):
        from services.clip_generator import refine_bounds_to_sentences
        # Arranca en minúscula (mitad de oración) con boundary temprano
        words = self._words([
            ("que", 0.0, 0.3), ("decía.", 0.4, 1.0),
            ("Ahora", 1.5, 2.0), ("empieza", 2.1, 10.0),
            ("lo", 10.1, 15.0), ("bueno.", 15.1, 20.0),
        ])
        start, end = refine_bounds_to_sentences(words, clip_duration=20.0)
        assert start > 1.0  # dropeó el fragmento "que decía."
        assert end == 20.0

    def test_no_punctuation_no_refine(self):
        from services.clip_generator import refine_bounds_to_sentences
        words = self._words([("hola", 0.0, 0.5), ("mundo", 5.0, 20.0)])
        assert refine_bounds_to_sentences(words, clip_duration=25.0) == (0.0, 25.0)

    def test_max_duration_snaps_to_boundary(self):
        from services.clip_generator import refine_bounds_to_sentences
        # 70s de palabras con boundaries cada ~20s — cap a 60 debe caer en boundary
        words = self._words([
            ("Uno.", 0.0, 19.0),
            ("Dos.", 20.0, 39.0),
            ("Tres.", 40.0, 55.0),
            ("Cuatro.", 56.0, 70.0),
        ])
        start, end = refine_bounds_to_sentences(words, clip_duration=70.0, max_duration=60.0)
        assert start == 0.0
        assert end <= 60.0
        assert abs(end - 55.4) < 0.01  # boundary 55.0 + 0.4

    def test_regression_clip1_incomplete_tail(self):
        """Job e3e7d54b clip 1: segmento termina en 'pidiendo.' pero words siguen."""
        from services.clip_generator import refine_bounds_to_sentences, has_incomplete_tail
        words = self._words([
            ("absolutamente", 28.0, 28.5), ("todo", 28.5, 28.9),
            ("le", 29.2, 29.5), ("estamos", 29.5, 30.0), ("pidiendo.", 30.0, 31.46),
            ("Para", 31.84, 32.0), ("entender", 32.0, 32.04), ("lo", 32.04, 32.1),
        ])
        assert has_incomplete_tail(words)
        _, end = refine_bounds_to_sentences(words, clip_duration=32.0)
        assert end < 32.0
        assert abs(end - 31.86) < 0.05  # 31.46 + end_pad 0.4

    def test_find_last_complete_sentence_end(self):
        from services.clip_generator import find_last_complete_sentence_end
        words = self._words([
            ("hola", 0.0, 0.5), ("mundo.", 0.5, 1.0),
            ("Para", 1.2, 1.5), ("entender", 1.5, 1.8),
        ])
        assert abs(find_last_complete_sentence_end(words) - 1.0) < 0.01

    def test_apply_whisper_brand_corrections(self):
        from services.clip_generator import apply_whisper_brand_corrections
        words = [{"word": "Coulouse", "start": 0.0, "end": 0.3},
                 {"word": "es", "start": 0.3, "end": 0.5}]
        fixed = apply_whisper_brand_corrections(words, ["Claude", "Claude Code"])
        assert fixed[0]["word"] == "Claude"

    def test_find_hook_start_in_words(self):
        from services.validation import find_hook_start_in_words
        words = [
            {"word": "Prenderlo", "start": 0.0, "end": 0.2},
            {"word": "de", "start": 0.2, "end": 0.3},
            {"word": "forma", "start": 0.3, "end": 0.5},
            {"word": "Claude", "start": 1.6, "end": 2.0},
            {"word": "es", "start": 2.0, "end": 2.2},
            {"word": "únicamente", "start": 2.2, "end": 3.0},
            {"word": "la", "start": 3.0, "end": 3.2},
            {"word": "mente", "start": 3.2, "end": 4.0},
        ]
        t = find_hook_start_in_words(
            words,
            hook="Claude es únicamente la mente",
            overlay="LA IA CON MANOS",
            clip_duration=20.0,
        )
        assert t is not None
        assert abs(t - 1.6) < 0.01

    def test_validate_durations_snaps_to_segment_end(self):
        from services.validation import validate_durations
        from types import SimpleNamespace
        transcript = {"segments": [
            {"start": 0, "end": 20, "text": "a"},
            {"start": 20, "end": 45, "text": "b"},
            {"start": 45, "end": 55, "text": "c"},
            {"start": 55, "end": 75, "text": "d"},
        ]}
        moment = SimpleNamespace(start_time=0, end_time=90, hook="long")
        kept = validate_durations([moment], max_duration=60, transcript=transcript)
        assert len(kept) == 1
        # snap al fin de segmento más cercano <= 60 → 55 (no corte seco a 60)
        assert kept[0].end_time == 55

    def test_find_phrase_start_in_words(self):
        from services.validation import find_phrase_start_in_words
        words = [
            {"word": "bueno", "start": 0.0, "end": 0.4},
            {"word": "eh", "start": 0.5, "end": 0.8},
            {"word": "el", "start": 3.0, "end": 3.2},
            {"word": "error", "start": 3.3, "end": 3.7},
            {"word": "más", "start": 3.8, "end": 4.0},
            {"word": "grande", "start": 4.1, "end": 4.5},
        ]
        t = find_phrase_start_in_words(words, "el error más grande")
        assert t == 3.0
        assert find_phrase_start_in_words(words, "frase inexistente aquí") is None


class TestScorer:
    """Fase 4: ROI determinístico + verificación."""

    def test_deterministic_roi_formula(self):
        from services.scorer import deterministic_roi
        # 8 base + 0.5*30s + 15*3 piezas = 68
        assert deterministic_roi(30, 3) == 68
        assert deterministic_roi(0, 0) == 8
        # monotónico en duración y piezas
        assert deterministic_roi(60, 3) > deterministic_roi(30, 3)
        assert deterministic_roi(30, 3) > deterministic_roi(30, 1)

    def test_verify_phrases_returns_dict_with_failed_flag(self):
        from services.validation import verify_phrases_against_whisper
        from types import SimpleNamespace

        words = [{"word": w, "start": i, "end": i + 0.5}
                 for i, w in enumerate(["hola", "a", "todos", "gracias", "por", "venir"])]

        # Ambas frases matchean
        m_ok = SimpleNamespace(
            hook="h",
            verification=SimpleNamespace(
                first_phrase_in_audio="hola a todos",
                last_phrase_in_audio="gracias por venir",
            ),
        )
        r = verify_phrases_against_whisper(m_ok, words)
        assert r["first_ok"] and r["last_ok"] and not r["failed"]

        # Ninguna matchea → failed
        m_bad = SimpleNamespace(
            hook="h",
            verification=SimpleNamespace(
                first_phrase_in_audio="texto totalmente distinto",
                last_phrase_in_audio="otra cosa inventada",
            ),
        )
        r = verify_phrases_against_whisper(m_bad, words)
        assert not r["first_ok"] and not r["last_ok"] and r["failed"]

        # Solo una falla → no failed
        m_half = SimpleNamespace(
            hook="h",
            verification=SimpleNamespace(
                first_phrase_in_audio="hola a todos",
                last_phrase_in_audio="otra cosa inventada",
            ),
        )
        r = verify_phrases_against_whisper(m_half, words)
        assert r["first_ok"] and not r["last_ok"] and r["failed"]


class TestGoldenSetEval:
    """Fase 6: golden set con thresholds y videos habilitados."""

    def test_golden_set_has_thresholds(self):
        golden_path = Path(__file__).parent.parent / "eval" / "golden_set.json"
        data = json.loads(golden_path.read_text(encoding="utf-8"))
        thresholds = data["thresholds"]
        assert 0 < thresholds["duration_pass_rate_min"] <= 1
        assert 0 < thresholds["verification_pass_rate_min"] <= 1
        assert thresholds.get("judge_llm_delta_max", 0) > 0
        enabled = [v for v in data["videos"] if v.get("enabled", True)]
        assert len(enabled) >= 4


class TestWorkerLogging:
    def test_trace_context_in_format(self):
        from config.logging import TraceFormatter, bind_trace, reset_trace, get_trace_context

        token = bind_trace(job_id="abc123456789", moment_index=2, phase="clip")
        try:
            assert get_trace_context()["job_id"] == "abc123456789"
            record = logging.LogRecord(
                name="worker",
                level=logging.INFO,
                pathname=__file__,
                lineno=1,
                msg="hello",
                args=(),
                exc_info=None,
            )
            formatted = TraceFormatter("%(trace)s%(message)s").format(record)
            assert "job=abc12345" in formatted
            assert "m=2" in formatted
            assert "phase=clip" in formatted
            assert "hello" in formatted
        finally:
            reset_trace(token)
