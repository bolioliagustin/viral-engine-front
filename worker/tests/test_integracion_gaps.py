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
