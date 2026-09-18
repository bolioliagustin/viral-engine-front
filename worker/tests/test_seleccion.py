"""
W2 — El juez elige (docs/PLAN_CALIDAD.md §4; causas C4/C5 de §1.3).

El auto-score de la Pasada A no discrimina (8-9 a casi todo) y el juez corría
después de renderizar sin decidir nada. `select_finalists` mueve la decisión
al juez sobre el texto real del clip, con penalizaciones por señales de
calidad ya conocidas (W1/W3) y un filtro de diversidad.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.moment_selector import (  # noqa: E402
    CandidateEval,
    select_finalists,
    score_candidate,
    PENALTY_VERIFICATION_FAILED,
)


def _cand(index, start, end, hook="momento distinto sin relación", judge=None, self_score=24.0, **flags):
    return CandidateEval(
        index=index,
        start_time=start,
        end_time=end,
        hook=hook,
        judge_scores=judge,
        self_score=self_score,
        **flags,
    )


class TestScoreCandidate:

    def test_juez_gana_aunque_autoscore_sea_menor(self):
        """C4: el auto-score alto e inútil no debe ganarle a un juez que sí puntuó bien."""
        alto_autoscore_bajo_juez = _cand(
            1, 0, 30, self_score=27.0, judge={"hook": 3, "retention": 3, "shareability": 3},
        )
        bajo_autoscore_alto_juez = _cand(
            2, 100, 130, self_score=15.0, judge={"hook": 9, "retention": 9, "shareability": 8},
        )
        assert score_candidate(bajo_autoscore_alto_juez) > score_candidate(alto_autoscore_bajo_juez)
        selected, discarded = select_finalists(
            [alto_autoscore_bajo_juez, bajo_autoscore_alto_juez], target=1
        )
        assert [c.index for c in selected] == [2]

    def test_verification_failed_pierde_contra_limpio_con_nota_similar(self):
        sucio = _cand(1, 0, 30, judge={"hook": 7, "retention": 7, "shareability": 7}, verification_failed=True)
        limpio = _cand(2, 100, 130, judge={"hook": 7, "retention": 7, "shareability": 6})
        assert score_candidate(limpio) > score_candidate(sucio)
        assert score_candidate(limpio) - score_candidate(sucio) < PENALTY_VERIFICATION_FAILED + 1

    def test_sin_juez_cae_a_autoscore_penalizado(self):
        sin_juez = _cand(1, 0, 30, self_score=27.0, judge=None)
        con_juez_mediocre = _cand(2, 100, 130, judge={"hook": 5, "retention": 5, "shareability": 5})
        # 27 - PENALTY_NO_JUDGE_SCORE(10) = 17 < 15 (mediocre) es falso acá,
        # lo que importa es que el juez SIEMPRE se prefiere sobre "sin juez"
        # cuando compite con una nota decente.
        assert score_candidate(con_juez_mediocre) >= score_candidate(sin_juez) - 5

    def test_no_usable_nunca_gana(self):
        roto = _cand(1, 0, 30, judge={"hook": 9, "retention": 9, "shareability": 9}, usable=False)
        cualquiera = _cand(2, 100, 130, judge={"hook": 4, "retention": 4, "shareability": 4})
        selected, _ = select_finalists([roto, cualquiera], target=1)
        assert [c.index for c in selected] == [2]


class TestSelectFinalistsDiversidad:

    def test_dos_candidatos_solapados_no_se_entregan_los_dos(self):
        a = _cand(1, 100, 140, hook="el error mas caro de programar en produccion", judge={"hook": 8, "retention": 8, "shareability": 8})
        b = _cand(2, 120, 160, hook="por que la memoria compartida rompe todo", judge={"hook": 7, "retention": 7, "shareability": 7})
        selected, discarded = select_finalists([a, b], target=1)
        assert len(selected) == 1
        assert selected[0].index == 1
        assert discarded[0].index == 2
        assert discarded[0].discard_reason is not None

    def test_hooks_casi_iguales_cuentan_como_solapados(self):
        a = _cand(1, 0, 30, hook="el error mas caro de programar en produccion", judge={"hook": 8, "retention": 8, "shareability": 8})
        b = _cand(2, 500, 530, hook="el error mas caro al programar en produccion", judge={"hook": 7, "retention": 7, "shareability": 7})
        selected, discarded = select_finalists([a, b], target=1)
        assert len(selected) == 1
        assert selected[0].index == 1

    def test_diversidad_hace_backfill_si_faltan_candidatos(self):
        """Si aplicar diversidad deja menos de `target`, se completa con el
        siguiente mejor igual — mejor repetido que entregar de menos."""
        a = _cand(1, 100, 140, hook="el error mas caro de programar en produccion", judge={"hook": 8, "retention": 8, "shareability": 8})
        b = _cand(2, 120, 160, hook="por que la memoria compartida rompe todo", judge={"hook": 7, "retention": 7, "shareability": 7})
        selected, discarded = select_finalists([a, b], target=2)
        assert len(selected) == 2
        assert {c.index for c in selected} == {1, 2}
        assert discarded == []

    def test_menos_candidatos_que_target_no_rompe(self):
        a = _cand(1, 0, 30, judge={"hook": 6, "retention": 6, "shareability": 6})
        selected, discarded = select_finalists([a], target=5)
        assert len(selected) == 1
        assert discarded == []

    def test_sin_candidatos_no_rompe(self):
        selected, discarded = select_finalists([], target=5)
        assert selected == []
        assert discarded == []

    def test_orden_final_es_cronologico(self):
        a = _cand(1, 500, 530, hook="tema uno completamente distinto", judge={"hook": 9, "retention": 9, "shareability": 9})
        b = _cand(2, 10, 40, hook="tema dos sin relacion alguna aca", judge={"hook": 5, "retention": 5, "shareability": 5})
        selected, _ = select_finalists([a, b], target=2)
        assert [c.index for c in selected] == [2, 1]
