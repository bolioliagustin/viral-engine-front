"""
W13 (docs/PLAN_CALIDAD.md §9 W10; etiquetas del 21-sep-2026) — el título
tiene que decir la afirmación concreta del clip, no el tema en abstracto.

Agustín rechazó dos clips publicables porque "los títulos y la descripción
no explican de qué hablan" (A-m11) / "las redacciones no son buenas"
(A-m8) — la salida real venía con el patrón "Tema: ¡La verdad sobre X!"
repetido en los 4 momentos de un job (corrida `9e739c7b`). Estos tests usan
esos títulos reales como entrada.

Cubre:
  - Los validadores puros (content_validators.title_is_valid y afines).
  - La orquestación en generate_moment_copy_full: título inválido dos
    veces cae a la cascada de fallback (W13-B, ver
    test_titulo_fallback_cascada.py para el detalle de la cascada) y marca
    `titulo_de_respaldo` cuando encuentra una fuente real.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.schemas import ViralMoment  # noqa: E402
from services import content_validators as cv  # noqa: E402
from services.processor import generate_moment_copy_full  # noqa: E402

# Títulos reales de la corrida 9e739c7b (docs de la tarea W13) — el patrón
# "Tema: ¡La verdad sobre X!" que llevó a Agustín a rechazar copy, no clip.
TITULOS_REALES_MALOS = [
    "Virus americanos: ¡El peligro de la inundación pulmonar!",
    "Contagios: ¡Las situaciones cotidianas de máximo riesgo!",
    "Tratamiento de virus: ¡La verdad sobre vacunas y fármacos!",
    "Transmisión: ¡La verdad sobre el periodo de incubación!",
]

CLIP_TEXT = (
    "No existe una vacuna aprobada ni un tratamiento antiviral específico "
    "para este virus. Lo único que se puede hacer es dar cuidados intensivos "
    "de soporte: oxígeno, líquidos y, en los casos más graves, un respirador."
)


# ═══════════════════════════════════════════════════════════════════════════
# content_validators.py — validadores puros
# ═══════════════════════════════════════════════════════════════════════════

class TestFormulasProhibidas:
    @pytest.mark.parametrize("titulo", [
        "Virus americanos: ¡El peligro de la inundación pulmonar!",
        "Tratamiento de virus: ¡La verdad sobre vacunas y fármacos!",
        "Transmisión: ¡La verdad sobre el periodo de incubación!",
        "El secreto de por qué te contagias tan rápido",
        "Lo que nadie te dice sobre las vacunas",
    ])
    def test_detecta_formula_prohibida(self, titulo):
        assert cv.title_has_forbidden_pattern(titulo) is True

    @pytest.mark.parametrize("titulo", [
        "No hay vacuna: el tratamiento es solo cuidados intensivos",
        "El virus americano inunda los pulmones de líquido",
        "Un apretón de manos puede contagiarte una zoonosis",
    ])
    def test_titulo_concreto_no_dispara_falso_positivo(self, titulo):
        assert cv.title_has_forbidden_pattern(titulo) is False


class TestConteoDeSignos:
    def test_un_par_de_exclamacion_no_pasa_el_maximo(self):
        assert cv.count_exclamations("Esto es grave: ¡cuidado!") == 1

    def test_dos_pares_de_exclamacion_pasa_el_maximo(self):
        assert cv.count_exclamations("¡Cuidado! ¡Esto es grave!") == 2

    def test_sin_exclamacion(self):
        assert cv.count_exclamations("No hay vacuna: solo cuidados intensivos") == 0


class TestTituloFiel:
    def test_titulo_con_palabra_del_clip_pasa(self):
        assert cv.title_is_faithful("No hay vacuna aprobada todavía", CLIP_TEXT) is True

    def test_titulo_sin_relacion_falla(self):
        assert cv.title_is_faithful("Receta de pasta casera fácil", CLIP_TEXT) is False

    def test_titulo_vacio_falla(self):
        assert cv.title_is_faithful("", CLIP_TEXT) is False


class TestTitleIsValid:
    @pytest.mark.parametrize("titulo", TITULOS_REALES_MALOS)
    def test_titulos_reales_que_rechazo_agustin_no_pasan(self, titulo):
        # Estos 4 títulos son la evidencia de la tarea (salida real de
        # 9e739c7b) — el validador tiene que rechazarlos a todos, aunque
        # sea por distintos motivos (fórmula, o no-fiel al CLIP_TEXT de
        # este test, que es de otro video).
        ok, reasons = cv.title_is_valid(titulo, CLIP_TEXT)
        assert ok is False
        assert reasons  # siempre hay al menos un motivo

    def test_titulo_valido_pasa(self):
        ok, reasons = cv.title_is_valid(
            "No hay vacuna: el tratamiento es solo cuidados intensivos", CLIP_TEXT
        )
        assert ok is True
        assert reasons == []

    def test_titulo_vacio_da_motivo_vacio(self):
        ok, reasons = cv.title_is_valid(None, CLIP_TEXT)
        assert ok is False
        assert reasons == ["vacio"]
        ok, reasons = cv.title_is_valid("   ", CLIP_TEXT)
        assert ok is False
        assert reasons == ["vacio"]

    def test_titulo_mas_de_60_caracteres_falla_por_largo(self):
        largo = "Esto es un título carísimo y larguísimo que supera con creces el tope de sesenta caracteres permitido"
        ok, reasons = cv.title_is_valid(largo, CLIP_TEXT)
        assert ok is False
        assert "supera_60_caracteres" in reasons


class TestDeriveTitleFromText:
    def test_deriva_de_la_primera_oracion(self):
        # La primera oración de CLIP_TEXT tiene 85 chars — más que el tope
        # de 60 — así que el fallback la recorta en el último espacio.
        derived = cv.derive_title_from_text(CLIP_TEXT)
        first = cv.first_sentence(CLIP_TEXT)
        assert len(derived) <= 60
        assert first.startswith(derived.rstrip(" ,;:—-"))

    def test_oracion_corta_no_se_toca(self):
        corta = "No hay vacuna."
        assert cv.derive_title_from_text(corta) == corta

    def test_nunca_supera_el_tope(self):
        derived = cv.derive_title_from_text(CLIP_TEXT, max_chars=20)
        assert len(derived) <= 20

    def test_no_corta_una_palabra_a_la_mitad(self):
        derived = cv.derive_title_from_text(CLIP_TEXT, max_chars=20)
        original_words = set(CLIP_TEXT.split())
        # Cada "palabra" del resultado (salvo la última si el corte cayó
        # justo en un espacio) tiene que existir tal cual en el original.
        for w in derived.split()[:-1]:
            assert w in original_words

    def test_texto_vacio_no_rompe(self):
        assert cv.derive_title_from_text("") == "Momento destacado"


# ═══════════════════════════════════════════════════════════════════════════
# generate_moment_copy_full — orquestación (regenerar → fallback + flag)
# ═══════════════════════════════════════════════════════════════════════════

def _moment() -> ViralMoment:
    return ViralMoment(
        start_time=0, end_time=30, hook="Borrador", viral_overlay="BORRADOR",
        emotional_trigger="curiosidad",
    )


def _llm_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.usage = None
    resp.choices = [MagicMock(message=MagicMock(content=json.dumps(payload)))]
    return resp


_BASE_COPY = {
    "twitter_thread": "\n\n".join([f"Tweet número {i} sobre el tratamiento del virus." * 6 for i in range(7)]),
    "linkedin_post": "No hay vacuna ni tratamiento antiviral para este virus. " * 15,
    "tiktok_caption": "No hay cura, solo cuidados intensivos #salud",
    "hook": "No existe una vacuna aprobada ni un tratamiento antiviral específico.",
    "viral_overlay": "SIN VACUNA NI CURA",
}


def _mock_client(*payloads: dict) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.side_effect = [_llm_response(p) for p in payloads]
    return client


class TestTituloInvalidoRegeneraYCaeAlFallback:
    @pytest.mark.parametrize("titulo_malo", TITULOS_REALES_MALOS)
    def test_titulo_real_rechazado_dos_veces_cae_al_fallback_con_flag(self, titulo_malo):
        moment = _moment()
        payload_1 = {**_BASE_COPY, "title": titulo_malo}
        payload_2 = {**_BASE_COPY, "title": titulo_malo}  # el modelo insiste con la misma fórmula
        client = _mock_client(payload_1, payload_2)

        ok = generate_moment_copy_full(moment, CLIP_TEXT, client=client)

        assert ok is True
        assert client.chat.completions.create.call_count == 2
        assert moment.title != titulo_malo
        # W13-B: con CLIP_TEXT bien formado la cascada de fallback
        # encuentra una fuente real (hook o primera oración) — solo cae a
        # "titulo_generico" cuando NADA de la cascada sirve, ver
        # test_titulo_fallback_cascada.py.
        assert "titulo_de_respaldo" in moment.clip_quality_issues
        assert "titulo_generico" not in moment.clip_quality_issues
        assert len(moment.title) <= 60

    def test_titulo_malo_en_el_primer_intento_bueno_en_el_reintento_no_hace_falta_fallback(self):
        moment = _moment()
        payload_1 = {**_BASE_COPY, "title": "La verdad sobre el tratamiento de este virus"}
        payload_2 = {**_BASE_COPY, "title": "No hay vacuna: el tratamiento es solo cuidados intensivos"}
        client = _mock_client(payload_1, payload_2)

        ok = generate_moment_copy_full(moment, CLIP_TEXT, client=client)

        assert ok is True
        assert client.chat.completions.create.call_count == 2
        assert moment.title == "No hay vacuna: el tratamiento es solo cuidados intensivos"
        assert "titulo_generico" not in (moment.clip_quality_issues or [])


class TestTituloValidoPasaSinTocar:
    def test_titulo_valido_no_regenera(self):
        moment = _moment()
        payload = {**_BASE_COPY, "title": "No hay vacuna: el tratamiento es solo cuidados intensivos"}
        client = _mock_client(payload)

        ok = generate_moment_copy_full(moment, CLIP_TEXT, client=client)

        assert ok is True
        assert client.chat.completions.create.call_count == 1
        assert moment.title == "No hay vacuna: el tratamiento es solo cuidados intensivos"
        assert "titulo_generico" not in (moment.clip_quality_issues or [])


class TestJobsViejosSinTitulo:
    """Robustez: title=None (job/modelo viejo) no rompe nada, ni en el
    validador puro ni en la orquestación."""

    def test_validador_puro_no_rompe_con_none(self):
        ok, reasons = cv.title_is_valid(None, CLIP_TEXT)
        assert ok is False
        assert reasons == ["vacio"]

    def test_orquestacion_sin_title_en_la_respuesta_cae_al_fallback(self):
        moment = _moment()
        payload = {k: v for k, v in _BASE_COPY.items()}  # sin "title"
        client = _mock_client(payload, payload)

        ok = generate_moment_copy_full(moment, CLIP_TEXT, client=client)

        assert ok is True
        assert moment.title  # no rompe, no queda None
        assert "titulo_de_respaldo" in moment.clip_quality_issues

    def test_copy_report_no_rompe_con_title_none(self):
        from eval.copy_report import analyze_title

        row = analyze_title("job-1", 1, None, None, CLIP_TEXT)
        assert row["formula_prohibida"] is False
        assert row["fiel_al_texto"] is False
        assert row["len_title"] == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
