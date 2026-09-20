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
