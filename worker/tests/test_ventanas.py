"""
W21 — Pasada A por Ventanas (docs/briefs/W21-pasada-a-por-ventanas.md, ADR 0009).

Funciones puras de `services/ventanas.py` (armado de Ventanas, reparto de
cupo, deduplicación, fusión) y la orquestación en
`moment_selector.select_moments` con un cliente falso: timestamps
absolutos, una Ventana que falla no tira el job, todas fallando cae a la
pasada única, y el flag entra en la versión efectiva del cache.
"""
import json
import os
import sys
import threading
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import ventanas as vt  # noqa: E402
from services.ventanas import (  # noqa: E402
    Ventana,
    armar_ventanas,
    deduplicar,
    fusionar_historias,
    repartir_cupo,
)


def _lineas(duracion_seg: float, paso: float = 10.0) -> list[dict]:
    """Una Línea cada `paso` segundos hasta `duracion_seg`."""
    out, t = [], 0.0
    while t < duracion_seg:
        fin = min(duracion_seg, t + paso)
        out.append({"start": t, "end": fin, "text": f"Oración en el segundo {int(t)}."})
        t = fin
    return out


# ─── Armado de Ventanas ──────────────────────────────────────────────────────

class TestArmarVentanas:
    def test_video_corto_una_sola_ventana(self):
        # Hasta VENTANA_MIN + 5 min (25 min con los defaults): una Ventana, como hoy.
        lineas = _lineas(25 * 60)
        vs = armar_ventanas(lineas, 25 * 60, minutos=20, solape_seg=180)
        assert len(vs) == 1
        assert vs[0].inicio == 0 and vs[0].fin == 25 * 60
        assert vs[0].lineas == lineas

    def test_apenas_pasado_el_margen_parte_en_dos(self):
        vs = armar_ventanas(_lineas(26 * 60), 26 * 60, minutos=20, solape_seg=180)
        # 26 min → 2 núcleos (20 + 6); 6 < 8 min se une a la anterior → 1
        assert len(vs) == 1
        vs = armar_ventanas(_lineas(29 * 60), 29 * 60, minutos=20, solape_seg=180)
        assert len(vs) == 2  # 20 + 9 (≥ 8 min)

    def test_bordes_y_solape(self):
        dur = 70 * 60
        vs = armar_ventanas(_lineas(dur), dur, minutos=20, solape_seg=180)
        # 70 min → núcleos [0,20), [20,40), [40,60), [60,70] (10 min ≥ 8)
        assert [(v.nucleo_inicio, v.nucleo_fin) for v in vs] == [
            (0, 1200), (1200, 2400), (2400, 3600), (3600, 4200),
        ]
        # Cada Ventana ve 90 s del otro lado de cada borde: 180 s compartidos.
        assert [(v.inicio, v.fin) for v in vs] == [
            (0, 1290), (1110, 2490), (2310, 3690), (3510, 4200),
        ]
        for a, b in zip(vs, vs[1:]):
            assert a.fin - b.inicio == 180

    def test_lineas_del_solape_estan_en_las_dos_ventanas(self):
        dur = 70 * 60
        vs = armar_ventanas(_lineas(dur), dur, minutos=20, solape_seg=180)
        en_borde = [ln for ln in vs[0].lineas if ln["start"] >= 1110]
        assert en_borde and all(ln in vs[1].lineas for ln in en_borde)
        # Ninguna Línea se pierde y los núcleos cubren todo el video.
        vistas = {ln["start"] for v in vs for ln in v.lineas}
        assert vistas == {ln["start"] for ln in _lineas(dur)}

    def test_ultima_ventana_corta_se_une_a_la_anterior(self):
        dur = 65 * 60  # núcleos 20, 20, 20, 5 → el de 5 min se une
        vs = armar_ventanas(_lineas(dur), dur, minutos=20, solape_seg=180)
        assert len(vs) == 3
        assert vs[-1].nucleo_inicio == 2400 and vs[-1].nucleo_fin == dur and vs[-1].fin == dur
        assert vs[-1].lineas[-1]["start"] == dur - 10

    def test_sin_lineas_una_ventana(self):
        vs = armar_ventanas([], 90 * 60, minutos=20, solape_seg=180)
        assert len(vs) == 1 and vs[0].lineas == []

    def test_duracion_sale_de_las_lineas_si_viene_corta(self):
        vs = armar_ventanas(_lineas(70 * 60), 0, minutos=20, solape_seg=180)
        assert vs[-1].fin == 70 * 60

    def test_timestamps_de_las_lineas_no_se_tocan(self):
        dur = 70 * 60
        lineas = _lineas(dur)
        vs = armar_ventanas(lineas, dur, minutos=20, solape_seg=180)
        ln = vs[2].lineas[0]
        assert ln["start"] >= 2310  # absoluto, no relativo a la Ventana
        assert ln is next(x for x in lineas if x["start"] == ln["start"])

    def test_defaults_desde_el_entorno(self, monkeypatch):
        monkeypatch.setenv("VENTANA_MIN", "30")
        monkeypatch.setenv("VENTANA_SOLAPE_SEG", "60")
        vs = armar_ventanas(_lineas(90 * 60), 90 * 60)
        assert len(vs) == 3 and vs[0].fin == 1830


# ─── Reparto de cupo ─────────────────────────────────────────────────────────

def _vs(*minutos) -> list[Ventana]:
    out, t = [], 0.0
    for i, m in enumerate(minutos):
        out.append(Ventana(i, t, t + m * 60, t, t + m * 60))
        t += m * 60
    return out


class TestRepartirCupo:
    def test_proporcional_y_total_exacto(self):
        cupos = repartir_cupo(_vs(20, 20, 20, 10), 30)
        assert sum(cupos) == 30
        # 30 × 20/70 = 8,6 → dos de 9 y una de 8 (resto mayor); la de 10 min, 4–5.
        assert max(cupos[:3]) - min(cupos[:3]) <= 1 and cupos[3] < min(cupos[:3])

    def test_minimo_tres_por_ventana(self):
        cupos = repartir_cupo(_vs(20, 8.5), 14)
        assert sum(cupos) == 14 and min(cupos) >= 3
        cupos = repartir_cupo(_vs(20, 20, 20, 8), 12)
        assert sum(cupos) == 12 and cupos == [3, 3, 3, 3]

    @pytest.mark.parametrize("minutos", range(26, 151, 7))
    def test_total_nunca_pasa_candidate_count(self, minutos):
        from services.moment_selector import candidate_count

        dur = minutos * 60
        vs = armar_ventanas(_lineas(dur, paso=30), dur)
        total = candidate_count(dur)
        cupos = repartir_cupo(vs, total)
        assert sum(cupos) <= 30
        assert sum(cupos) == total
        assert all(c >= 3 for c in cupos)

    def test_una_ventana_recibe_todo(self):
        assert repartir_cupo(_vs(25), 12) == [12]


# ─── Deduplicación ───────────────────────────────────────────────────────────

def _m(ini, fin, ventana=0, puesto=0, **extra):
    return {"start_time": ini, "end_time": fin, "ventana": ventana, "puesto_en_ventana": puesto, **extra}


class TestDeduplicar:
    def test_se_queda_el_que_contiene_al_otro(self):
        grande, chico = _m(100, 200, ventana=0, puesto=5), _m(120, 180, ventana=1, puesto=0)
        vivos, fuera = deduplicar([chico, grande])
        assert vivos == [grande] and fuera == [chico]

    def test_sin_contencion_gana_el_mejor_posicionado(self):
        a, b = _m(100, 160, ventana=0, puesto=4), _m(120, 180, ventana=1, puesto=1)
        vivos, _ = deduplicar([a, b])
        assert vivos == [b]

    def test_solape_menor_al_umbral_quedan_los_dos(self):
        a, b = _m(100, 160), _m(140, 220, ventana=1)  # 20 s de 60 → 33 %
        vivos, fuera = deduplicar([a, b])
        assert vivos == [a, b] and fuera == []

    def test_el_umbral_se_mide_sobre_el_mas_corto(self):
        largo, corto = _m(100, 220), _m(200, 230, ventana=1, puesto=0)  # 20 de 30 s → 67 %
        vivos, _ = deduplicar([largo, corto])
        assert len(vivos) == 1

    def test_tres_copias_del_mismo_momento(self):
        cs = [_m(100, 160, ventana=0, puesto=2), _m(101, 161, ventana=1, puesto=0), _m(99, 159, ventana=0, puesto=3)]
        vivos, fuera = deduplicar(cs)
        assert len(vivos) == 1 and len(fuera) == 2


# ─── Fusión de historias ─────────────────────────────────────────────────────

class TestFusionarHistorias:
    VS = _vs(20, 20)  # sin solape en la fixture; los tests con solape arman el suyo

    def test_fusiona_contiguos_de_la_misma_ventana(self):
        a = _m(100, 150, puesto=2, verification={"first_phrase_in_audio": "arranca", "last_phrase_in_audio": "a medias"})
        b = _m(153, 200, puesto=0, hook="el remate", verification={"first_phrase_in_audio": "sigue", "last_phrase_in_audio": "remata."})
        out, n = fusionar_historias([b, a], self.VS, 120)
        assert n == 1 and len(out) == 1
        f = out[0]
        assert (f["start_time"], f["end_time"]) == (100, 200)
        assert f["verification"]["first_phrase_in_audio"] == "arranca"
        assert f["verification"]["last_phrase_in_audio"] == "remata."
        assert f["hook"] == "el remate"  # base: el mejor posicionado
        assert f["fusionado_de"] == [[100, 150], [153, 200]]

    def test_no_fusiona_si_pasa_la_duracion_maxima(self):
        out, n = fusionar_historias([_m(100, 170), _m(172, 240)], self.VS, 120)
        assert n == 0 and len(out) == 2

    def test_no_fusiona_con_hueco_grande(self):
        out, n = fusionar_historias([_m(100, 150), _m(160, 200)], self.VS, 120)
        assert n == 0 and len(out) == 2

    def test_no_fusiona_ventanas_distintas_fuera_del_solape(self):
        vs = armar_ventanas(_lineas(70 * 60), 70 * 60, minutos=20, solape_seg=180)
        out, n = fusionar_historias([_m(300, 350, ventana=0), _m(352, 400, ventana=1)], vs, 120)
        assert n == 0 and len(out) == 2

    def test_fusiona_ventanas_distintas_en_el_solape(self):
        vs = armar_ventanas(_lineas(70 * 60), 70 * 60, minutos=20, solape_seg=180)
        # Borde en 1200 s; el solape de las Ventanas 0 y 1 es [1110, 1290].
        out, n = fusionar_historias([_m(1150, 1200, ventana=0), _m(1202, 1260, ventana=1)], vs, 120)
        assert n == 1 and (out[0]["start_time"], out[0]["end_time"]) == (1150, 1260)

    def test_encadena_tres_partes(self):
        out, n = fusionar_historias([_m(100, 130), _m(131, 160), _m(162, 190)], self.VS, 120)
        assert n == 2 and len(out) == 1
        assert out[0]["fusionado_de"] == [[100, 130], [131, 160], [162, 190]]


# ─── Orquestación en select_moments ──────────────────────────────────────────

class _ClienteFalso:
    """
    Responde cada llamada con 3 candidatos dentro del tramo que ve (lo lee
    del contexto). `falla_ventanas`: índices (1-based, como en el prompt)
    que siempre fallan; "todas" hace fallar cualquier llamada por Ventana.
    """

    def __init__(self, falla_ventanas=(), dur=70 * 60):
        self.falla = falla_ventanas
        self.dur = dur
        self.llamadas: list[str] = []
        self.lock = threading.Lock()
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        user = kwargs["messages"][1]["content"]
        with self.lock:
            self.llamadas.append(user)
        import re

        m = re.search(r"VENTANA (\d+) DE (\d+): esta llamada ve SOLO el tramo del segundo (\d+) al (\d+)", user)
        if m:
            idx, ini, fin = int(m.group(1)), int(m.group(3)), int(m.group(4))
            if self.falla == "todas" or idx in self.falla:
                raise RuntimeError(f"502 ventana {idx}")
        else:
            idx, ini, fin = 0, 0, self.dur
        paso = (fin - ini) / 4
        momentos = [
            {"start_time": int(ini + paso * k), "end_time": int(ini + paso * k + 40),
             "hook": f"v{idx} m{k}", "scores": {"hook": 7, "retention": 6, "shareability": 6}}
            for k in (1, 2, 3)
        ]
        content = json.dumps({"video_title": f"t{idx}", "summary": "s", "viral_moments": momentos})
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=500, total_tokens=1500,
                                  completion_tokens_details=None),
        )


def _select(cliente, dur=70 * 60, lineas=True):
    from services.moment_selector import select_moments

    transcript = {"source": "whisper_full", "lines": _lineas(dur) if lineas else [], "segments": []}
    return select_moments(
        transcript_text="(transcript completo)", video_info={"title": "x"}, duration=dur,
        category="podcast", language="es", client=cliente, model="google/gemini-3.5-flash",
        max_retries=1, transcript=transcript,
    )


@pytest.fixture
def ventanas_on(monkeypatch):
    monkeypatch.setenv("SELECCION_POR_VENTANAS", "on")
    monkeypatch.setattr("services.moment_selector.time.sleep", lambda *_: None)


class TestSelectMomentsPorVentanas:
    def test_flag_off_una_sola_llamada(self, monkeypatch):
        monkeypatch.delenv("SELECCION_POR_VENTANAS", raising=False)
        c = _ClienteFalso()
        r = _select(c)
        assert len(c.llamadas) == 1 and "VENTANA" not in c.llamadas[0]
        assert r["_pasada_a"]["modo"] == "unica"

    def test_una_llamada_por_ventana_con_timestamps_absolutos(self, ventanas_on):
        c = _ClienteFalso()
        r = _select(c)
        assert len(c.llamadas) == 4
        tercera = next(u for u in c.llamadas if "VENTANA 3 DE 4" in u)
        # La Ventana 3 ve Líneas con su segundo absoluto, no desde 0.
        assert "[2310-2320]" in tercera and "[0-10]" not in tercera
        assert "de un video de 4200 segundos" in tercera
        assert r["_pasada_a"]["modo"] == "ventanas"
        assert r["_pasada_a"]["ventanas"] == [[0, 1290], [1110, 2490], [2310, 3690], [3510, 4200]]
        assert sum(r["_pasada_a"]["cupos"]) <= 30
        # Candidatos de todas las Ventanas, con su campo `ventana`, y en candidates_all.
        assert {m["ventana"] for m in r["viral_moments"]} == {0, 1, 2, 3}
        assert all("ventana" in m for m in r["candidates_all"])
        assert max(m["start_time"] for m in r["viral_moments"]) > 3600
        assert r["_pasada_a"]["costo_usd"] > 0

    def test_una_ventana_falla_el_job_sigue(self, ventanas_on):
        c = _ClienteFalso(falla_ventanas=(2,))
        r = _select(c)
        assert r["_pasada_a"]["modo"] == "ventanas"
        assert r["_pasada_a"]["ventanas_fallidas"] == [1]
        assert {m["ventana"] for m in r["viral_moments"]} == {0, 2, 3}

    def test_todas_fallan_cae_a_la_pasada_unica(self, ventanas_on):
        c = _ClienteFalso(falla_ventanas="todas")
        r = _select(c)
        assert r["_pasada_a"]["modo"] == "unica_respaldo"
        assert "(transcript completo)" in c.llamadas[-1]
        assert r["viral_moments"] and all("ventana" not in m for m in r["viral_moments"])

    def test_video_corto_no_parte(self, ventanas_on):
        c = _ClienteFalso(dur=20 * 60)
        r = _select(c, dur=20 * 60)
        assert len(c.llamadas) == 1 and r["_pasada_a"]["modo"] == "unica"

    def test_sin_lineas_no_parte(self, ventanas_on):
        c = _ClienteFalso()
        r = _select(c, lineas=False)
        assert len(c.llamadas) == 1 and r["_pasada_a"]["modo"] == "unica"

    def test_descarta_candidatos_fuera_de_su_ventana(self, ventanas_on, monkeypatch):
        c = _ClienteFalso()
        original = c._create

        def _con_basura(**kw):
            resp = original(**kw)
            d = json.loads(resp.choices[0].message.content)
            d["viral_moments"].append({"start_time": 99999, "end_time": 100040, "hook": "mm:ss mal convertido"})
            resp.choices[0].message.content = json.dumps(d)
            return resp

        c.chat.completions.create = _con_basura
        r = _select(c)
        assert all(m["start_time"] < 4200 for m in r["viral_moments"])

    def test_el_costo_llega_al_rollup_del_job(self, ventanas_on, monkeypatch):
        # Las llamadas en paralelo heredan el job (in_current_context).
        from context.job_context import clear_job_context, set_job_context
        from services import usage_tracker as ut

        monkeypatch.setenv("EVAL_DRY_RUN", "1")
        set_job_context(job_id="test-w21-rollup")
        try:
            _select(_ClienteFalso())
        finally:
            clear_job_context()
        rollup = ut._job_rollups.pop("test-w21-rollup", {})
        assert rollup.get("by_task", {}).get("analysis", 0) > 0


# ─── Versión efectiva del cache ──────────────────────────────────────────────

class TestVersionCache:
    def test_prompt_version_nuevo_y_nunca_usado(self):
        from services.analysis_cache import PROMPT_VERSION

        assert PROMPT_VERSION not in ("v8", "v9")

    def test_flag_entra_en_la_version_efectiva(self, monkeypatch):
        from services.analysis_cache import PROMPT_VERSION, effective_prompt_version

        monkeypatch.delenv("SELECCION_POR_VENTANAS", raising=False)
        apagado = effective_prompt_version("whisper_full")
        assert apagado == f"{PROMPT_VERSION}+whisper_full"
        monkeypatch.setenv("SELECCION_POR_VENTANAS", "on")
        prendido = effective_prompt_version("whisper_full")
        assert prendido == f"{PROMPT_VERSION}+whisper_full+ventanas"
        assert prendido != apagado
        # flags explícitos pisan el entorno
        assert effective_prompt_version("whisper_full", flags=()) == apagado

    def test_flag_valores(self, monkeypatch):
        for valor, esperado in (("on", True), ("off", False), ("true", True), ("", False)):
            monkeypatch.setenv("SELECCION_POR_VENTANAS", valor)
            assert vt.seleccion_por_ventanas_enabled() is esperado


# ─── Eval: medir la fusión con una sola corrida ──────────────────────────────

def test_candidatos_sin_fusion_deshace_las_fusiones():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "eval"))
    import seleccion

    cs = [
        {"start_time": 100, "end_time": 200, "rank_score": 20, "fusionado_de": [[100, 150], [153, 200]]},
        {"start_time": 300, "end_time": 340, "rank_score": 18},
    ]
    sin = seleccion.candidatos_sin_fusion(cs)
    assert [(c["start_time"], c["end_time"]) for c in sin] == [(100, 150), (153, 200), (300, 340)]
    assert all("fusionado_de" not in c for c in sin)
