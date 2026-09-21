"""
W10 — Copy por clip y capa de presentación del score (docs/PLAN_CALIDAD.md
§9 Fase 0; motivación docs/ANALISIS_OPUS_CLIP.md §2.4).

Cubre la parte del worker: generate_moment_copy_full devuelve
title/description/hashtags con el modelo mockeado, y save_content_result
inserta esas columnas (y degrada con gracia si no existen todavía).
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.schemas import ViralMoment  # noqa: E402
from services.processor import _clean_hashtags, generate_moment_copy_full  # noqa: E402


def _moment() -> ViralMoment:
    return ViralMoment(
        start_time=0,
        end_time=30,
        hook="Borrador",
        viral_overlay="BORRADOR",
        emotional_trigger="curiosidad",
    )


def _llm_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.usage = None
    resp.choices = [MagicMock(message=MagicMock(content=json.dumps(payload)))]
    return resp


_FULL_PAYLOAD = {
    "twitter_thread": "\n\n".join([f"Tweet {i} sobre el sarampión y el R0 de contagio real." * 4 for i in range(7)]),
    "linkedin_post": "El sarampión es más contagioso que el COVID. " * 20,
    "tiktok_caption": "El dato que nadie te cuenta sobre contagios #salud",
    "hook": "El sarampión es más contagioso que el COVID, y la ciencia lo explica con el R0.",
    "viral_overlay": "MÁS CONTAGIOSO QUE COVID",
    "title": "Sarampión vs COVID: ¡La verdad de la inmunidad de grupo!",
    "description": "Comparamos la contagiosidad del sarampión y el COVID con el número R0. Mirá el clip para entender por qué la inmunidad de grupo cambia según la enfermedad.",
    "hashtags": [
        "#Sarampion", "#Covid", "#InmunidadDeGrupo", "#Salud", "#Virus",
        "#Contagio", "#Ciencia", "#Epidemiologia", "#Vacunas", "#Divulgacion",
    ],
}

CLIP_TEXT = "El sarampión es mucho más contagioso que el COVID. Su R0 es altísimo."


class TestCopyPorClip:
    def test_pasada_b_devuelve_title_description_hashtags(self):
        moment = _moment()
        client = MagicMock()
        client.chat.completions.create.return_value = _llm_response(_FULL_PAYLOAD)

        ok = generate_moment_copy_full(moment, CLIP_TEXT, client=client)

        assert ok is True
        assert moment.title == "Sarampión vs COVID: ¡La verdad de la inmunidad de grupo!"
        assert moment.description.startswith("Comparamos la contagiosidad")
        assert moment.hashtags == [
            "#Sarampion", "#Covid", "#InmunidadDeGrupo", "#Salud", "#Virus",
            "#Contagio", "#Ciencia", "#Epidemiologia", "#Vacunas", "#Divulgacion",
        ]

    def test_title_se_trunca_a_60_caracteres(self):
        moment = _moment()
        payload = {**_FULL_PAYLOAD, "title": "T" * 80}
        client = MagicMock()
        client.chat.completions.create.return_value = _llm_response(payload)

        generate_moment_copy_full(moment, CLIP_TEXT, client=client)

        assert len(moment.title) == 60

    def test_sin_title_description_hashtags_no_rompe(self):
        # Modelo viejo / respuesta parcial: el moment no se rompe, solo no
        # se llenan esos campos (quedan None, como antes de W10).
        moment = _moment()
        payload = {k: v for k, v in _FULL_PAYLOAD.items() if k not in ("title", "description", "hashtags")}
        client = MagicMock()
        client.chat.completions.create.return_value = _llm_response(payload)

        ok = generate_moment_copy_full(moment, CLIP_TEXT, client=client)

        assert ok is True
        assert moment.title is None
        assert moment.description is None
        assert moment.hashtags is None

    def test_clip_text_vacio_no_llama_al_llm(self):
        moment = _moment()
        client = MagicMock()
        ok = generate_moment_copy_full(moment, "   ", client=client)
        assert ok is False
        assert client.chat.completions.create.call_count == 0
        assert moment.title is None


class TestCleanHashtags:
    def test_normaliza_acentos_y_agrega_numeral(self):
        assert _clean_hashtags(["Salúd", "#Contagión"]) == ["#Salud", "#Contagion"]

    def test_descarta_genericos_vacios(self):
        out = _clean_hashtags(["#Viral", "#Fyp", "#ParaTi", "#Sarampion"])
        assert out == ["#Sarampion"]

    def test_descarta_duplicados_case_insensitive(self):
        out = _clean_hashtags(["Salud", "salud", "SALUD"])
        assert out == ["#Salud"]

    def test_tope_10(self):
        tags = [f"Tag{i}" for i in range(15)]
        assert len(_clean_hashtags(tags)) == 10

    def test_no_es_lista_devuelve_vacio(self):
        assert _clean_hashtags("no es lista") == []
        assert _clean_hashtags(None) == []

    def test_descarta_vacios_y_no_strings(self):
        assert _clean_hashtags(["", "  ", 123, None, "Real"]) == ["#Real"]


class TestSaveContentResultCopyPorClip:
    """save_content_result inserta las columnas nuevas y degrada con
    gracia si la migración copy_por_clip todavía no corrió (mismo patrón
    que score_llm/score_judge — Fase 4)."""

    def _mock_sb(self):
        sb = MagicMock()
        sb.table.return_value.insert.return_value.execute.return_value = None
        return sb

    def test_insert_incluye_title_description_hashtags(self, monkeypatch):
        monkeypatch.delenv("EVAL_DRY_RUN", raising=False)
        from services import supabase_client as sbc

        sb = self._mock_sb()
        with patch("services.supabase_client.get_supabase", return_value=sb), \
             patch("services.supabase_client._require_supabase", return_value=sb):
            sbc.save_content_result(
                job_id="job-1",
                content_type="twitter_thread",
                content="x",
                title="Un título",
                description="Dos oraciones.",
                hashtags=["#Uno", "#Dos"],
            )

        insert_call = sb.table.return_value.insert
        payload = insert_call.call_args.args[0]
        assert payload["title"] == "Un título"
        assert payload["description"] == "Dos oraciones."
        assert payload["hashtags"] == ["#Uno", "#Dos"]

    def test_degrada_si_las_columnas_no_existen_todavia(self, monkeypatch):
        monkeypatch.delenv("EVAL_DRY_RUN", raising=False)
        from services import supabase_client as sbc

        sb = self._mock_sb()
        call_count = {"n": 0}

        def _execute_side_effect():
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception('column "title" of relation "content_results" does not exist (PGRST204)')
            return MagicMock()

        sb.table.return_value.insert.return_value.execute.side_effect = _execute_side_effect

        with patch("services.supabase_client.get_supabase", return_value=sb), \
             patch("services.supabase_client._require_supabase", return_value=sb):
            result_id = sbc.save_content_result(
                job_id="job-1",
                content_type="twitter_thread",
                content="x",
                title="Un título",
                description="Dos oraciones.",
                hashtags=["#Uno"],
            )

        assert isinstance(result_id, str)
        assert call_count["n"] == 2
        second_payload = sb.table.return_value.insert.call_args.args[0]
        assert "title" not in second_payload
        assert "description" not in second_payload
        assert "hashtags" not in second_payload

    def test_dry_run_acumula_title_description_hashtags(self, monkeypatch):
        monkeypatch.setenv("EVAL_DRY_RUN", "1")
        from services import supabase_client as sbc
        sbc.reset_dry_run()
        try:
            sbc.save_content_result(
                job_id="job-1",
                content_type="twitter_thread",
                content="x",
                title="Un título",
                description="Dos oraciones.",
                hashtags=["#Uno"],
            )
            assert sbc.DRY_RUN_RESULTS[-1]["title"] == "Un título"
            assert sbc.DRY_RUN_RESULTS[-1]["hashtags"] == ["#Uno"]
        finally:
            sbc.reset_dry_run()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
