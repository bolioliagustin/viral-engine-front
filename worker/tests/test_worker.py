"""
C2b: Worker Unit Tests
Tests core worker functions: env validation, cleanup, recovery, and processing pipeline.
"""
import os
import sys
import json
import time
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
            {"word": "mundo", "start": 3.5, "end": 9.5},
        ]
        start, end = snap_trim_bounds(words, clip_duration=10.0)
        assert start == pytest.approx(2.7)
        assert end == 10.0

    def test_snap_trim_bounds_trims_trailing_silence(self):
        from services.clip_generator import snap_trim_bounds
        words = [{"word": "fin", "start": 1.0, "end": 3.0}]
        start, end = snap_trim_bounds(words, clip_duration=10.0)
        assert start == 0.0
        assert end == pytest.approx(3.5)

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
