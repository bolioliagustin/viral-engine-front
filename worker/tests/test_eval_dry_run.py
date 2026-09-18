"""
Modo dry-run (EVAL_DRY_RUN=1) del pipeline, usado por el tier e2e del golden set.

Con la variable: nada se escribe en Supabase ni se sube a R2; los resultados
quedan en memoria. Sin la variable: comportamiento idéntico al de siempre.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def dry_run(monkeypatch):
    monkeypatch.setenv("EVAL_DRY_RUN", "1")
    from services import supabase_client as sbc
    from services import usage_tracker as ut
    sbc.reset_dry_run()
    ut.DRY_RUN_ROLLUPS.clear()
    ut._job_rollups.clear()
    yield
    sbc.reset_dry_run()
    ut.DRY_RUN_ROLLUPS.clear()
    ut._job_rollups.clear()


@pytest.fixture
def no_dry_run(monkeypatch):
    monkeypatch.delenv("EVAL_DRY_RUN", raising=False)
    from services import usage_tracker as ut
    ut._job_rollups.clear()
    yield
    ut._job_rollups.clear()


def _mock_sb():
    sb = MagicMock()
    sb.table.return_value.update.return_value.eq.return_value.execute.return_value = None
    sb.table.return_value.insert.return_value.execute.return_value = None
    sb.rpc.return_value.execute.return_value = MagicMock(
        data=[{"success": True, "new_credits": 4}]
    )
    return sb


class TestDryRunNoPersiste:
    """Con EVAL_DRY_RUN=1 el cliente Supabase no recibe ni insert ni update ni rpc."""

    def test_jobs_no_se_escriben_y_quedan_en_memoria(self, dry_run):
        from services import supabase_client as sbc

        sb = _mock_sb()
        with patch("services.supabase_client.get_supabase", return_value=sb):
            sbc.update_job_status("job-1", "processing", video_title="Título")
            sbc.update_job_progress("job-1", current_step="analyzing", progress_percentage=50)
            sbc.update_job_status("job-1", "completed")
            sbc.update_job_error("job-2", "boom")

        assert not sb.table.called
        assert sbc.DRY_RUN_JOBS["job-1"]["status"] == "completed"
        assert sbc.DRY_RUN_JOBS["job-1"]["video_title"] == "Título"
        assert sbc.DRY_RUN_JOBS["job-1"]["current_step"] == "analyzing"
        assert sbc.DRY_RUN_JOBS["job-1"]["progress_percentage"] == 50
        assert sbc.DRY_RUN_JOBS["job-2"] == {
            "status": "failed", "error_message": "boom",
            "current_step": None, "progress_percentage": None, "video_title": None,
        }

    def test_content_results_se_acumulan_sin_insert(self, dry_run):
        from services import supabase_client as sbc

        sb = _mock_sb()
        with patch("services.supabase_client.get_supabase", return_value=sb):
            rid = sbc.save_content_result(
                job_id="job-1",
                content_type="twitter_thread",
                content="1/ hola",
                clip_url="dryrun://job-1/1.mp4",
                start_time=10,
                end_time=40,
                moment_index=1,
                score_judge={"hook": 5, "retention": 4, "shareability": 6, "reasoning": "meh"},
                whisper_words={"words": [{"word": "Hola", "start": 0, "end": 0.3}],
                               "duration_sec": 29.5, "snap_trim_start": 0.5},
                clip_quality_issues=["late_hook"],
                verification_failed=True,
                words_per_sec=2.5,
                sub_coverage=0.95,
            )

        assert not sb.table.called
        assert isinstance(rid, str) and len(rid) == 36
        assert len(sbc.DRY_RUN_RESULTS) == 1
        row = sbc.DRY_RUN_RESULTS[0]
        assert row["id"] == rid
        assert row["type"] == "twitter_thread"
        assert row["moment_index"] == 1
        # Los valores quedan sin serializar (el eval los lee como dicts/listas)
        assert row["score_judge"]["reasoning"] == "meh"
        assert row["whisper_words"]["duration_sec"] == 29.5
        assert row["clip_quality_issues"] == ["late_hook"]
        assert row["verification_failed"] is True

    def test_uploads_devuelven_url_ficticia_sin_tocar_r2(self, dry_run):
        from services import supabase_client as sbc

        with patch("services.storage_client.upload_file") as upload:
            url = sbc.upload_clip_to_storage("/tmp/x.mp4", "job-1", 2)
            raw = sbc.upload_raw_clip_to_storage("/tmp/x.mp4", "job-1", 2)

        assert not upload.called
        assert url == "dryrun://job-1/2.mp4"
        assert raw == "dryrun://job-1/raw_2.mp4"
        assert "youtube.com" not in url  # el eval lo cuenta como renderizado

    def test_credito_no_se_descuenta(self, dry_run):
        from services import supabase_client as sbc

        sb = _mock_sb()
        with patch("services.supabase_client.get_supabase", return_value=sb):
            assert sbc.deduct_credit("user-1", "job-1", "https://youtu.be/x") is True
        assert not sb.rpc.called

    def test_usage_events_no_se_insertan_pero_el_rollup_queda(self, dry_run):
        from context.job_context import set_job_context, clear_job_context
        from services import usage_tracker as ut

        sb = _mock_sb()
        with patch("services.supabase_client.get_supabase", return_value=sb):
            set_job_context(job_id="job-1", user_id=None)
            try:
                resp = MagicMock()
                resp.usage = MagicMock(prompt_tokens=1000, completion_tokens=200,
                                       completion_tokens_details=None)
                resp.choices = []
                ut.record_llm_usage("judge", "openai/gpt-5.4-nano", resp)
                ut.record_whisper_usage("groq", "whisper-large-v3-turbo", 30.0)
            finally:
                clear_job_context()
            rollup = ut.finalize_job_usage("job-1")

        assert not sb.table.called
        assert rollup is not None
        assert rollup["event_count"] == 2
        assert rollup["total_cost_usd"] > 0
        assert rollup["by_task"]["whisper"] > 0
        assert ut.DRY_RUN_ROLLUPS["job-1"] is rollup
        assert "job-1" not in ut._job_rollups


class TestSinDryRunComportamientoIntacto:
    """Sin EVAL_DRY_RUN todo sigue escribiendo como antes."""

    def test_is_dry_run_false_por_defecto(self, no_dry_run):
        from services.supabase_client import is_dry_run
        assert is_dry_run() is False

    def test_jobs_y_content_results_se_escriben(self, no_dry_run):
        from services import supabase_client as sbc

        sb = _mock_sb()
        with patch("services.supabase_client.get_supabase", return_value=sb):
            sbc.update_job_status("job-1", "processing")
            sbc.update_job_progress("job-1", current_step="analyzing", progress_percentage=50)
            sbc.update_job_error("job-1", "boom")
            sbc.save_content_result(job_id="job-1", content_type="twitter_thread", content="x")

        tables = [c.args[0] for c in sb.table.call_args_list]
        assert tables.count("jobs") == 3
        assert tables.count("content_results") == 1
        sb.table.return_value.insert.assert_called_once()
        assert sbc.DRY_RUN_RESULTS == []
        assert sbc.DRY_RUN_JOBS == {}

    def test_uploads_van_a_r2(self, no_dry_run):
        from services import supabase_client as sbc

        with patch("services.storage_client.upload_file", return_value="https://r2/x.mp4") as upload:
            assert sbc.upload_clip_to_storage("/tmp/x.mp4", "job-1", 1) == "https://r2/x.mp4"
            assert sbc.upload_raw_clip_to_storage("/tmp/x.mp4", "job-1", 1) == "https://r2/x.mp4"
        assert upload.call_count == 2

    def test_credito_se_descuenta_via_rpc(self, no_dry_run):
        from services import supabase_client as sbc

        sb = _mock_sb()
        with patch("services.supabase_client.get_supabase", return_value=sb):
            assert sbc.deduct_credit("user-1", "job-1", "https://youtu.be/x") is True
        sb.rpc.assert_called_once()

    def test_usage_events_y_summary_se_persisten(self, no_dry_run, monkeypatch):
        monkeypatch.setenv("PERSIST_USAGE_EVENTS", "true")
        from context.job_context import set_job_context, clear_job_context
        from services import usage_tracker as ut

        sb = _mock_sb()
        with patch("services.supabase_client.get_supabase", return_value=sb):
            set_job_context(job_id="job-1", user_id="u")
            try:
                ut.record_whisper_usage("groq", "whisper-large-v3-turbo", 30.0)
            finally:
                clear_job_context()
            rollup = ut.finalize_job_usage("job-1")

        tables = [c.args[0] for c in sb.table.call_args_list]
        assert "job_usage_events" in tables
        assert "jobs" in tables
        assert rollup["event_count"] == 1
        assert ut.DRY_RUN_ROLLUPS == {}
