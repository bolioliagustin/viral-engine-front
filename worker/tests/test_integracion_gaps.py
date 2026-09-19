"""
INT-1 — Gaps de integración cerrados al juntar las siete ramas de la Fase 0
(docs/PLAN_CALIDAD.md §9) en integracion/fase-0.

Cada gap es un cambio que un agente dejó listo (schema, servicio) pero que
no llegaba a producción porque el archivo que lo enchufa era de otro agente.
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
    data = dict(
        start_time=100,
        end_time=140,
        hook="Un hook cualquiera",
        emotional_trigger="Curiosidad",
        title="Un título con gancho",
        description="Primera oración. Segunda oración invita a mirar.",
        hashtags=["#Uno", "#Dos"],
    )
    data.update(overrides)
    m = ViralMoment(**data)
    m.content_pieces.twitter_thread = "tweet 1\n\ntweet 2"
    return m


class TestGapATitleDescriptionHashtags:
    """(a) main.py → save_content_result: title/description/hashtags (W10)."""

    def test_deliver_moment_pasa_title_description_hashtags(self):
        moment = _moment()
        prepared = main._PreparedClip(ok=False)  # sin video local: salta render/Pasada B

        with patch.object(main, "save_content_result") as mock_save:
            main._deliver_moment(
                moment, 1, prepared,
                job_id="job-1", video_id="vid-1", job_tone="profesional",
                user_name="Creador", user_title="Experto", transcript={"segments": []},
            )

        assert mock_save.called
        kwargs = mock_save.call_args.kwargs
        assert kwargs["title"] == "Un título con gancho"
        assert kwargs["description"] == "Primera oración. Segunda oración invita a mirar."
        assert kwargs["hashtags"] == ["#Uno", "#Dos"]

    def test_deliver_moment_sin_copy_por_clip_pasa_none(self):
        """Si la Pasada A/B no llenó title/description/hashtags, no rompe."""
        moment = _moment(title=None, description=None, hashtags=None)
        prepared = main._PreparedClip(ok=False)

        with patch.object(main, "save_content_result") as mock_save:
            main._deliver_moment(
                moment, 1, prepared,
                job_id="job-1", video_id="vid-1", job_tone="profesional",
                user_name="Creador", user_title="Experto", transcript={"segments": []},
            )

        kwargs = mock_save.call_args.kwargs
        assert kwargs["title"] is None
        assert kwargs["description"] is None
        assert kwargs["hashtags"] is None


class TestGapBSubtitleStyleDefault:
    """(b) main.py → generate_clip: subtitle_style default tiktok_viral_v2 (W11)."""

    def test_deliver_moment_usa_tiktok_viral_v2_por_defecto(self, tmp_path):
        moment = _moment(keywords=["sarampión", "R0"])
        precut = tmp_path / "precut.mp4"
        precut.write_bytes(b"x")
        prepared = main._PreparedClip(
            ok=True, precut_path=str(precut), clip_duration=30.0,
            clip_text_final=None,  # sin Pasada B: solo interesa el render
        )

        fake_gen_result = MagicMock(total_time_sec=1.0, final=MagicMock(size_mb=1.0))
        with patch.object(main, "generate_clip", return_value=fake_gen_result) as mock_gen, \
             patch.object(main, "upload_clip_to_storage", return_value="https://r2/clip.mp4"), \
             patch.object(main, "save_content_result"):
            main._deliver_moment(
                moment, 1, prepared,
                job_id="job-1", video_id="vid-1", job_tone="profesional",
                user_name="Creador", user_title="Experto", transcript={"segments": []},
            )

        assert mock_gen.called
        kwargs = mock_gen.call_args.kwargs
        assert kwargs["subtitle_style"] == "tiktok_viral_v2"
        assert kwargs["keywords"] == ["sarampión", "R0"]

    def test_subtitle_style_default_configurable_por_env(self, monkeypatch):
        monkeypatch.setenv("SUBTITLE_STYLE_DEFAULT", "tiktok_viral")
        import importlib
        importlib.reload(main)
        try:
            assert main.SUBTITLE_STYLE_DEFAULT == "tiktok_viral"
        finally:
            monkeypatch.delenv("SUBTITLE_STYLE_DEFAULT", raising=False)
            importlib.reload(main)
            assert main.SUBTITLE_STYLE_DEFAULT == "tiktok_viral_v2"


class TestGapEPromptVersion:
    """(e) PROMPT_VERSION sube a v7 una sola vez (W4 agregó una línea al
    prompt de la Pasada A sin subirla, por acuerdo con el coordinador)."""

    def test_prompt_version_es_v7(self):
        from services.analysis_cache import PROMPT_VERSION
        assert PROMPT_VERSION == "v7"


class TestGapDReframeModeDefaultOff:
    """(d) generate_clip: el reencuadre de W5 (services/reframe.py) se
    dispara solo con REFRAME_MODE=auto; no hace falta ningún cambio en
    main.py (lee video_path, que ya se pasaba). Con la variable sin
    definir, el comportamiento tiene que ser IDÉNTICO al de antes de W5:
    layout None -> filtro "fit" de siempre, sin llamar a
    plan_reframe_for_clip (docs/PROYECTO.md §11 y §5.5 punto 9)."""

    def test_reframe_mode_sin_definir_no_analiza_layout(self, monkeypatch, tmp_path):
        from services import clip_generator

        monkeypatch.delenv("REFRAME_MODE", raising=False)
        video = tmp_path / "in.mp4"
        video.write_bytes(b"x")

        with patch.object(clip_generator, "plan_reframe_for_clip") as mock_plan:
            try:
                clip_generator.generate_clip(
                    video_path=str(video), start_sec=0.0, end_sec=5.0,
                    output_path=str(tmp_path / "out.mp4"), segments=[],
                )
            except Exception:
                pass  # el render en sí falla sin ffmpeg real; solo interesa si se llamó al análisis

        mock_plan.assert_not_called()

    def test_reframe_mode_off_explicito_tampoco_analiza(self, monkeypatch, tmp_path):
        from services import clip_generator

        monkeypatch.setenv("REFRAME_MODE", "off")
        video = tmp_path / "in.mp4"
        video.write_bytes(b"x")

        with patch.object(clip_generator, "plan_reframe_for_clip") as mock_plan:
            try:
                clip_generator.generate_clip(
                    video_path=str(video), start_sec=0.0, end_sec=5.0,
                    output_path=str(tmp_path / "out.mp4"), segments=[],
                )
            except Exception:
                pass

        mock_plan.assert_not_called()
        monkeypatch.delenv("REFRAME_MODE", raising=False)

    def test_reframe_mode_auto_si_analiza(self, monkeypatch, tmp_path):
        """Control positivo: con REFRAME_MODE=auto sí se intenta analizar
        (confirma que el test anterior no pasa "por accidente")."""
        from services import clip_generator
        from services.reframe import LayoutPlan

        monkeypatch.setenv("REFRAME_MODE", "auto")
        video = tmp_path / "in.mp4"
        video.write_bytes(b"x")

        with patch.object(
            clip_generator, "plan_reframe_for_clip",
            return_value=LayoutPlan(name="fit", crops=[]),
        ) as mock_plan:
            try:
                clip_generator.generate_clip(
                    video_path=str(video), start_sec=0.0, end_sec=5.0,
                    output_path=str(tmp_path / "out.mp4"), segments=[],
                )
            except Exception:
                pass

        mock_plan.assert_called_once()
        monkeypatch.delenv("REFRAME_MODE", raising=False)


class TestGapCClipEditFallback:
    """(c) clip_edit_processor.py: fallback de subtitle_style a tiktok_viral_v2 (W11)."""

    def test_fallback_sin_estilo_especificado(self):
        from services.clip_edit_processor import _resolve_subtitle_style
        assert _resolve_subtitle_style({"id": "e1"}) == "tiktok_viral_v2"

    def test_fallback_respeta_env_override(self, monkeypatch):
        from services.clip_edit_processor import _resolve_subtitle_style
        monkeypatch.setenv("SUBTITLE_STYLE_DEFAULT", "clean")
        assert _resolve_subtitle_style({}) == "clean"
        monkeypatch.delenv("SUBTITLE_STYLE_DEFAULT", raising=False)

    def test_edit_con_estilo_explicito_no_lo_pisa(self):
        from services.clip_edit_processor import _resolve_subtitle_style
        assert _resolve_subtitle_style({"subtitle_style": "podcast"}) == "podcast"
