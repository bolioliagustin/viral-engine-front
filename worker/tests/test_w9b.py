"""
W9-B — La mitad worker de la galería (docs/PLAN_CALIDAD.md §9 W9,
docs/adr/0008): preview liviano por clip, HD a pedido real, y un job sin
clips reales tiene que fallar de verdad (para que F1 devuelva el crédito).
"""
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "development")

import main  # noqa: E402
from models.schemas import ViralMoment  # noqa: E402


def _moment(**overrides) -> ViralMoment:
    data = dict(start_time=100, end_time=140, hook="Un hook cualquiera", emotional_trigger="Curiosidad")
    data.update(overrides)
    m = ViralMoment(**data)
    m.content_pieces.twitter_thread = "tweet 1\n\ntweet 2"
    return m


class TestPreviewLiviano:
    """(3) generate_clip se llama con las dimensiones/crf de preview, y
    preview_url viaja a save_content_result con el mismo valor que clip_url."""

    def test_deliver_moment_usa_dimensiones_de_preview(self, tmp_path):
        moment = _moment()
        precut = tmp_path / "precut.mp4"
        precut.write_bytes(b"x")
        prepared = main._PreparedClip(
            ok=True, precut_path=str(precut), clip_duration=30.0, clip_text_final=None,
        )
        fake_gen_result = MagicMock(total_time_sec=1.0, final=MagicMock(size_mb=1.0))

        with patch.object(main, "generate_clip", return_value=fake_gen_result) as mock_gen, \
             patch.object(main, "upload_clip_to_storage", return_value="https://r2/preview.mp4"), \
             patch.object(main, "save_content_result") as mock_save:
            main._deliver_moment(
                moment, 1, prepared,
                job_id="job-1", video_id="vid-1", job_tone="profesional",
                user_name="Creador", user_title="Experto", transcript={"segments": []},
            )

        gen_kwargs = mock_gen.call_args.kwargs
        assert gen_kwargs["target_width"] == main.PREVIEW_WIDTH
        assert gen_kwargs["target_height"] == main.PREVIEW_HEIGHT
        assert gen_kwargs["crf"] == main.PREVIEW_CRF

        save_kwargs = mock_save.call_args.kwargs
        assert save_kwargs["clip_url"] == "https://r2/preview.mp4"
        assert save_kwargs["preview_url"] == "https://r2/preview.mp4"

    def test_deliver_moment_sin_render_no_pisa_preview_url(self):
        """Fallback a link de YouTube (sin video local): preview_url None,
        no un link de YouTube disfrazado de preview."""
        moment = _moment()
        prepared = main._PreparedClip(ok=False)

        with patch.object(main, "save_content_result") as mock_save:
            main._deliver_moment(
                moment, 1, prepared,
                job_id="job-1", video_id="vid-1", job_tone="profesional",
                user_name="Creador", user_title="Experto", transcript={"segments": []},
            )

        save_kwargs = mock_save.call_args.kwargs
        assert save_kwargs["clip_url"].startswith("https://www.youtube.com/")
        assert save_kwargs["preview_url"] is None


class TestSavePreviewUrlColumn:
    """save_content_result acepta y persiste preview_url (services/supabase_client.py)."""

    def test_dry_run_incluye_preview_url(self):
        from services.supabase_client import save_content_result, DRY_RUN_RESULTS
        DRY_RUN_RESULTS.clear()
        with patch("services.supabase_client.is_dry_run", return_value=True):
            save_content_result(
                job_id="job-1", content_type="twitter_thread", content="x",
                preview_url="https://r2/preview.mp4",
            )
        assert DRY_RUN_RESULTS[-1]["preview_url"] == "https://r2/preview.mp4"


class TestHdUpgradeIdempotente:
    """(4) clip_edit_processor: un hd_upgrade con rendered_clip_url ya
    seteado no vuelve a descargar/renderizar."""

    def test_hd_upgrade_ya_completado_no_re_renderiza(self):
        from services import clip_edit_processor as cep

        edit = {
            "id": "edit-1",
            "content_result_id": "cr-1",
            "edit_type": "hd_upgrade",
            "rendered_clip_url": "https://r2/hd_ya_listo.mp4",
        }
        with patch.object(cep, "mark_clip_edit_completed") as mock_mark, \
             patch.object(cep, "_process_clip_edit_inner") as mock_inner:
            cep.process_clip_edit(edit)

        mock_mark.assert_called_once_with("edit-1", "https://r2/hd_ya_listo.mp4")
        mock_inner.assert_not_called()

    def test_hd_upgrade_sin_rendered_clip_url_procesa_normal(self):
        from services import clip_edit_processor as cep

        edit = {
            "id": "edit-2",
            "content_result_id": "cr-2",
            "edit_type": "hd_upgrade",
            "rendered_clip_url": None,
        }
        with patch.object(cep, "mark_clip_edit_completed") as mock_mark, \
             patch.object(cep, "_process_clip_edit_inner") as mock_inner:
            cep.process_clip_edit(edit)

        mock_mark.assert_not_called()
        mock_inner.assert_called_once()

    def test_edit_de_estilo_normal_no_dispara_el_atajo(self):
        from services import clip_edit_processor as cep

        edit = {
            "id": "edit-3",
            "content_result_id": "cr-3",
            "edit_type": "style",
            "rendered_clip_url": "https://r2/algo-viejo.mp4",
        }
        with patch.object(cep, "mark_clip_edit_completed") as mock_mark, \
             patch.object(cep, "_process_clip_edit_inner") as mock_inner:
            cep.process_clip_edit(edit)

        mock_mark.assert_not_called()
        mock_inner.assert_called_once()


class TestJobSinClipsFalla:
    """(5) 0 clips entregados sin excepción → job failed (CONTEXT.md: "Job")."""

    def test_cero_clips_marca_failed_y_no_descuenta_credito(self):
        with patch.object(main, "update_job_error") as mock_error, \
             patch.object(main, "update_job_status") as mock_status, \
             patch("services.supabase_client.deduct_credit") as mock_deduct:
            main._finalize_job_outcome(
                job_id="job-1", video_url="https://youtube.com/watch?v=x",
                user_id="user-1", supabase=MagicMock(),
                clips_rendered_count=0, total_moments=3,
            )

        mock_error.assert_called_once_with("job-1", "sin clips viables")
        mock_status.assert_not_called()
        mock_deduct.assert_not_called()

    def test_al_menos_un_clip_marca_completed_y_descuenta_credito(self):
        with patch.object(main, "update_job_error") as mock_error, \
             patch.object(main, "update_job_status") as mock_status, \
             patch("services.supabase_client.deduct_credit") as mock_deduct:
            main._finalize_job_outcome(
                job_id="job-1", video_url="https://youtube.com/watch?v=x",
                user_id="user-1", supabase=MagicMock(),
                clips_rendered_count=2, total_moments=3,
            )

        mock_status.assert_called_once_with("job-1", "completed")
        mock_error.assert_not_called()
        mock_deduct.assert_called_once_with("user-1", "job-1", "https://youtube.com/watch?v=x")

    def test_sin_user_id_no_intenta_descontar(self):
        with patch.object(main, "update_job_status"), \
             patch("services.supabase_client.deduct_credit") as mock_deduct:
            main._finalize_job_outcome(
                job_id="job-1", video_url="https://youtube.com/watch?v=x",
                user_id=None, supabase=MagicMock(),
                clips_rendered_count=1, total_moments=1,
            )
        mock_deduct.assert_not_called()


class TestSentryDsnDesdeEnv:
    """(6) Sin SENTRY_DSN_WORKER, Sentry no se inicializa (sin DSN hardcodeado)."""

    def test_sin_dsn_no_llama_a_init(self, monkeypatch):
        monkeypatch.delenv("SENTRY_DSN_WORKER", raising=False)
        with patch("sentry_sdk.init") as mock_init:
            import importlib
            importlib.reload(main)
        mock_init.assert_not_called()
        importlib.reload(main)  # restaurar estado normal del módulo para el resto de la suite

    def test_con_dsn_llama_a_init_con_ese_dsn(self, monkeypatch):
        monkeypatch.setenv("SENTRY_DSN_WORKER", "https://fake@example.ingest.sentry.io/1")
        with patch("sentry_sdk.init") as mock_init:
            import importlib
            importlib.reload(main)
        mock_init.assert_called_once()
        assert mock_init.call_args.kwargs["dsn"] == "https://fake@example.ingest.sentry.io/1"
        monkeypatch.delenv("SENTRY_DSN_WORKER", raising=False)
        importlib.reload(main)


class TestComputeProgressPercentage:
    """Contrato de progreso acordado con P1 (pantalla de progreso nueva,
    también sobre integracion/fase-0): función pura, sin I/O — main.py la
    llama, pero no necesita mockear nada para testearla."""

    def test_transcribing_es_piso_cero(self):
        assert main.compute_progress_percentage("transcribing") == 0

    def test_classifying_y_analyzing_son_15(self):
        assert main.compute_progress_percentage("classifying") == 15
        assert main.compute_progress_percentage("analyzing") == 15

    def test_evaluating_sin_total_es_el_piso(self):
        assert main.compute_progress_percentage("evaluating") == 25

    def test_evaluating_interpola_linealmente(self):
        assert main.compute_progress_percentage(
            "evaluating", candidate_index=0, candidates_total=10,
        ) == 25
        assert main.compute_progress_percentage(
            "evaluating", candidate_index=5, candidates_total=10,
        ) == 48  # 25 + 0.5*(70-25) = 47.5 -> round() bankers: 48
        assert main.compute_progress_percentage(
            "evaluating", candidate_index=10, candidates_total=10,
        ) == 70

    def test_evaluating_nunca_pasa_del_techo_aunque_el_index_sea_mayor_al_total(self):
        assert main.compute_progress_percentage(
            "evaluating", candidate_index=99, candidates_total=10,
        ) == 70

    def test_ranking_es_70(self):
        assert main.compute_progress_percentage("ranking") == 70

    def test_delivering_interpola_entre_70_y_98(self):
        assert main.compute_progress_percentage(
            "delivering", delivered_index=0, delivered_total=4,
        ) == 70
        assert main.compute_progress_percentage(
            "delivering", delivered_index=4, delivered_total=4,
        ) == 98
        assert main.compute_progress_percentage(
            "delivering", delivered_index=2, delivered_total=4,
        ) == 84

    def test_finalizing_es_100(self):
        assert main.compute_progress_percentage("finalizing") == 100

    def test_fase_desconocida_explota_en_vez_de_devolver_cualquier_cosa(self):
        import pytest
        with pytest.raises(ValueError):
            main.compute_progress_percentage("descargando")


class TestUpdateJobProgressDetail:
    """update_job_progress acepta progress_detail y degrada con gracia si
    la columna todavía no existe (la agrega P1 en su migración)."""

    def test_dry_run_guarda_progress_detail(self):
        from services.supabase_client import update_job_progress, DRY_RUN_JOBS
        DRY_RUN_JOBS.clear()
        with patch("services.supabase_client.is_dry_run", return_value=True):
            update_job_progress(
                "job-1", current_step="evaluating", progress_percentage=40,
                progress_detail={"current": 4, "total": 10, "message": "x", "clips_ready": 0},
            )
        job = DRY_RUN_JOBS["job-1"]
        assert job["current_step"] == "evaluating"
        assert job["progress_percentage"] == 40
        assert job["progress_detail"]["clips_ready"] == 0

    def test_degrada_con_gracia_si_progress_detail_no_existe_como_columna(self):
        from services.supabase_client import update_job_progress

        calls = []

        class _FakeTable:
            def update(self, data):
                calls.append(dict(data))
                return self

            def eq(self, *a, **k):
                return self

            def execute(self):
                if len(calls) == 1:
                    raise RuntimeError("column jobs.progress_detail does not exist (PGRST204)")
                return MagicMock()

        fake_supabase = MagicMock()
        fake_supabase.table.return_value = _FakeTable()

        with patch("services.supabase_client.is_dry_run", return_value=False), \
             patch("services.supabase_client._require_supabase", return_value=fake_supabase):
            update_job_progress(
                "job-1", current_step="evaluating", progress_percentage=40,
                progress_detail={"current": 4, "total": 10},
            )

        assert len(calls) == 2
        assert "progress_detail" in calls[0]
        assert "progress_detail" not in calls[1]
        assert calls[1]["current_step"] == "evaluating"
