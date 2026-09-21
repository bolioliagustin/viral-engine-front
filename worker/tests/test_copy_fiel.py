"""
W6 — Hook, overlay y copy fieles al clip (docs/PLAN_CALIDAD.md §4).

El juez castigaba hook y overlay porque prometían el TEMA del momento en vez
de citar algo que el clip realmente dice. Estos tests cubren:
  - Los validadores de fidelidad puros (content_validators.py).
  - La orquestación en generate_moment_copy_full (services/processor.py):
    regenera una vez si hook u overlay no son fieles, y si persiste cae a
    un fallback determinístico + flag en moment.clip_quality_issues.
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


# ─── Fixtures de texto ───────────────────────────────────────────────────────

CLIP_TEXT = (
    "El error del Ferrari en el pit stop le costó la carrera. "
    "Nadie se dio cuenta hasta que fue demasiado tarde. "
    "Por eso ahora revisan cada procedimiento tres veces antes de cada carrera."
)
FIRST_SENTENCE = "El error del Ferrari en el pit stop le costó la carrera."
LAST_SENTENCE = "Por eso ahora revisan cada procedimiento tres veces antes de cada carrera."

# Sin puntuación de oración — simula clip_text de un job legacy / Whisper sin
# puntuación fiable (el único caso en que first/last sentence degradan al
# texto completo).
CLIP_TEXT_SIN_PUNTUACION = (
    "el error del ferrari en el pit stop le costo la carrera nadie se dio "
    "cuenta hasta que fue demasiado tarde"
)


def _moment(hook: str = "Borrador", overlay: str = "BORRADOR OVERLAY") -> ViralMoment:
    return ViralMoment(
        start_time=0,
        end_time=30,
        hook=hook,
        viral_overlay=overlay,
        emotional_trigger="curiosidad",
    )


def _llm_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.usage = None
    resp.choices = [MagicMock(message=MagicMock(content=json.dumps(payload)))]
    return resp


_BASE_COPY = {
    "twitter_thread": "\n\n".join([f"Tweet número {i} sobre el error del Ferrari." * 6 for i in range(7)]),
    "linkedin_post": "Un error de pit stop le costó la carrera al equipo. " * 20,
    "tiktok_caption": "El error que le costó la carrera #f1 #ferrari",
}


def _mock_client(*payloads: dict) -> MagicMock:
    """Cliente que devuelve una respuesta distinta por cada llamada, en orden."""
    client = MagicMock()
    client.chat.completions.create.side_effect = [_llm_response(p) for p in payloads]
    return client


# ═══════════════════════════════════════════════════════════════════════════
# content_validators.py — validadores puros
# ═══════════════════════════════════════════════════════════════════════════

class TestTokenizeYStopwords:
    def test_normaliza_acentos_puntuacion_mayusculas(self):
        assert cv.tokenize("¡Ferrari, el ERROR!") == ["ferrari", "el", "error"]

    def test_content_words_saca_stopwords(self):
        assert cv.content_words("el error del Ferrari en el pit stop") == [
            "error", "ferrari", "pit", "stop",
        ]

    def test_tokenize_vacio(self):
        assert cv.tokenize("") == []
        assert cv.tokenize(None) == []


class TestOraciones:
    def test_first_last_sentence(self):
        assert cv.first_sentence(CLIP_TEXT) == FIRST_SENTENCE
        assert cv.last_sentence(CLIP_TEXT) == LAST_SENTENCE

    def test_sin_puntuacion_degrada_a_texto_completo(self):
        assert cv.first_sentence(CLIP_TEXT_SIN_PUNTUACION) == CLIP_TEXT_SIN_PUNTUACION
        assert cv.last_sentence(CLIP_TEXT_SIN_PUNTUACION) == CLIP_TEXT_SIN_PUNTUACION

    def test_texto_vacio(self):
        assert cv.first_sentence("") == ""
        assert cv.split_sentences("") == []


class TestOverlayFiel:
    def test_overlay_con_palabra_del_arranque_pasa(self):
        head = cv.clip_text_head_approx(CLIP_TEXT)
        assert cv.overlay_is_faithful("FERRARI EN PROBLEMAS", head) is True

    def test_overlay_sin_relacion_falla(self):
        head = cv.clip_text_head_approx(CLIP_TEXT)
        assert cv.overlay_is_faithful("RECETA DE PASTA CASERA", head) is False

    def test_overlay_solo_stopwords_falla(self):
        head = cv.clip_text_head_approx(CLIP_TEXT)
        assert cv.overlay_is_faithful("EL DE LA", head) is False

    def test_derive_overlay_usa_palabras_reales(self):
        head = cv.clip_text_head_approx(CLIP_TEXT)
        derived = cv.derive_overlay_from_text(head)
        assert derived == derived.upper()
        assert len(derived.split()) <= 4
        # Cada palabra derivada tiene que venir del texto real (fidelidad
        # garantizada por construcción, no solo por el checker).
        head_words = set(cv.tokenize(head))
        assert all(cv.normalize_token(w) in head_words for w in derived.split())


class TestHookFiel:
    def test_hook_identico_pasa(self):
        assert cv.hook_is_faithful(FIRST_SENTENCE, CLIP_TEXT) is True

    def test_hook_parafraseado_una_palabra_pasa(self):
        paraphrased = "El fallo del Ferrari en el pit stop le costó la carrera."
        assert cv.hook_is_faithful(paraphrased, CLIP_TEXT) is True

    def test_hook_inventado_falla(self):
        invented = "Así se hackea la mente de cualquier persona en cinco segundos"
        assert cv.hook_is_faithful(invented, CLIP_TEXT) is False

    def test_hook_muchas_palabras_distintas_falla(self):
        # 3 de 12 palabras cambiadas (25%+) — por encima del umbral tolerado
        degraded = "El desastre absoluto y total en el pit stop arruinó la carrera."
        assert cv.hook_is_faithful(degraded, CLIP_TEXT) is False

    def test_hook_vacio_falla(self):
        assert cv.hook_is_faithful("", CLIP_TEXT) is False

    def test_hook_reordenado_100_por_ciento_fiel_pasa(self):
        # Mismas palabras, cláusula movida al frente — parafraseo natural
        # 100% fiel que la versión anterior (subsecuencia en orden estricto)
        # rechazaba por error.
        reordered = "En el pit stop, el error del Ferrari le costó la carrera."
        assert cv.hook_is_faithful(reordered, CLIP_TEXT) is True


# ═══════════════════════════════════════════════════════════════════════════
# generate_moment_copy_full — orquestación (regenerar → fallback + flag)
# ═══════════════════════════════════════════════════════════════════════════

class TestOverlayNoFielRegeneraYCaeAlFallback:
    def test_overlay_no_fiel_dos_veces_cae_al_derivado_con_flag(self):
        moment = _moment()
        payload_1 = {**_BASE_COPY, "hook": FIRST_SENTENCE, "viral_overlay": "RECETA DE PASTA CASERA"}
        payload_2 = {**_BASE_COPY, "hook": FIRST_SENTENCE, "viral_overlay": "OTRO TEMA AJENO"}
        client = _mock_client(payload_1, payload_2)

        ok = generate_moment_copy_full(moment, CLIP_TEXT, client=client)

        assert ok is True
        assert client.chat.completions.create.call_count == 2
        expected_fallback = cv.derive_overlay_from_text(cv.clip_text_head_approx(CLIP_TEXT))
        assert moment.viral_overlay == expected_fallback
        assert moment.clip_quality_issues == ["overlay_no_fiel"]
        # El hook era fiel — no se toca ni se marca.
        assert moment.hook == FIRST_SENTENCE

    def test_overlay_no_fiel_en_el_reintento_pasa_no_hace_falta_fallback(self):
        moment = _moment()
        payload_1 = {**_BASE_COPY, "hook": FIRST_SENTENCE, "viral_overlay": "RECETA DE PASTA CASERA"}
        payload_2 = {**_BASE_COPY, "hook": FIRST_SENTENCE, "viral_overlay": "FERRARI EN PROBLEMAS"}
        client = _mock_client(payload_1, payload_2)

        ok = generate_moment_copy_full(moment, CLIP_TEXT, client=client)

        assert ok is True
        assert client.chat.completions.create.call_count == 2
        assert moment.viral_overlay == "FERRARI EN PROBLEMAS"
        assert not (moment.clip_quality_issues or [])


class TestOverlayFielPasaSinTocar:
    def test_overlay_fiel_no_regenera(self):
        moment = _moment()
        payload = {**_BASE_COPY, "hook": FIRST_SENTENCE, "viral_overlay": "FERRARI EN PROBLEMAS"}
        client = _mock_client(payload)

        ok = generate_moment_copy_full(moment, CLIP_TEXT, client=client)

        assert ok is True
        assert client.chat.completions.create.call_count == 1
        assert moment.viral_overlay == "FERRARI EN PROBLEMAS"
        assert not (moment.clip_quality_issues or [])


class TestHookNoFielRegeneraYCaeAPrimeraOracion:
    def test_hook_inventado_dos_veces_cae_a_primera_oracion_con_flag(self):
        moment = _moment()
        invented = "Así se hackea la mente de cualquier persona en cinco segundos"
        payload_1 = {**_BASE_COPY, "hook": invented, "viral_overlay": "FERRARI EN PROBLEMAS"}
        payload_2 = {**_BASE_COPY, "hook": "Otra promesa que el clip nunca dice", "viral_overlay": "FERRARI EN PROBLEMAS"}
        client = _mock_client(payload_1, payload_2)

        ok = generate_moment_copy_full(moment, CLIP_TEXT, client=client)

        assert ok is True
        assert client.chat.completions.create.call_count == 2
        assert moment.hook == FIRST_SENTENCE
        assert moment.clip_quality_issues == ["hook_no_fiel"]
        # El overlay era fiel en ambos intentos — no se toca ni se marca.
        assert moment.viral_overlay == "FERRARI EN PROBLEMAS"

    def test_ambos_no_fieles_marcan_los_dos_flags(self):
        moment = _moment()
        payload = {
            **_BASE_COPY,
            "hook": "Así se hackea la mente de cualquier persona",
            "viral_overlay": "RECETA DE PASTA CASERA",
        }
        client = _mock_client(payload, payload)

        generate_moment_copy_full(moment, CLIP_TEXT, client=client)

        assert set(moment.clip_quality_issues) == {"overlay_no_fiel", "hook_no_fiel"}


class TestHookParafraseadoPasa:
    def test_hook_con_una_palabra_distinta_pasa_sin_regenerar(self):
        moment = _moment()
        paraphrased = "El fallo del Ferrari en el pit stop le costó la carrera."
        payload = {**_BASE_COPY, "hook": paraphrased, "viral_overlay": "FERRARI EN PROBLEMAS"}
        client = _mock_client(payload)

        ok = generate_moment_copy_full(moment, CLIP_TEXT, client=client)

        assert ok is True
        assert client.chat.completions.create.call_count == 1
        assert moment.hook == paraphrased
        assert not (moment.clip_quality_issues or [])


class TestTextoSinPuntuacionJobsLegacy:
    """Clips sin Whisper puntuado (o jobs legacy): first/last sentence
    degradan al texto completo — el chequeo de fidelidad sigue funcionando
    igual que con oraciones reales, sin romper nada."""

    def test_hook_fiel_pasa_igual_que_antes(self):
        moment = _moment()
        payload = {
            **_BASE_COPY,
            "hook": "el error del ferrari en el pit stop le costo la carrera",
            "viral_overlay": "FERRARI PIT STOP",
        }
        client = _mock_client(payload)

        ok = generate_moment_copy_full(moment, CLIP_TEXT_SIN_PUNTUACION, client=client)

        assert ok is True
        assert client.chat.completions.create.call_count == 1
        assert moment.hook == payload["hook"]
        assert not (moment.clip_quality_issues or [])

    def test_hook_invalido_degrada_al_texto_completo(self):
        moment = _moment()
        invented = "Así se hackea la mente de cualquier persona en cinco segundos"
        payload = {**_BASE_COPY, "hook": invented, "viral_overlay": "FERRARI PIT STOP"}
        client = _mock_client(payload, payload)

        ok = generate_moment_copy_full(moment, CLIP_TEXT_SIN_PUNTUACION, client=client)

        assert ok is True
        # Degradación explícita del brief: sin oraciones reales, el
        # fallback es "todo el texto" — no crashea, mismo comportamiento
        # documentado que antes de W6 (Pasada B trabajaba sobre el texto
        # completo sin más señal disponible).
        assert moment.hook == CLIP_TEXT_SIN_PUNTUACION
        assert "hook_no_fiel" in moment.clip_quality_issues


class TestSinCambiosCuandoNoHayTexto:
    def test_clip_text_vacio_no_llama_al_llm(self):
        moment = _moment()
        client = _mock_client()
        ok = generate_moment_copy_full(moment, "   ", client=client)
        assert ok is False
        assert client.chat.completions.create.call_count == 0

    def test_llm_vacio_no_rompe(self):
        moment = _moment(hook="Hook previo", overlay="OVERLAY PREVIO")
        resp = MagicMock()
        resp.usage = None
        resp.choices = [MagicMock(message=MagicMock(content=""))]
        client = MagicMock()
        client.chat.completions.create.return_value = resp

        ok = generate_moment_copy_full(moment, CLIP_TEXT, client=client)

        assert ok is False
        # No tocamos el moment si la Pasada B no devolvió nada.
        assert moment.hook == "Hook previo"
        assert moment.viral_overlay == "OVERLAY PREVIO"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
