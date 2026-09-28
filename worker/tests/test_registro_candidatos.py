"""
Registro de candidatos (W30): una fila por Candidato evaluado en
`candidate_evals`, no fatal y sin escribir en dry-run.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services import registro_candidatos as rc
from services.moment_selector import CandidateEval

COLUMNAS = {
    "job_id", "video_id", "video_duration", "candidate_index", "delivery_index",
    "proposed_start", "proposed_end", "final_start", "final_end", "hook", "lines_text",
    "clip_text", "rank_score", "self_score", "judge_hook", "judge_retention",
    "judge_shareability", "judge_reasoning", "jev_rank_score", "jev_hook", "jev_retention",
    "jev_shareability", "jev_confidence", "usable", "flags", "w2_score", "selected",
    "discard_reason", "ranker", "prompt_version", "analysis_model", "judge_model",
    "jev_model", "category", "formato",
}

TRANSCRIPT = {
    "source": "whisper_full",
    "lines": [
        {"start": 0.0, "end": 9.0, "text": "Arranca la charla."},
        {"start": 9.0, "end": 21.0, "text": "Te cuento lo que me pasó en el aeropuerto."},
        {"start": 21.0, "end": 40.0, "text": "Y ahí me di cuenta de todo."},
        {"start": 40.0, "end": 60.0, "text": "Pasemos a otro tema."},
    ],
    "segments": [{"start": 0.0, "end": 60.0, "text": "todo junto."}],
}

JEV = {
    "scores_0_4": {"gancho": 3.0, "retencion": 2.5, "compartir": 2.0},
    "confidence": {"gancho": 0.9, "retencion": 0.8, "compartir": 0.7},
    "sum_0_12": 7.5,
    "rank_score": 18.75,
    "confidence_avg": 0.8,
    "latency_ms": 900,
    "model": "jev-2026-09",
}


def _cand(i, **kw):
    base = dict(
        index=i, start_time=10.0 * i, end_time=10.0 * i + 25.0, hook=f"hook {i}",
        judge_scores={"hook": 8, "retention": 7, "shareability": 6, "reasoning": "buen remate"},
        self_score=24.0,
    )
    base.update(kw)
    return CandidateEval(**base)


def _prepared(start, end, text="texto whisper"):
    return SimpleNamespace(final_start=start, final_end=end, clip_text_final=text)


def _registrar(cands, selected, *, jev_by_index=None, prepared=None):
    return rc.registrar_candidatos(
        job_id="job-1", video_id="B60BHDNFNxM", video_duration=600.0,
        candidates=cands, selected=selected,
        moment_by_index={c.index: SimpleNamespace(category="podcast") for c in cands},
        prepared_by_index=prepared if prepared is not None else {c.index: _prepared(c.start_time, c.end_time) for c in cands},
        jev_by_index=jev_by_index or {},
        transcript=TRANSCRIPT,
    )


@pytest.fixture
def capturar():
    with patch.object(rc, "guardar_filas", return_value=True) as m:
        yield m


class TestFilas:
    def test_una_fila_por_candidato_con_todos_los_campos(self, capturar):
        c1 = _cand(1)
        c2 = _cand(2, discard_reason="bajo el umbral", late_hook=True)
        c3 = _cand(3)
        n = _registrar([c1, c2, c3], selected=[c3, c1], jev_by_index={1: JEV})
        filas = capturar.call_args.args[0]
        assert n == 3 and len(filas) == 3
        for f in filas:
            assert set(f) == COLUMNAS

        f1, f2, f3 = filas
        # elegidos: moment_index de entrega = posición en `selected`
        assert (f1["selected"], f1["delivery_index"], f1["discard_reason"]) == (True, 2, None)
        assert (f3["selected"], f3["delivery_index"]) == (True, 1)
        assert (f2["selected"], f2["delivery_index"], f2["discard_reason"]) == (False, None, "bajo el umbral")
        assert f2["flags"]["late_hook"] is True and f2["flags"]["hook_not_found"] is False
        assert set(f2["flags"]) == set(rc.FLAG_FIELDS)

        # Juez y Jev
        assert (f1["judge_hook"], f1["judge_retention"], f1["judge_shareability"]) == (8, 7, 6)
        assert f1["judge_reasoning"] == "buen remate"
        assert (f1["jev_rank_score"], f1["jev_hook"], f1["jev_retention"], f1["jev_shareability"]) == (18.75, 3.0, 2.5, 2.0)
        assert (f1["jev_confidence"], f1["jev_model"]) == (0.8, "jev-2026-09")

        # tiempos, texto y contexto
        assert (f1["proposed_start"], f1["proposed_end"]) == (10.0, 35.0)
        assert (f1["final_start"], f1["final_end"]) == (10.0, 35.0)
        assert f1["lines_text"] == "Te cuento lo que me pasó en el aeropuerto. Y ahí me di cuenta de todo."
        assert f1["clip_text"] == "texto whisper"
        assert f1["category"] == "podcast" and f1["formato"] is None
        assert f1["prompt_version"].endswith("whisper_full")
        assert f1["analysis_model"] and f1["judge_model"]
        assert f1["video_duration"] == 600.0 and f1["job_id"] == "job-1"

    def test_jev_ausente_queda_en_null(self, capturar):
        _registrar([_cand(1)], selected=[])
        (f,) = capturar.call_args.args[0]
        for col in ("jev_rank_score", "jev_hook", "jev_retention", "jev_shareability",
                    "jev_confidence", "jev_model"):
            assert f[col] is None, col

    def test_sin_clip_usa_el_tramo_propuesto_y_final_null(self, capturar):
        c = _cand(4, usable=False, judge_scores=None)
        _registrar([c], selected=[], prepared={4: None})
        (f,) = capturar.call_args.args[0]
        assert f["final_start"] is None and f["final_end"] is None and f["clip_text"] is None
        assert f["usable"] is False and f["judge_hook"] is None
        assert f["lines_text"] == "Pasemos a otro tema."  # tramo propuesto: 40–65 s

    def test_ranker_jev_en_el_contexto(self, capturar):
        with patch.dict(os.environ, {"RANKER": "jev"}):
            _registrar([_cand(1)], selected=[])
        assert capturar.call_args.args[0][0]["ranker"] == "jev"

    def test_rank_score_es_el_de_la_pasada_a(self):
        """Mismo número que `rank_and_prune_candidates` guarda en candidates_all."""
        from services.moment_selector import rank_and_prune_candidates

        transcript = {"segments": [{"start": 0.0, "end": 30.0, "text": "sin punto final"}]}
        m = {"start_time": 5.0, "end_time": 29.0, "scores": {"hook": 8, "retention": 7, "shareability": 6}}
        esperado = rank_and_prune_candidates({"viral_moments": [m]}, 5, transcript)["candidates_all"][0]["rank_score"]
        assert rc.rank_score_pasada_a(21.0, 29.0, transcript) == esperado == 18.0


class TestNoFatalYDryRun:
    def test_falla_de_supabase_no_rompe(self):
        sb = MagicMock()
        sb.table.return_value.insert.return_value.execute.side_effect = RuntimeError("Server disconnected")
        with patch.dict(os.environ, {"EVAL_DRY_RUN": ""}), \
             patch("services.supabase_client.get_supabase", return_value=sb):
            assert rc.guardar_filas([{"job_id": "x"}]) is False
            assert _registrar([_cand(1)], selected=[]) == 1  # no levanta
        sb.table.assert_called_with("candidate_evals")

    def test_error_armando_filas_no_rompe(self):
        with patch.object(rc, "construir_fila", side_effect=KeyError("boom")), \
             patch.object(rc, "guardar_filas") as guardar:
            assert _registrar([_cand(1)], selected=[]) == 0
        guardar.assert_not_called()

    def test_sin_supabase_no_rompe(self):
        with patch.dict(os.environ, {"EVAL_DRY_RUN": ""}), \
             patch("services.supabase_client.get_supabase", return_value=None):
            assert rc.guardar_filas([{"job_id": "x"}]) is False

    def test_inserta_todas_las_filas_en_un_insert(self):
        sb = MagicMock()
        with patch.dict(os.environ, {"EVAL_DRY_RUN": ""}), \
             patch("services.supabase_client.get_supabase", return_value=sb):
            _registrar([_cand(1), _cand(2)], selected=[])
        sb.table.assert_called_once_with("candidate_evals")
        filas = sb.table.return_value.insert.call_args.args[0]
        assert [f["candidate_index"] for f in filas] == [1, 2]

    def test_en_dry_run_no_escribe(self):
        from services import supabase_client as sbc

        sbc.reset_dry_run()
        with patch.dict(os.environ, {"EVAL_DRY_RUN": "1"}), \
             patch("services.supabase_client.get_supabase") as get_sb:
            _registrar([_cand(1), _cand(2)], selected=[])
        get_sb.assert_not_called()
        assert [f["candidate_index"] for f in rc.DRY_RUN_CANDIDATE_EVALS] == [1, 2]
        sbc.reset_dry_run()
        assert rc.DRY_RUN_CANDIDATE_EVALS == []
