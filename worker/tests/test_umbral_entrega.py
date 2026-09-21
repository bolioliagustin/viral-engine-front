"""
INT-5 (docs/PLAN_CALIDAD.md §9, decisión pendiente #2 de Agustín):
`eval/umbral_entrega.py` re-simula `select_finalists` sobre `w2_score` ya
calculado (sin CandidateEval completo) para barrer `DELIVERY_JUDGE_MIN` sin
llamar a ninguna API. Estos tests cubren `_deliver_at_threshold` con datos
sintéticos — el fetch real a `analysis_cache` no se testea acá (I/O).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "eval"))

from umbral_entrega import _Cand, _deliver_at_threshold  # noqa: E402

_TOPICS = [
    "el error mas caro de programar en produccion",
    "por que la memoria compartida rompe todo",
    "el algoritmo que cambio la industria del video",
    "como afecta el cambio climatico a la agricultura",
    "la historia oculta detras del primer telefono movil",
    "por que los gatos duermen tantas horas al dia",
    "el secreto mejor guardado de los chefs profesionales",
    "como funciona realmente la bolsa de valores",
    "el mayor error de los inversores novatos",
    "la ciencia detras de un buen cafe",
    "por que dormir bien mejora la memoria",
    "el impacto de las redes sociales en adolescentes",
    "como se entrena a un piloto de formula uno",
    "el misterio de las ballenas varadas en la costa",
    "la verdad sobre las dietas milagro",
    "por que fracasan la mayoria de los emprendimientos",
    "el origen olvidado de una palabra comun",
    "como se fabrica realmente el chocolate",
    "el error de calculo que casi arruino la mision",
    "la razon detras de un habito cotidiano extrano",
]


def _c(start, score, hook=None, topic=0):
    return _Cand(start_time=start, end_time=start + 30, hook=hook or _TOPICS[topic % len(_TOPICS)], w2_score=score)


class TestDeliverAtThreshold:
    def test_entrega_solo_los_que_pasan_el_umbral(self):
        cands = [_c(0, 20, "uno"), _c(100, 10, "dos"), _c(200, 25, "tres")]
        out = _deliver_at_threshold(cands, judge_min=15, floor=1, max_clips=10)
        assert {c.start_time for c in out} == {0, 200}

    def test_piso_completa_con_los_siguientes_mejores(self):
        cands = [_c(0, 20, "uno"), _c(100, 10, "dos"), _c(200, 8, "tres")]
        out = _deliver_at_threshold(cands, judge_min=15, floor=3, max_clips=10)
        assert len(out) == 3  # solo 1 pasa el umbral, backfill completa a 3

    def test_tope_no_se_supera_aunque_todos_pasen(self):
        cands = [_c(i * 100, 20, topic=i) for i in range(5)]
        out = _deliver_at_threshold(cands, judge_min=15, floor=1, max_clips=2)
        assert len(out) == 2
        # se entregan los de mejor score (todos iguales acá, así que cualquier 2)

    def test_diversidad_se_respeta_igual_que_select_finalists(self):
        a = _c(100, 20, "el error mas caro de programar en produccion")
        b = _c(105, 18, "el error mas caro de programar en produccion")  # solapa + mismo hook
        out = _deliver_at_threshold([a, b], judge_min=15, floor=1, max_clips=10)
        assert len(out) == 1
        assert out[0].start_time == 100

    def test_sin_candidatos_no_rompe(self):
        assert _deliver_at_threshold([], judge_min=15, floor=3, max_clips=12) == []

    def test_max_clips_none_no_topea(self):
        cands = [_c(i * 100, 20, topic=i) for i in range(20)]
        out = _deliver_at_threshold(cands, judge_min=15, floor=1, max_clips=None)
        assert len(out) == 20
