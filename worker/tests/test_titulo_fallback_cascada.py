"""
W13-B (docs/PLAN_CALIDAD.md §9 W10) — el fallback del título no puede ser
peor que lo que reemplaza.

Al revisar 5 clips reales del fallback de W13 (primera oración a secas),
uno arrancaba a mitad de diálogo: "si es un poco exagerado, me cuentes un
poquito." — un fragmento en minúscula con coma, peor que el título
genérico "Tema: ¡La verdad sobre X!" que se supone que reemplaza.

Esta suite cubre:
  - `parece_titulo`, con los 4 casos reales de la tarea.
  - `resolve_title_fallback`, la cascada completa (hook → primera oración
    → oración más informativa → overlay → "Momento destacado"), un test
    por nivel.
  - La orquestación en generate_moment_copy_full: el flag
    `titulo_de_respaldo` (se usó una fuente real) vs `titulo_generico`
    (no quedó nada mejor que el molde).
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


# ═══════════════════════════════════════════════════════════════════════════
# parece_titulo — los 4 casos reales de la tarea
# ═══════════════════════════════════════════════════════════════════════════

class TestPareceTitulo:
    def test_fragmento_de_dialogo_minuscula_falla(self):
        # El caso real que motivó esta tarea: fragmento de diálogo a mitad
        # de conversación, en minúscula, con coma y punto final.
        assert cv.parece_titulo("si es un poco exagerado, me cuentes un poquito.") is False

    def test_oracion_completa_con_mayuscula_pasa(self):
        assert cv.parece_titulo("El periodo de incubación del virus no es contagioso") is True

    def test_muletilla_corta_falla(self):
        assert cv.parece_titulo("y bueno, claro") is False

    def test_afirmacion_concreta_pasa(self):
        assert cv.parece_titulo("Una azafata sin mascarilla se expone al contagio") is True

    # Casos adicionales (mismas reglas, no pedidos explícitamente pero
    # cubren cada regla por separado en vez de solo en combinación).
    def test_menos_de_4_palabras_falla_aunque_tenga_mayuscula(self):
        assert cv.parece_titulo("El virus muta.") is False

    def test_conector_o_sea_como_frase_falla(self):
        assert cv.parece_titulo("O sea que no hay tratamiento posible") is False

    def test_pregunta_corta_falla(self):
        assert cv.parece_titulo("Y esto por qué?") is False

    def test_vacio_falla(self):
        assert cv.parece_titulo("") is False
        assert cv.parece_titulo(None) is False

    def test_arranca_en_minuscula_aunque_el_resto_sea_una_oracion_valida(self):
        assert cv.parece_titulo("el periodo de incubación del virus no es contagioso") is False


# ═══════════════════════════════════════════════════════════════════════════
# resolve_title_fallback — un test por nivel de la cascada
# ═══════════════════════════════════════════════════════════════════════════

CLIP_COHERENTE = (
    "No existe una vacuna aprobada ni un tratamiento antiviral específico "
    "para este virus. Lo único que se puede hacer es dar cuidados intensivos "
    "de soporte: oxígeno, líquidos y, en los casos más graves, un respirador."
)

# Clip que arranca a mitad de diálogo (el caso real que rompía el fallback
# viejo) — ninguna de las primeras oraciones sirve como título solo.
CLIP_A_MITAD_DE_DIALOGO = (
    "si es un poco exagerado, me cuentes un poquito. y bueno, claro. "
    "eh, no sé, viste, es como que a veces pasa."
)


class TestResolveTitleFallbackCascada:
    def test_nivel_a_usa_el_hook_si_es_fiel_y_entra_en_60(self):
        titulo, nivel = cv.resolve_title_fallback(
            clip_text=CLIP_COHERENTE,
            hook="No hay tratamiento antiviral específico para este virus",
            hook_is_faithful_flag=True,
            overlay="SIN TRATAMIENTO",
        )
        assert nivel == "hook"
        assert titulo == "No hay tratamiento antiviral específico para este virus"

    def test_nivel_a_se_salta_si_el_hook_no_es_fiel(self):
        titulo, nivel = cv.resolve_title_fallback(
            clip_text=CLIP_COHERENTE,
            hook="Un hook inventado que el clip nunca dice",
            hook_is_faithful_flag=False,
            overlay="SIN TRATAMIENTO",
        )
        assert nivel != "hook"

    def test_nivel_a_se_salta_si_el_hook_supera_60_caracteres(self):
        hook_largo = "N" * 61
        titulo, nivel = cv.resolve_title_fallback(
            clip_text=CLIP_COHERENTE, hook=hook_largo, hook_is_faithful_flag=True, overlay="X",
        )
        assert nivel != "hook"

    def test_nivel_a_se_salta_si_el_hook_es_un_fragmento_aunque_sea_fiel(self):
        # Hallazgo real (10 clips etiquetados, job 9e739c7b m2): un hook de
        # una sola palabra común ("esto.") pasa hook_is_faithful (esa
        # palabra aparece en el clip, cubre la bolsa de palabras) pero es
        # tan mal título como cualquier fragmento — parece_titulo tiene que
        # bloquearlo igual que bloquearía la primera oración.
        titulo, nivel = cv.resolve_title_fallback(
            clip_text=CLIP_COHERENTE, hook="esto.", hook_is_faithful_flag=True, overlay=None,
        )
        assert nivel != "hook"
        assert titulo != "esto."

    def test_nivel_b_primera_oracion_cuando_no_hay_hook(self):
        titulo, nivel = cv.resolve_title_fallback(
            clip_text=CLIP_COHERENTE, hook=None, hook_is_faithful_flag=False, overlay=None,
        )
        assert nivel == "primera_oracion"
        assert titulo[0].isupper()

    def test_nivel_c_oracion_informativa_cuando_la_primera_no_sirve(self):
        # La primera oración es un arranque de diálogo corto (no pasa
        # parece_titulo por longitud) pero la segunda, entre las primeras
        # 5, sí sirve — (c) tiene que rescatarla.
        clip = "Che, mirá esto. El sarampión es la enfermedad más contagiosa que existe según los expertos."
        titulo, nivel = cv.resolve_title_fallback(
            clip_text=clip, hook=None, hook_is_faithful_flag=False, overlay=None,
        )
        assert nivel == "oracion_informativa"
        assert titulo.startswith("El sarampión")

    def test_ninguna_oracion_sirve_y_sin_overlay_cae_a_generico(self):
        # CLIP_A_MITAD_DE_DIALOGO: ninguna de sus 3 oraciones pasa
        # parece_titulo (todas arrancan en minúscula o son muy cortas) —
        # ni (b) ni (c) encuentran nada, sin overlay cae directo a (d). Lo
        # que NUNCA puede pasar es devolver el fragmento roto.
        titulo, nivel = cv.resolve_title_fallback(
            clip_text=CLIP_A_MITAD_DE_DIALOGO,
            hook=None,
            hook_is_faithful_flag=False,
            overlay=None,
        )
        assert nivel == "generico"
        assert titulo == "Momento destacado"
        assert titulo != "si es un poco exagerado, me cuentes un poquito."

    def test_nivel_c_prefiere_la_oracion_con_mas_carga_informativa(self):
        clip = (
            "Bueno. El sarampión tiene un R0 de dieciocho contagios por persona "
            "infectada según la OMS."
        )
        titulo, nivel = cv.resolve_title_fallback(
            clip_text=clip, hook=None, hook_is_faithful_flag=False, overlay=None,
        )
        # "Bueno." no pasa parece_titulo (muletilla + muy corta); la
        # oración con el dato (R0, número) tiene que ganar en (b) o (c).
        assert "R0" in titulo or "dieciocho" in titulo

    def test_nivel_d_overlay_capitalizado_cuando_nada_mas_sirve(self):
        titulo, nivel = cv.resolve_title_fallback(
            clip_text=CLIP_A_MITAD_DE_DIALOGO,
            hook=None,
            hook_is_faithful_flag=False,
            overlay="sin tratamiento posible",
        )
        assert nivel == "overlay"
        assert titulo == "Sin tratamiento posible"

    def test_overlay_con_signo_de_apertura_no_arranca_en_minuscula(self):
        # Hallazgo real (job 9e739c7b m11): el overlay '¿HAY TRATAMIENTO O
        # CURACIÓN' (MAYÚSCULAS, arranca con "¿") se convertía en
        # '¿hay tratamiento o curación' con .capitalize() — el primer
        # CARÁCTER es "¿", no una letra, así que la "H" real quedaba en
        # minúscula. _capitalize_first_letter tiene que mayuscular la
        # primera letra de verdad, no el primer carácter.
        titulo, nivel = cv.resolve_title_fallback(
            clip_text=CLIP_A_MITAD_DE_DIALOGO,
            hook=None,
            hook_is_faithful_flag=False,
            overlay="¿HAY TRATAMIENTO O CURACIÓN",
        )
        assert nivel == "overlay"
        assert titulo == "¿Hay tratamiento o curación"

    def test_nivel_generico_como_ultimo_recurso(self):
        titulo, nivel = cv.resolve_title_fallback(
            clip_text="eh. y bueno. si, claro.", hook=None, hook_is_faithful_flag=False, overlay=None,
        )
        assert nivel == "generico"
        assert titulo == "Momento destacado"

    def test_nunca_devuelve_mas_de_60_caracteres(self):
        clip = "Una explicación clínica extremadamente larga sobre " * 5 + "el virus."
        titulo, _nivel = cv.resolve_title_fallback(
            clip_text=clip, hook=None, hook_is_faithful_flag=False, overlay=None,
        )
        assert len(titulo) <= 60


# ═══════════════════════════════════════════════════════════════════════════
# Orquestación — generate_moment_copy_full con el clip real que rompía W13
# ═══════════════════════════════════════════════════════════════════════════

def _moment(hook: str = "Borrador") -> ViralMoment:
    return ViralMoment(
        start_time=0, end_time=30, hook=hook, viral_overlay="BORRADOR",
        emotional_trigger="curiosidad",
    )


def _llm_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.usage = None
    resp.choices = [MagicMock(message=MagicMock(content=json.dumps(payload)))]
    return resp


def _mock_client(*payloads: dict) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.side_effect = [_llm_response(p) for p in payloads]
    return client


_BASE_COPY_SIN_HOOK_FIEL = {
    "twitter_thread": "\n\n".join([f"Tweet {i} sobre el periodo de incubación." * 6 for i in range(7)]),
    "linkedin_post": "El periodo de incubación puede durar semanas. " * 15,
    "tiktok_caption": "Semanas sin síntomas #salud",
    # Hook inventado a propósito (no fiel) para forzar que la cascada de
    # título no pueda usar el nivel (a) y tenga que probar (b)/(c)/(d).
    "hook": "Una promesa que el clip jamás dice",
    "viral_overlay": "PERIODO DE INCUBACION",
}


class TestOrquestacionConClipQueRompiaElFallbackViejo:
    def test_clip_a_mitad_de_dialogo_nunca_da_un_titulo_roto(self):
        moment = _moment()
        payload = {**_BASE_COPY_SIN_HOOK_FIEL, "title": "Periodo de incubación: ¡El peligro de las 6 semanas!"}
        client = _mock_client(payload, payload)

        ok = generate_moment_copy_full(moment, CLIP_A_MITAD_DE_DIALOGO, client=client)

        assert ok is True
        assert moment.title != "si es un poco exagerado, me cuentes un poquito."
        assert moment.title[0].isupper() or moment.title == "Momento destacado"
        assert "," not in moment.title.rstrip(".")[:1]  # no arranca a mitad de frase

    def test_flag_titulo_de_respaldo_cuando_usa_una_fuente_real(self):
        moment = _moment(hook="No hay tratamiento antiviral específico para este virus")
        # Un hook FIEL al clip real (CLIP_COHERENTE) — la Pasada B lo repite.
        payload = {
            **_BASE_COPY_SIN_HOOK_FIEL,
            "hook": "No hay tratamiento antiviral específico para este virus",
            "title": "La verdad sobre el tratamiento de este virus",  # fórmula prohibida
        }
        client = _mock_client(payload, payload)

        ok = generate_moment_copy_full(moment, CLIP_COHERENTE, client=client)

        assert ok is True
        assert moment.title == "No hay tratamiento antiviral específico para este virus"
        assert "titulo_de_respaldo" in moment.clip_quality_issues
        assert "titulo_generico" not in moment.clip_quality_issues


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


class TestRescatarTituloDelModelo:
    """Antes de ir al transcript, intentar limpiar el título del modelo.

    El título del modelo, aunque use una fórmula prohibida, está escrito COMO
    un título y habla del tema del clip; una oración del transcript es texto
    hablado y se lee peor. Medido sobre 10 clips reales el 21-sep-2026: la
    cascada sin este nivel producía títulos peores que el genérico que
    reemplazaba ("¿Hay tratamiento o curación", "Pinta situación, un
    trabajador del barco que tiene que").
    """

    def test_saca_la_formula_y_conserva_lo_que_informa(self):
        from services.content_validators import limpiar_titulo_generado
        texto = ("No hay vacuna contra este virus ni tratamiento antiviral, "
                 "lo único que se hace son cuidados intensivos.")
        r = limpiar_titulo_generado(
            "Tratamiento de virus: ¡La verdad sobre vacunas y fármacos!", texto
        )
        assert r == "Tratamiento de virus: vacunas y fármacos"

    def test_saca_el_peligro_de(self):
        from services.content_validators import limpiar_titulo_generado
        texto = "Los virus americanos inundan los pulmones con el suero de la sangre."
        r = limpiar_titulo_generado(
            "Virus americanos: ¡El peligro de la inundación pulmonar!", texto
        )
        assert r == "Virus americanos: la inundación pulmonar"

    def test_si_no_queda_nada_util_devuelve_none(self):
        from services.content_validators import limpiar_titulo_generado
        assert limpiar_titulo_generado("Lo que nadie te dice", "Texto del clip.") is None

    def test_si_el_titulo_no_habla_del_clip_no_se_rescata(self):
        """La limpieza no puede saltearse la validación de fidelidad."""
        from services.content_validators import limpiar_titulo_generado
        assert limpiar_titulo_generado(
            "Recetas de cocina: ¡La verdad sobre el pan casero!",
            "Hablamos del periodo de incubación de un virus y su transmisión.",
        ) is None

    def test_vacio_o_none_devuelve_none(self):
        from services.content_validators import limpiar_titulo_generado
        assert limpiar_titulo_generado(None, "texto") is None
        assert limpiar_titulo_generado("   ", "texto") is None

    def test_el_titulo_limpiado_gana_al_transcript(self):
        from services.content_validators import resolve_title_fallback
        texto = ("Durante el periodo de incubación la persona no transmite el virus, "
                 "se va a transmitir sobre todo cuando tenemos síntomas.")
        # Sin hook usable, el título limpiado gana a cualquier oración del
        # transcript (con hook fiel, el hook va primero: dice el hecho).
        titulo, nivel = resolve_title_fallback(
            clip_text=texto,
            hook="",
            hook_is_faithful_flag=False,
            overlay="PERIODO DE INCUBACIÓN",
            titulo_generado="Transmisión: ¡La verdad sobre el periodo de incubación!",
        )
        assert nivel == "limpiado"
        assert titulo == "Transmisión: el periodo de incubación"

    def test_sin_titulo_generado_la_cascada_es_la_de_antes(self):
        """Compatibilidad: el parámetro es opcional."""
        from services.content_validators import resolve_title_fallback
        texto = "Una azafata sin mascarilla se expone al contagio durante el vuelo."
        titulo, nivel = resolve_title_fallback(
            clip_text=texto, hook="", hook_is_faithful_flag=False, overlay=None,
        )
        assert nivel in ("primera_oracion", "oracion_informativa", "generico")
