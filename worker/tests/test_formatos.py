"""
W22 — Formato de contenido (docs/briefs/W22-formatos-de-contenido.md, ADR 0010).

Parseo del clasificador (valores inválidos caen a `entrevista`), los tres
extractos, el mapeo a la categoría vieja, el prompt por Formato (foco,
historia completa y publicidad), la verificación posterior de publicidad con
el aviso de DiDi del Anexo B (B60BHDNFNxM, 3100–3168 s), la orquestación en
`select_moments` (también por Ventana) y el flag en la versión efectiva.
"""
import json
import os
import sys
import threading
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import formatos as fm  # noqa: E402


# ─── Parseo y mapeo ──────────────────────────────────────────────────────────

class TestParseo:
    @pytest.mark.parametrize("respuesta,esperado", [
        ("charla", "charla"),
        ("Charla", "charla"),
        ("  ENTREVISTA\n", "entrevista"),
        ("Monólogo.", "monologo"),
        ("monologo", "monologo"),
        ("clase: es un tutorial de Claude", "clase"),
        ("**clase**", "clase"),
    ])
    def test_valores_validos(self, respuesta, esperado):
        assert fm.parsear_formato(respuesta) == esperado

    @pytest.mark.parametrize("respuesta", ["podcast", "business", "", None, "no sé", "es una charla"])
    def test_invalido_cae_a_entrevista_con_log(self, respuesta, capsys):
        assert fm.parsear_formato(respuesta) == "entrevista"
        assert "Formato inválido" in capsys.readouterr().out

    def test_mapeo_a_categoria(self):
        assert fm.formato_a_categoria("entrevista") == "podcast"
        assert fm.formato_a_categoria("charla") == "podcast"
        assert fm.formato_a_categoria("monologo") == "business"
        assert fm.formato_a_categoria("clase") == "business"
        assert fm.formato_a_categoria("monólogo") == "business"  # etiqueta del golden set
        assert fm.formato_a_categoria(None) == "podcast"

    @pytest.mark.parametrize("valor,esperado", [("on", True), ("true", True), ("off", False), ("", False)])
    def test_flag(self, monkeypatch, valor, esperado):
        monkeypatch.setenv("FORMATOS", valor)
        assert fm.formatos_enabled() is esperado

    def test_flag_default_off(self, monkeypatch):
        monkeypatch.delenv("FORMATOS", raising=False)
        assert fm.formatos_enabled() is False


class TestExtractos:
    def test_tres_extractos_de_inicio_mitad_y_final(self):
        lineas = [{"start": i, "end": i + 1, "text": f"L{i:04d} " + "x" * 40} for i in range(400)]
        ex = fm.extractos_para_clasificar({"lines": lineas})
        assert all(len(ex[k]) == fm.EXTRACTO_CHARS for k in ("inicio", "mitad", "final"))
        assert ex["inicio"].startswith("L0000")
        assert "L0399" in ex["final"]
        assert "L0199" in ex["mitad"] or "L0200" in ex["mitad"]

    def test_video_corto_todo_en_inicio(self):
        ex = fm.extractos_para_clasificar({"segments": [{"text": "Hola."}, {"text": "Chau."}]})
        assert ex == {"inicio": "Hola. Chau.", "mitad": "", "final": ""}

    def test_sin_transcript(self):
        assert fm.extractos_para_clasificar(None) == {"inicio": "", "mitad": "", "final": ""}


# ─── Clasificador (processor.clasificar_formato) ─────────────────────────────

class _ClienteClasificador:
    def __init__(self, respuesta="charla", error=None):
        self.respuesta, self.error = respuesta, error
        self.prompts: list[str] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.prompts.append(kwargs["messages"][1]["content"])
        if self.error:
            raise self.error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.respuesta), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=1, total_tokens=101,
                                  completion_tokens_details=None),
        )


def _transcript_largo():
    lineas = [{"start": i * 5, "end": i * 5 + 5, "text": f"Frase número {i} del programa."} for i in range(600)]
    return {"lines": lineas, "segments": []}


class TestClasificarFormato:
    def test_ve_titulo_canal_y_tres_extractos(self):
        from services.processor import clasificar_formato

        c = _ClienteClasificador("charla")
        f = clasificar_formato({"title": "Blender en vivo", "uploader": "Blender"}, c, transcript=_transcript_largo())
        assert f == "charla"
        p = c.prompts[0]
        assert "Blender en vivo" in p and "Canal: Blender" in p
        assert "Inicio del transcript" in p and "Mitad del transcript" in p and "Final del transcript" in p
        assert "Frase número 0 " in p and "Frase número 599" in p

    def test_invalido_cae_a_entrevista(self, capsys):
        from services.processor import clasificar_formato

        assert clasificar_formato({"title": "x"}, _ClienteClasificador("podcast"), transcript=_transcript_largo()) == "entrevista"
        assert "Formato inválido" in capsys.readouterr().out

    def test_falla_del_modelo_cae_a_entrevista(self, capsys):
        from services.processor import clasificar_formato

        c = _ClienteClasificador(error=RuntimeError("timeout"))
        assert clasificar_formato({"title": "x"}, c, transcript=_transcript_largo()) == "entrevista"
        assert "falló" in capsys.readouterr().out

    def test_sin_cliente_usa_palabras_clave(self):
        from services.processor import clasificar_formato

        assert clasificar_formato({"title": "Tutorial de Claude Code"}) == "clase"
        assert clasificar_formato({"title": "Entrevista a Fulano"}) == "entrevista"
        assert clasificar_formato({"title": "Blender stream en vivo"}) == "charla"
        assert clasificar_formato({"title": "Cómo dejar de procrastinar"}) == "clase"
        assert clasificar_formato({"title": "Mi opinión sobre el éxito"}) == "monologo"

    def test_get_video_category_envuelve_con_flag_on(self, monkeypatch):
        from services.processor import get_video_category

        monkeypatch.setenv("FORMATOS", "on")
        assert get_video_category({"title": "x"}, _ClienteClasificador("charla"), "hola") == "podcast"
        assert get_video_category({"title": "x"}, _ClienteClasificador("clase"), "hola") == "business"

    def test_get_video_category_de_siempre_con_flag_off(self, monkeypatch):
        from services.processor import get_video_category

        monkeypatch.delenv("FORMATOS", raising=False)
        assert get_video_category({"title": "x"}, _ClienteClasificador("podcast"), "hola") == "podcast"


# ─── Prompt por Formato ──────────────────────────────────────────────────────

class TestPromptPorFormato:
    FOCOS = {
        "charla": ["Anécdota con remate", "Imitación o personaje", "chicana", "incluye la reacción"],
        "entrevista": ["Pregunta provocadora", "RESPUESTA COMPLETA"],
        "monologo": ["Contrarian truths", "IDEA COMPLETA con su conclusión"],
        "clase": ["concepto explicado ENTERO", "PORQUÉ", "CONTEXTO va ANTES del resultado"],
    }

    @pytest.mark.parametrize("formato", fm.FORMATOS)
    def test_cada_formato_trae_foco_historia_completa_y_publicidad(self, formato):
        from services.moment_selector import get_selection_prompt

        p = get_selection_prompt(3600, 20, fm.formato_a_categoria(formato), "es", formato=formato)
        assert f"FORMATO DEL VIDEO: {formato.upper()}" in p
        for trozo in self.FOCOS[formato]:
            assert trozo.lower() in p.lower(), trozo
        assert "HISTORIA COMPLETA" in p and "NUNCA la partas" in p
        assert "Ejemplo de partir MAL una historia" in p
        assert "al menos ~30 s" in p
        assert "PUBLICIDAD (EXCLUIR)" in p and "chivo" in p and "códigos de descuento" in p
        assert f'"category": "{fm.formato_a_categoria(formato)}"' in p
        json.loads(p[p.index("{", p.index("FORMATO JSON")):p.index("RECORDATORIO")].strip())

    @pytest.mark.parametrize("formato,rango", [
        ("charla", "entre 30 y 120"), ("entrevista", "entre 30 y 120"),
        ("monologo", "entre 20 y 90"), ("clase", "entre 20 y 90"),
    ])
    def test_duracion_objetivo(self, formato, rango):
        from services.moment_selector import get_selection_prompt

        assert rango in get_selection_prompt(3600, 20, "podcast", "es", formato=formato)

    def test_charla_no_es_el_foco_de_entrevista(self):
        from services.moment_selector import get_selection_prompt

        p = get_selection_prompt(3600, 20, "podcast", "es", formato="charla")
        assert "Pregunta provocadora" not in p

    def test_sin_formato_el_prompt_de_siempre(self):
        from services.moment_selector import get_selection_prompt

        p = get_selection_prompt(3600, 20, "podcast", "es")
        assert "FORMATO DEL VIDEO" not in p and "PUBLICIDAD" not in p
        assert "PRIORIZA (contenido conversacional)" in p
        assert get_selection_prompt(3600, 20, "podcast", "es", formato="desconocido") == p


# ─── Publicidad: el aviso de DiDi del Anexo B ────────────────────────────────

# Líneas reales de B60BHDNFNxM (transcript whisper_full), 3060–3190 s.
_B60 = [
    (3060, 3066, "Públicamente no hay frío ni calor."),
    (3066, 3075, "Después llorás en el baño, eso sí."),
    (3075, 3087, "Pero públicamente no hay frío ni calor, nunca."),
    (3093, 3094, "Eso sí que no."),
    (3095, 3096, "¿Qué hermoso con esto?"),
    (3097, 3098, "Se puede."),
    (3098, 3100, "Vamos de todo."),
    (3100, 3102, "Voy a tirar un chivado."),
    (3102, 3103, "Permitime."),
    (3103, 3110, "Si sos conductor de Didi, todos los puntos Didi que acumulás se traducen en dinero en tu bolsillo y mejores experiencias."),
    (3110, 3112, "La clasificación tiene cuatro niveles."),
    (3112, 3114, "Básico, avanzado, experto y elite."),
    (3115, 3122, "Gracias a la colaboración junto a la Liga Profesional de Fútbol, tus puntos se pueden volver recompensas para vivir el fútbol desde adentro."),
    (3122, 3129, "Si al plantel de Riestra lo hubiese llevado al estadio de gimnasia de Mendoza, un Didi, hubiese recorrido 1.100 kilómetros."),
    (3130, 3132, "Eso es un elite de cabeza, Luqui."),
    (3133, 3140, "Acordate, acordate que el Instituto recibió talleres y los hubiera llevado un Didi y hubiese hecho solo 11 kilómetros."),
    (3140, 3141, "Para mí, ahí todavía vas a estar en básico."),
    (3142, 3146, "Otros que hicieron más de 1.000 kilómetros son los de Independiente de Río Ávila que están jugando contra Barracas."),
    (3146, 3149, "No se olviden de Belgrano y estudiantes de Río Cuarto."),
    (3149, 3153, "Son 220 kilómetros en auto, dos horitas y media, no es mucho."),
    (3153, 3153, "No es mucho, papá."),
    (3153, 3157, "En resumen, Didi Elite te permite hacer rendir más tu tiempo."),
    (3158, 3165, "Mientras más alto llegás, más rápido sumás puntos para cada viaje y a mejores experiencias podés acceder."),
    (3165, 3167, "Con Didi te queda más."),
    (3166, 3168, "¡Vamos, Didi!"),
    (3168, 3169, "Gracias, Didi."),
    (3169, 3171, "Vamos a despedir a Bambino."),
    (3171, 3172, "Bambino, muchísimas gracias."),
    (3172, 3173, "¿Cómo la pasaste?"),
    (3174, 3174, "Me voy a un Didi."),
    (3177, 3178, "¿Cómo estuviste? Como el orto."),
    (3180, 3182, "No vengo más, no vengo más."),
    (3186, 3188, "Y venimos con más par en la mano."),
]
LINEAS_DIDI = [{"start": a, "end": b, "text": t} for a, b, t in _B60]


class TestPublicidad:
    def test_detecta_el_aviso_de_didi(self):
        tramos = fm.detectar_publicidad({"lines": LINEAS_DIDI})
        assert len(tramos) == 1
        ini, fin = tramos[0]
        assert ini <= 3100 and fin >= 3168
        assert ini >= 3095 and fin <= 3190

    def test_descarta_el_candidato_del_aviso_y_conserva_los_de_al_lado(self, capsys):
        momentos = [
            {"start_time": 3098, "end_time": 3170, "hook": "aviso"},
            {"start_time": 3060, "end_time": 3090, "hook": "R09 frase citable"},
            {"start_time": 3169, "end_time": 3182, "hook": "R24 cierre"},
        ]
        conservados, descartados = fm.descartar_publicidad(momentos, {"lines": LINEAS_DIDI})
        assert [m["hook"] for m in descartados] == ["aviso"]
        assert descartados[0]["solape_publicidad"] > 0.5
        assert [m["hook"] for m in conservados] == ["R09 frase citable", "R24 cierre"]
        assert "Publicidad: descarto el candidato 3098–3170s" in capsys.readouterr().out

    def test_solape_menor_a_la_mitad_se_conserva(self):
        # 60 s de clip con 20 s dentro del aviso: se queda.
        conservados, descartados = fm.descartar_publicidad(
            [{"start_time": 3040, "end_time": 3120}], {"lines": LINEAS_DIDI},
        )
        assert len(conservados) == 1 and not descartados

    def test_hablar_de_publicidad_no_es_un_aviso(self):
        lineas = [
            {"start": 573, "end": 576, "text": "No, ¿vos viste la publicidad que compartí hoy?"},
            {"start": 586, "end": 588, "text": "Compartí tres minutos de una publicidad."},
            {"start": 593, "end": 598, "text": "Es espectacular la publicidad de Brahma."},
            {"start": 600, "end": 604, "text": "No, es de Brahma, igual son amigos de la gente."},
        ]
        assert fm.detectar_publicidad(lineas) == []

    def test_un_nombre_repetido_no_extiende_el_aviso(self):
        lineas = [
            {"start": 1099, "end": 1101, "text": "No entiendo cómo no lo llamaron para que sea auspiciado por una marca."},
            {"start": 1108, "end": 1120, "text": "Igual Macri está muy salidor, Macri está muy salidor."},
            {"start": 1121, "end": 1130, "text": "Ayer se viralizó una foto de Macri en la Expo."},
        ]
        tramos = fm.detectar_publicidad(lineas)
        assert tramos == [(1099.0, 1101.0)]

    def test_marcas_debiles_necesitan_ser_dos(self):
        una = [{"start": 10, "end": 15, "text": "Esto lo conseguís en cualquier lado."}]
        assert fm.detectar_publicidad(una) == []
        dos = una + [{"start": 16, "end": 20, "text": "Descargate la app y listo."}]
        assert fm.detectar_publicidad(dos) == [(10.0, 20.0)]

    def test_sin_lineas(self):
        assert fm.detectar_publicidad({}) == []
        assert fm.descartar_publicidad([{"start_time": 1, "end_time": 2}], None) == ([{"start_time": 1, "end_time": 2}], [])


# ─── Orquestación en select_moments ──────────────────────────────────────────

class _ClientePasadaA:
    """Devuelve un candidato normal y uno encima del aviso de DiDi."""

    def __init__(self):
        self.sistemas: list[str] = []
        self.usuarios: list[str] = []
        self.lock = threading.Lock()
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        import re

        with self.lock:
            self.sistemas.append(kwargs["messages"][0]["content"])
            self.usuarios.append(kwargs["messages"][1]["content"])
        m = re.search(r"tramo del segundo (\d+) al (\d+)", kwargs["messages"][1]["content"])
        ini, fin = (int(m.group(1)), int(m.group(2))) if m else (0, 4200)
        momentos = []
        if ini <= 3100 <= fin:
            momentos.append({"start_time": 3100, "end_time": 3166, "hook": "aviso",
                             "scores": {"hook": 9, "retention": 9, "shareability": 9}})
        momentos.append({"start_time": ini + 100, "end_time": ini + 160, "hook": "bueno",
                         "scores": {"hook": 7, "retention": 7, "shareability": 7}})
        content = json.dumps({"video_title": "t", "summary": "s", "viral_moments": momentos})
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=500, total_tokens=1500,
                                  completion_tokens_details=None),
        )


def _transcript_con_didi(dur=4200):
    relleno = [{"start": t, "end": t + 10, "text": f"Oración en el segundo {t}."}
               for t in range(0, dur, 10) if not (3050 <= t < 3200)]
    lineas = sorted(relleno + LINEAS_DIDI, key=lambda l: l["start"])
    return {"source": "whisper_full", "lines": lineas, "segments": []}


def _select(cliente, formato, dur=4200):
    from services.moment_selector import select_moments

    return select_moments(
        transcript_text="(transcript completo)", video_info={"title": "x"}, duration=dur,
        category="podcast", language="es", client=cliente, model="google/gemini-3.5-flash",
        max_retries=1, transcript=_transcript_con_didi(dur), formato=formato,
    )


class TestSelectMomentsConFormato:
    def test_pasada_unica_con_foco_y_sin_publicidad(self, monkeypatch):
        monkeypatch.delenv("SELECCION_POR_VENTANAS", raising=False)
        c = _ClientePasadaA()
        r = _select(c, "charla")
        assert len(c.sistemas) == 1 and "FORMATO DEL VIDEO: CHARLA" in c.sistemas[0]
        hooks = [m["hook"] for m in r["viral_moments"]]
        assert hooks == ["bueno"]
        assert [m["hook"] for m in r["candidates_all"]] == ["bueno"]
        assert r["_pasada_a"]["formato"] == "charla"
        assert r["_pasada_a"]["descartados_publicidad"][0][:2] == [3100, 3166]

    def test_el_foco_se_aplica_en_cada_ventana(self, monkeypatch):
        monkeypatch.setenv("SELECCION_POR_VENTANAS", "on")
        monkeypatch.setattr("services.moment_selector.time.sleep", lambda *_: None)
        c = _ClientePasadaA()
        r = _select(c, "clase")
        assert len(c.sistemas) >= 2
        assert all("FORMATO DEL VIDEO: CLASE" in s for s in c.sistemas)
        assert r["_pasada_a"]["modo"] == "ventanas"
        assert "aviso" not in [m["hook"] for m in r["viral_moments"]]

    def test_sin_formato_no_filtra_ni_cambia_el_prompt(self, monkeypatch):
        monkeypatch.delenv("SELECCION_POR_VENTANAS", raising=False)
        c = _ClientePasadaA()
        r = _select(c, None)
        assert "FORMATO DEL VIDEO" not in c.sistemas[0]
        assert "aviso" in [m["hook"] for m in r["viral_moments"]]
        assert "formato" not in r["_pasada_a"]


# ─── Versión efectiva de la caché ────────────────────────────────────────────

class TestVersionEfectiva:
    def test_prompt_version_nueva(self):
        from services.analysis_cache import PROMPT_VERSION

        assert PROMPT_VERSION not in ("v8", "v9", "v10")

    def test_flag_entra_en_la_version_efectiva(self, monkeypatch):
        from services.analysis_cache import PROMPT_VERSION, effective_prompt_version

        monkeypatch.delenv("SELECCION_POR_VENTANAS", raising=False)
        monkeypatch.delenv("FORMATOS", raising=False)
        assert effective_prompt_version("whisper_full") == f"{PROMPT_VERSION}+whisper_full"
        monkeypatch.setenv("FORMATOS", "on")
        assert effective_prompt_version("whisper_full") == f"{PROMPT_VERSION}+whisper_full+formatos"
        monkeypatch.setenv("SELECCION_POR_VENTANAS", "on")
        assert effective_prompt_version("whisper_full") == f"{PROMPT_VERSION}+whisper_full+formatos+ventanas"
