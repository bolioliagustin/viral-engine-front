"""Tests for services/usage_tracker.py"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def enable_persist(monkeypatch):
    monkeypatch.setenv("PERSIST_USAGE_EVENTS", "true")


@pytest.fixture(autouse=True)
def clear_rollups():
    from services import usage_tracker
    usage_tracker._job_rollups.clear()
    yield
    usage_tracker._job_rollups.clear()


class TestUsageTracker:
    def test_record_llm_without_job_context_is_noop(self):
        from services.usage_tracker import record_llm_usage

        response = MagicMock()
        response.usage = MagicMock(prompt_tokens=100, completion_tokens=50)
        record_llm_usage("analysis", "google/gemini-3.5-flash", response)
        from services import usage_tracker
        assert len(usage_tracker._job_rollups) == 0

    @patch("services.supabase_client.get_supabase")
    def test_record_llm_updates_rollup_and_inserts(self, mock_get_sb):
        from context.job_context import set_job_context, clear_job_context
        from services.usage_tracker import record_llm_usage, finalize_job_usage

        mock_sb = MagicMock()
        mock_get_sb.return_value = mock_sb
        mock_sb.table.return_value.insert.return_value.execute.return_value = None
        mock_sb.table.return_value.update.return_value.eq.return_value.execute.return_value = None

        set_job_context(job_id="job-123", user_id="user-456")
        response = MagicMock()
        response.usage = MagicMock(
            prompt_tokens=1000,
            completion_tokens=200,
            completion_tokens_details=None,
        )
        response.choices = []

        record_llm_usage("analysis", "google/gemini-3.5-flash", response)

        from services import usage_tracker
        rollup = usage_tracker._job_rollups["job-123"]
        assert rollup["event_count"] == 1
        assert rollup["total_input_tokens"] == 1000
        assert rollup["total_cost_usd"] > 0
        mock_sb.table.assert_called_with("job_usage_events")

        finalize_job_usage("job-123")
        mock_sb.table.assert_called_with("jobs")
        clear_job_context()

    @patch("services.supabase_client.get_supabase")
    def test_cache_hit_zero_cost_with_avoided(self, mock_get_sb):
        from context.job_context import set_job_context, clear_job_context
        from services.usage_tracker import record_cache_hit

        mock_get_sb.return_value = MagicMock()
        mock_get_sb.return_value.table.return_value.insert.return_value.execute.return_value = None

        set_job_context(job_id="job-cache")
        record_cache_hit("analysis", model="google/gemini-3.5-flash")

        from services import usage_tracker
        r = usage_tracker._job_rollups["job-cache"]
        assert r["cache_hits"] == 1
        assert r["cost_avoided_usd"] > 0
        assert r["total_cost_usd"] == 0
        clear_job_context()

    @patch("services.supabase_client.get_supabase")
    def test_finalize_skips_empty_rollup(self, mock_get_sb):
        from services.usage_tracker import finalize_job_usage

        finalize_job_usage("nonexistent-job")
        mock_get_sb.assert_not_called()
