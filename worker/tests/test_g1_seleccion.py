"""
W22 — `eval/g1_seleccion.py`: las dos corridas se miden con la misma vara
(Referencias validadas, recalculando aunque el JSON traiga métricas viejas
contra borradores) y se aplican los umbrales vigentes de G1
(docs/PLAN_MEJORA.md §7, ajustado el 28-sep).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "eval"))

import g1_seleccion as g1  # noqa: E402

LARGO = 90 * 60
CORTO = 20 * 60


def _ref(rid, ini, fin, calidad="A", validada=True):
    r = {"id": rid, "inicio": ini - 5, "fin": fin + 5, "nucleo_inicio": ini, "nucleo_fin": fin, "calidad": calidad}
    if validada:
        r["validado_por"] = "agustin"
    return r


def _cand(ini, fin, score=20):
    return {"start_time": ini, "end_time": fin, "rank_score": score}


def _repartidos(dur, n=8, largo=60):
    """n candidatos repartidos parejo en el video (min_cuarto alto)."""
    paso = dur / n
    return [_cand(int(paso * k + 10), int(paso * k + 10 + largo)) for k in range(n)]


def _video(vid, yt, dur, candidatos, *, costo=0.10, segundos=30, formato="charla", formato_rep=None,
           reps=3, agregado_viejo=None):
    reps_ = []
    for i in range(reps):
        r = {"rep": i + 1, "candidatos": candidatos, "costo_por_tarea": {"classifier": 0.0001, "analysis": costo},
             "segundos": segundos}
        if formato_rep:
            r["formato"] = formato_rep
        reps_.append(r)
    v = {"id": vid, "youtube_id": yt, "formato": formato, "duracion_sec": dur, "reps": reps_}
    if agregado_viejo is not None:
        v["agregado"] = agregado_viejo
    return v


def _corrida(*videos):
    return {"tier": "seleccion", "reps": 3, "videos": list(videos)}


def _docs_largo(excluir=None):
    # 4 Referencias A validadas + 1 borrador A que no cuenta.
    return {
        "L1": {
            "youtube_id": "L1",
            "momentos": [
                _ref("R1", 100, 150), _ref("R2", 1500, 1560), _ref("R3", 3000, 3050), _ref("R4", 4500, 4560),
                _ref("B1", 5000, 5050, validada=False),
            ],
            "excluir": excluir or [],
        },
    }


def _cubre(refs_ids, docs, yt="L1", extra=()):
    """Candidatos que contienen el núcleo de las Referencias pedidas, más relleno repartido."""
    por_id = {m["id"]: m for m in docs[yt]["momentos"]}
    cands = [_cand(por_id[r]["nucleo_inicio"] - 3, por_id[r]["nucleo_fin"] + 3, 25) for r in refs_ids]
    return cands + list(extra)


class TestMismaVara:
    def test_recalcula_el_antes_aunque_traiga_metricas_viejas(self):
        docs = _docs_largo()
        # El JSON del "antes" dice 99 % (medido contra borradores): se ignora.
        viejo = {"recall_completo": {"media": 0.99}, "min_cuarto": {"media": 0.5}}
        antes = _corrida(_video("largo", "L1", LARGO, _cubre(["R1"], docs, extra=_repartidos(LARGO)),
                                agregado_viejo=viejo))
        despues = _corrida(_video("largo", "L1", LARGO, _cubre(["R1", "R2", "R3"], docs, extra=_repartidos(LARGO))))
        filas, r = g1.comparar(antes, despues, docs=docs)
        assert r["recall_macro_antes"] == pytest.approx(0.25)
        assert r["recall_macro_despues"] == pytest.approx(0.75)
        assert "25% → **75%**" in filas[2]

    def test_los_borradores_no_cuentan(self):
        docs = _docs_largo()
        corrida = _corrida(_video("largo", "L1", LARGO, _cubre(["R1"], docs) + [_cand(4997, 5053)]))
        r = g1.misma_vara(corrida, docs=docs)
        m = r["videos"][0]["reps"][0]["metricas"]
        assert m["n_referencias_a"] == 4
        assert m["recall_completo"] == pytest.approx(0.25)

    def test_no_modifica_las_corridas_originales(self):
        docs = _docs_largo()
        antes = _corrida(_video("largo", "L1", LARGO, _cubre(["R1"], docs), agregado_viejo={"x": 1}))
        g1.comparar(antes, antes, docs=docs)
        assert antes["videos"][0]["agregado"] == {"x": 1}


class TestUmbrales:
    def _comparar(self, refs_despues, *, costo_antes=0.10, costo_despues=0.12, excluir=None, extra=None):
        docs = _docs_largo(excluir)
        antes = _corrida(_video("largo", "L1", LARGO, _cubre(["R1"], docs, extra=_repartidos(LARGO)), costo=costo_antes))
        despues = _corrida(_video(
            "largo", "L1", LARGO,
            _cubre(refs_despues, docs, extra=list(extra or []) + _repartidos(LARGO)), costo=costo_despues,
        ))
        return g1.comparar(antes, despues, docs=docs)[1]

    def test_pasa_todo(self):
        r = self._comparar(["R1", "R2", "R3"])
        assert all(r["pasa"].values()), r["pasa"]

    def test_recall_largos_debajo_de_55(self):
        r = self._comparar(["R1", "R2"])  # 50 %
        assert r["pasa"]["recall_completo ≥ 55% (> 60 min)"] is False

    def test_min_cuarto(self):
        docs = _docs_largo()
        amontonados = [_cand(100 + 70 * k, 160 + 70 * k) for k in range(10)]
        antes = _corrida(_video("largo", "L1", LARGO, amontonados))
        r = g1.comparar(antes, antes, docs=docs)[1]
        assert r["min_cuarto_largos"] == 0
        assert r["pasa"]["min_cuarto ≥ 15% (> 60 min)"] is False

    @pytest.mark.parametrize("costo,pasa", [(0.135, True), (0.14, False)])
    def test_costo_hasta_35_por_ciento(self, costo, pasa):
        r = self._comparar(["R1", "R2", "R3"], costo_despues=costo)
        assert r["pasa"]["costo Pasada A ≤ +35%"] is pasa

    def test_publicidad_cuenta_solo_los_avisos(self):
        excluir = [
            {"inicio": 2000, "fin": 2060, "motivo": "publicidad (aviso de DiDi)"},
            {"inicio": 0, "fin": 60, "motivo": "Introducción y presentación del programa"},
        ]
        r = self._comparar(["R1", "R2", "R3"], excluir=excluir, extra=[_cand(5, 55)])
        assert r["candidatos_en_publicidad"] == 0
        assert r["pasa"]["0 candidatos en publicidad"] is True
        r = self._comparar(["R1", "R2", "R3"], excluir=excluir, extra=[_cand(2005, 2055)])
        assert r["candidatos_en_publicidad"] == 3  # uno por repetición
        assert r["pasa"]["0 candidatos en publicidad"] is False

    def test_historias_partidas(self):
        # R2 partida en dos candidatos que juntos cubren el núcleo.
        r = self._comparar(["R1", "R3", "R4"], extra=[_cand(1495, 1530), _cand(1530, 1565)])
        assert r["historias_partidas_max"] == 1
        assert r["pasa"]["historias_partidas ≤ 1 por video"] is True


class TestRegresionEnCortos:
    CLAVE = "sin regresión > 5 pp en cortos (< 30 min), salvo ≤ 1 Referencia"

    def _docs(self):
        return {"C1": {"youtube_id": "C1", "excluir": [], "momentos": [
            _ref("R1", 60, 100), _ref("R2", 300, 340), _ref("R3", 600, 640), _ref("R4", 900, 940),
        ]}}

    def _r(self, antes_ids, despues_ids):
        docs = self._docs()
        antes = _corrida(_video("corto", "C1", CORTO, _cubre(antes_ids, docs, yt="C1")))
        despues = _corrida(_video("corto", "C1", CORTO, _cubre(despues_ids, docs, yt="C1")))
        return g1.comparar(antes, despues, docs=docs)[1]

    def test_perder_una_referencia_se_tolera(self):
        r = self._r(["R1", "R2", "R3", "R4"], ["R1", "R2", "R3"])
        assert r["regresion_cortos_pp"] == pytest.approx(25)
        assert r["regresion_cortos_refs"] == pytest.approx(1)
        assert r["pasa"][self.CLAVE] is True

    def test_perder_dos_referencias_no(self):
        r = self._r(["R1", "R2", "R3", "R4"], ["R1", "R2"])
        assert r["pasa"][self.CLAVE] is False

    def test_mejora_no_es_regresion(self):
        r = self._r(["R1"], ["R1", "R2"])
        assert r["regresion_cortos_pp"] < 0 and r["pasa"][self.CLAVE] is True


class TestInformacion:
    def test_acierto_del_clasificador_y_recall_por_formato(self):
        docs = _docs_largo()
        docs["C1"] = {"youtube_id": "C1", "excluir": [], "momentos": [_ref("R1", 60, 100)]}
        antes = _corrida(
            _video("largo", "L1", LARGO, _cubre(["R1"], docs, extra=_repartidos(LARGO)), formato="charla"),
            _video("corto", "C1", CORTO, [_cand(55, 105)], formato="monólogo"),
        )
        despues = _corrida(
            _video("largo", "L1", LARGO, _cubre(["R1", "R2"], docs, extra=_repartidos(LARGO)),
                   formato="charla", formato_rep="charla"),
            _video("corto", "C1", CORTO, [_cand(55, 105)], formato="monólogo", formato_rep="entrevista"),
        )
        filas, r = g1.comparar(antes, despues, docs=docs)
        assert r["acierto_clasificador"] == (3, 6)
        assert r["recall_por_formato"]["charla"]["despues"] == pytest.approx(0.5)
        assert r["recall_por_formato"]["monologo"]["antes"] == pytest.approx(1.0)

    def test_sin_formato_por_repeticion(self):
        docs = _docs_largo()
        c = _corrida(_video("largo", "L1", LARGO, _cubre(["R1"], docs)))
        assert g1.comparar(c, c, docs=docs)[1]["acierto_clasificador"] is None
