"""
W8-E1 (docs/PLAN_CALIDAD.md §4 W8): prompt "idea completa" de la Pasada A.

get_selection_prompt no tenía tests dedicados — solo se ejercitaba
indirectamente vía mocks de select_moments en otros archivos. Estos tests
cubren el contenido del prompt en sí: que el ejemplo de JSON embebido siga
siendo JSON válido (el formato de salida NO cambió en E1), que las
instrucciones nuevas (citar desde el inicio de la oración, diversidad de
temas, preferencia 40-90s en podcast) estén presentes, y que las dos ramas
de categoría (podcast/business) sigan andando.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.moment_selector import get_selection_prompt  # noqa: E402


def _extract_json_example(prompt: str) -> dict:
    """Extrae y parsea el bloque de ejemplo bajo 'FORMATO JSON DE SALIDA'."""
    marker = "FORMATO JSON DE SALIDA (SOLO JSON, sin markdown):"
    idx = prompt.index(marker)
    tail = prompt[idx + len(marker):]
    start = tail.index("{")
    # Contar llaves para encontrar el cierre del objeto de nivel superior.
    depth = 0
    end = None
    for i, ch in enumerate(tail[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    assert end is not None, "no se encontró el cierre del bloque JSON en el prompt"
    return json.loads(tail[start:end])


class TestFormatoJSONSinCambios:
    """E1 es explícito: no cambia el formato JSON de salida."""

    def test_ejemplo_json_es_parseable_business(self):
        prompt = get_selection_prompt(600, 5, category="business")
        example = _extract_json_example(prompt)
        assert set(example.keys()) == {"video_title", "summary", "main_topics", "viral_moments"}
        moment = example["viral_moments"][0]
        assert set(moment.keys()) == {
            "start_time", "end_time", "clipping_reason", "hook", "viral_overlay",
            "emotional_trigger", "pillar_type", "category", "sentiment_detected",
            "scores", "verification",
        }
        assert set(moment["verification"].keys()) == {
            "first_phrase_in_audio", "last_phrase_in_audio", "narrative_goal",
        }
        assert set(moment["scores"].keys()) == {"hook", "retention", "shareability"}

    def test_ejemplo_json_es_parseable_podcast(self):
        prompt = get_selection_prompt(3600, 8, category="podcast")
        example = _extract_json_example(prompt)
        assert example["viral_moments"][0]["category"] == "podcast"

    def test_start_end_time_siguen_siendo_numeros_en_el_ejemplo(self):
        prompt = get_selection_prompt(600, 5)
        example = _extract_json_example(prompt)
        moment = example["viral_moments"][0]
        assert isinstance(moment["start_time"], (int, float))
        assert isinstance(moment["end_time"], (int, float))


class TestInstruccionesNuevasE1:
    def test_pide_citar_desde_el_inicio_de_la_oracion(self):
        prompt = get_selection_prompt(600, 5)
        assert "PRIMERA palabra de la oración" in prompt
        assert "conector" in prompt.lower()

    def test_pide_diversidad_de_temas(self):
        prompt = get_selection_prompt(600, 5)
        assert "temas distintos" in prompt
        assert "mismo minuto" in prompt

    def test_prefiere_40_90s_en_podcast(self):
        prompt = get_selection_prompt(3600, 8, category="podcast")
        assert "PREFERÍ 40-90" in prompt

    def test_remate_real_no_corte_arbitrario(self):
        prompt = get_selection_prompt(600, 5)
        assert "cierre real del remate" in prompt or "CIERRE real del remate" in prompt


class TestCategoriasExistentes:
    """No romper lo que ya andaba: las dos ramas de focus siguen presentes."""

    def test_business_tiene_su_bloque_de_priorizacion(self):
        prompt = get_selection_prompt(600, 5, category="business")
        assert "Contrarian truths" in prompt

    def test_podcast_tiene_su_bloque_de_priorizacion(self):
        prompt = get_selection_prompt(3600, 8, category="podcast")
        assert "ping-pong viral" in prompt

    def test_num_candidates_se_propaga(self):
        prompt = get_selection_prompt(600, 7, category="business")
        assert "Identifica los 7 MEJORES" in prompt
        assert "genera 7 candidatos" in prompt

    def test_idioma_se_propaga(self):
        prompt_es = get_selection_prompt(600, 5, language="es")
        prompt_none = get_selection_prompt(600, 5, language=None)
        # No debería tirar con ninguno de los dos; el contenido puede diferir.
        assert isinstance(prompt_es, str) and isinstance(prompt_none, str)


class TestNoRegresionesDeVersionesAnteriores:
    """Instrucciones de W2-B (tope 120s) y W4 (segundos absolutos) siguen ahí."""

    def test_tope_de_duracion_sigue_presente(self):
        prompt = get_selection_prompt(600, 5)
        assert "120" in prompt  # CLIP_MAX_DURATION_SEC

    def test_conversion_mmss_sigue_presente(self):
        prompt = get_selection_prompt(600, 5)
        assert "mm" in prompt and "60" in prompt

    def test_anti_alucinacion_sigue_obligatoria(self):
        prompt = get_selection_prompt(600, 5)
        assert "VERIFICACIÓN ANTI-ALUCINACIÓN" in prompt
        assert "oración completa" in prompt
