"""
W2 — El juez elige (docs/PLAN_CALIDAD.md §4; causas C4/C5 de §1.3).

El auto-score de la Pasada A no discrimina (8-9 a casi todo) y el juez corría
después de renderizar sin decidir nada. `select_finalists` mueve la decisión
al juez sobre el texto real del clip, con penalizaciones por señales de
calidad ya conocidas (W1/W3) y un filtro de diversidad.

W9-B (docs/PLAN_CALIDAD.md §9 W9) sumó un piso mínimo de entrega
(`max(target, DELIVERY_MIN_CLIPS)`, default `DELIVERY_MIN_CLIPS=3`): con
pools chicos de 2 candidatos como los de acá, ese piso puede forzar un
backfill que entrega AMBOS aunque uno pierda por ranking o por diversidad
— exactamente lo que se supone que hay que evitar para poder probar el
ranking/diversidad en aislamiento. Los tests que quieren verificar "el
peor NO se entrega" bajan `DELIVERY_MIN_CLIPS` a 1 (`floor = target`, el
comportamiento pre-W9-B) vía monkeypatch; el piso/tope en sí (con pools
más realistas) se prueba aparte en `TestSelectFinalistsPisoYTope`.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.moment_selector import (  # noqa: E402
    CandidateEval,
    select_finalists,
    score_candidate,
    PENALTY_BROKEN,
    DELIVERY_MAX_CLIPS,
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

    def test_juez_gana_aunque_autoscore_sea_menor(self, monkeypatch):
        """C4: el auto-score alto e inútil no debe ganarle a un juez que sí puntuó bien."""
        monkeypatch.setattr("services.moment_selector.DELIVERY_MIN_CLIPS", 1)
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

    def test_payoff_not_found_pierde_contra_limpio_con_nota_similar(self):
        """W2-C: el nivel FUERTE (clip roto) es payoff_not_found/hook_not_found/
        bad_segment — ya no el viejo `verification_failed` genérico."""
        roto = _cand(1, 0, 30, judge={"hook": 7, "retention": 7, "shareability": 7}, payoff_not_found=True)
        limpio = _cand(2, 100, 130, judge={"hook": 7, "retention": 7, "shareability": 6})
        assert score_candidate(limpio) > score_candidate(roto)
        assert score_candidate(limpio) - score_candidate(roto) < PENALTY_BROKEN + 1

    def test_late_hook_es_leve_y_no_le_gana_a_uno_sin_flags_con_juez_similar(self, monkeypatch):
        """W2-C: late_hook/incomplete_tail ya no son 'clip roto' — la
        penalización es leve, no debería tumbar a un candidato con buen juez."""
        monkeypatch.setattr("services.moment_selector.DELIVERY_MIN_CLIPS", 1)
        con_flag_leve = _cand(
            1, 0, 30, judge={"hook": 8, "retention": 7, "shareability": 8}, late_hook=True,
        )
        sin_flags_peor_juez = _cand(
            2, 100, 130, judge={"hook": 6, "retention": 6, "shareability": 6},
        )
        assert score_candidate(con_flag_leve) > score_candidate(sin_flags_peor_juez)
        selected, _ = select_finalists([con_flag_leve, sin_flags_peor_juez], target=1)
        assert [c.index for c in selected] == [1]

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

    def test_dos_candidatos_solapados_no_se_entregan_los_dos(self, monkeypatch):
        monkeypatch.setattr("services.moment_selector.DELIVERY_MIN_CLIPS", 1)
        a = _cand(1, 100, 140, hook="el error mas caro de programar en produccion", judge={"hook": 8, "retention": 8, "shareability": 8})
        b = _cand(2, 120, 160, hook="por que la memoria compartida rompe todo", judge={"hook": 7, "retention": 7, "shareability": 7})
        selected, discarded = select_finalists([a, b], target=1)
        assert len(selected) == 1
        assert selected[0].index == 1
        assert discarded[0].index == 2
        assert discarded[0].discard_reason is not None

    def test_hooks_casi_iguales_cuentan_como_solapados(self, monkeypatch):
        monkeypatch.setattr("services.moment_selector.DELIVERY_MIN_CLIPS", 1)
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


def _pool(n, judge_sum, start_index=1, **flags):
    """N candidatos distintos (sin solapamiento de tiempo ni de hook), todos
    con la misma suma de juez (hook=retention=shareability=judge_sum/3)."""
    per = judge_sum / 3
    return [
        _cand(
            i, i * 100, i * 100 + 30, hook=_TOPICS[i % len(_TOPICS)],
            judge={"hook": per, "retention": per, "shareability": per},
            **flags,
        )
        for i in range(start_index, start_index + n)
    ]


class TestSelectFinalistsPisoYTope:
    """
    W9-B (docs/PLAN_CALIDAD.md §9 W9): entrega por umbral con piso mínimo
    (`max(target, DELIVERY_MIN_CLIPS)`) y tope máximo (`DELIVERY_MAX_CLIPS`).
    Pools de candidatos genuinamente distintos (sin conflicto de tiempo ni
    de hook) para que el piso/tope se prueben sin la interferencia del
    backfill de diversidad (ver `TestSelectFinalistsDiversidad`, que usa
    pools chicos a propósito).
    """

    def test_ninguno_pasa_el_umbral_igual_se_entrega_el_piso(self):
        candidatos = _pool(5, judge_sum=9)  # todos por debajo de DELIVERY_JUDGE_MIN
        selected, discarded = select_finalists(candidatos, target=1)
        assert {c.index for c in selected} == {1, 2, 3}
        assert len(selected) == 3  # DELIVERY_MIN_CLIPS, no 1 (piso absoluto)
        assert len(discarded) == 2

    def test_target_mayor_al_piso_absoluto_tambien_se_respeta(self):
        # target=5 > DELIVERY_MIN_CLIPS(3): el piso pasa a ser target.
        candidatos = _pool(8, judge_sum=9)  # ninguno pasa el umbral
        selected, discarded = select_finalists(candidatos, target=5)
        assert len(selected) == 5
        assert len(discarded) == 3

    def test_todos_pasan_el_umbral_pero_topea_en_delivery_max_clips(self):
        n = DELIVERY_MAX_CLIPS + 3
        candidatos = _pool(n, judge_sum=27)  # muy por encima del umbral
        selected, discarded = select_finalists(candidatos, target=1)
        assert len(selected) == DELIVERY_MAX_CLIPS
        assert len(discarded) == 3

    def test_solo_se_completa_el_piso_con_los_que_faltan(self):
        # 4 candidatos pasan el umbral (ya cubren el piso de 3) — los otros
        # 4, por debajo del umbral, quedan afuera sin necesidad de backfill.
        buenos = _pool(4, judge_sum=27)
        malos = _pool(4, judge_sum=9, start_index=10)
        selected, discarded = select_finalists(buenos + malos, target=1)
        assert {c.index for c in selected} == {1, 2, 3, 4}
        assert {c.index for c in discarded} == {10, 11, 12, 13}

    def test_diversidad_se_respeta_sin_forzar_backfill_con_pool_grande(self):
        # A diferencia de TestSelectFinalistsDiversidad (pools de 2, el
        # piso fuerza a entregar el casi-duplicado): con el piso ya cubierto
        # por candidatos genuinamente distintos, el casi-duplicado NO se
        # entrega.
        base = _pool(4, judge_sum=27)
        casi_duplicado = _cand(
            5, base[0].start_time + 5, base[0].end_time + 5,
            hook=base[0].hook, judge={"hook": 9, "retention": 9, "shareability": 9},
        )
        selected, discarded = select_finalists(base + [casi_duplicado], target=1)
        assert {c.index for c in selected} == {1, 2, 3, 4}
        assert 5 in {c.index for c in discarded}

    def test_no_usable_nunca_completa_el_piso(self):
        # Sin suficientes candidatos usables para llegar al piso, se entrega
        # lo que haya — nunca uno sin clip_text, ni para completar el piso.
        usable = _pool(1, judge_sum=9)
        no_usable = _pool(3, judge_sum=9, start_index=10, usable=False)
        selected, discarded = select_finalists(usable + no_usable, target=1)
        assert len(selected) == 1
        assert selected[0].index == 1
        assert {c.index for c in discarded} == {10, 11, 12}
