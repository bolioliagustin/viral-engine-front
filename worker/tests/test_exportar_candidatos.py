"""
Exportador del Registro de candidatos (W30): candidate_evals + Referencias +
clip_feedback → JSONL. Datos sintéticos; la base solo se lee.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from eval import exportar_candidatos as ex
from eval.etiquetas import Etiqueta

REF_DOC = {
    "esquema": 1, "youtube_id": "VID1", "duracion_sec": 1000.0,
    "excluir": [{"inicio": 900.0, "fin": 960.0, "motivo": "publicidad"}],
    "momentos": [
        {"id": "R1", "inicio": 95.0, "fin": 160.0, "nucleo_inicio": 100.0, "nucleo_fin": 150.0,
         "calidad": "A", "validado_por": "agustin"},
        {"id": "R2", "inicio": 400.0, "fin": 460.0, "nucleo_inicio": 410.0, "nucleo_fin": 450.0,
         "calidad": "B"},  # borrador sin validar
    ],
}


def _fila(i, *, job="job-1", video="VID1", created="2026-09-28T10:00:00", **kw):
    base = {
        "id": f"id-{job}-{i}", "job_id": job, "video_id": video, "video_duration": 1000.0,
        "candidate_index": i, "delivery_index": None, "created_at": created,
        "proposed_start": 0.0, "proposed_end": 30.0, "final_start": None, "final_end": None,
        "judge_hook": 8, "judge_retention": 7, "judge_shareability": 6,
        "jev_rank_score": None, "rank_score": 20.0, "w2_score": 21.0,
        "flags": {"late_hook": False}, "selected": False, "usable": True,
    }
    base.update(kw)
    return base


def _etiqueta(job, idx, posteable, motivo=None):
    return Etiqueta(content_result_id=f"cr-{job}-{idx}", job_id=job, moment_index=idx,
                    posteable=posteable, motivo=motivo, comentario=None,
                    created_at="2026-09-28", user_id="u")


def _refs(vid):
    return REF_DOC if vid == "VID1" else None


class TestArmarDataset:
    def test_etiquetas_por_referencia_y_posteable(self):
        filas = [
            # toca R1 validada con el tramo FINAL (el propuesto no la toca)
            _fila(1, proposed_start=300.0, proposed_end=340.0, final_start=98.0, final_end=152.0,
                  selected=True, delivery_index=1, jev_rank_score=18.5),
            # toca solo el borrador R2
            _fila(2, proposed_start=405.0, proposed_end=455.0),
            # cae en la zona excluida, elegido y etiquetado como no posteable
            _fila(3, proposed_start=905.0, proposed_end=950.0, selected=True, delivery_index=2),
            # video sin archivo de Referencias
            _fila(1, job="job-2", video="VID2"),
        ]
        et = {("job-1", 1): _etiqueta("job-1", 1, True), ("job-1", 2): _etiqueta("job-1", 2, False, "momento_flojo")}
        out = ex.armar_dataset(filas, et, cargar_ref=_refs)
        assert len(out) == 4 and all(r["esquema"] == ex.ESQUEMA for r in out)
        r1, r2, r3, r4 = out

        assert (r1["toca_referencia"], r1["referencia_ids"], r1["referencia_calidad"]) == (True, ["R1"], "A")
        assert (r1["posteable"], r1["jev_rank_score"]) == (True, 18.5)
        assert r1["duracion"] == 54.0 and r1["pos_rel"] == 0.098
        assert r1["juez_suma"] == 21.0

        assert (r2["toca_referencia"], r2["toca_borrador"], r2["posteable"]) == (False, True, None)
        assert r3["en_exclusion"] is True and (r3["posteable"], r3["motivo"]) == (False, "momento_flojo")
        assert (r4["hay_referencias"], r4["toca_referencia"], r4["en_exclusion"]) == (False, None, None)

    def test_rasgos_de_ml_factibilidad(self):
        (r,) = ex.armar_dataset([_fila(1)], {}, cargar_ref=_refs)
        for k in ("video", "toca_referencia", "en_exclusion", "juez_suma", "w2_score",
                  "rank_score", "pos_rel", "duracion"):
            assert k in r, k

    def test_juez_ausente_da_suma_null(self):
        (r,) = ex.armar_dataset([_fila(1, judge_hook=None)], {}, cargar_ref=_refs)
        assert r["juez_suma"] is None

    def test_reproceso_se_queda_con_la_fila_mas_reciente(self):
        viejo = _fila(1, created="2026-09-28T10:00:00", w2_score=5.0)
        nuevo = _fila(1, created="2026-09-28T11:00:00", w2_score=9.0)
        otro_job = _fila(1, job="job-2", created="2026-09-28T09:00:00")
        out = ex.armar_dataset([nuevo, viejo, otro_job], {}, cargar_ref=_refs)
        assert [(r["job_id"], r["w2_score"]) for r in out] == [("job-2", 21.0), ("job-1", 9.0)]

    def test_jsonl_ida_y_vuelta(self, tmp_path):
        out = ex.armar_dataset([_fila(1), _fila(2)], {}, cargar_ref=_refs)
        ruta = ex.escribir_jsonl(out, tmp_path / "d" / "c.jsonl")
        assert ex.leer_jsonl(ruta) == out


def _fake_sb(paginas):
    """Supabase falso: `candidate_evals` devuelve `paginas` en orden; cualquier
    escritura falla el test."""
    sb = MagicMock()
    consultas = iter(paginas)

    def _table(name):
        t = MagicMock()
        for op in ("insert", "update", "upsert", "delete"):
            getattr(t, op).side_effect = AssertionError(f"escritura en {name}")
        q = t.select.return_value
        q.eq.return_value = q
        q.order.return_value = q
        q.range.return_value.execute.side_effect = lambda: MagicMock(data=next(consultas))
        return t

    sb.table.side_effect = _table
    return sb


class TestLecturaSoloLectura:
    def test_pagina_hasta_la_ultima(self):
        with patch.object(ex, "PAGINA", 2):
            sb = _fake_sb([[_fila(1), _fila(2)], [_fila(3)]])
            assert [f["candidate_index"] for f in ex.leer_candidatos(sb)] == [1, 2, 3]

    def test_main_genera_el_jsonl(self, tmp_path):
        sb = _fake_sb([[_fila(1, selected=True, delivery_index=1), _fila(2)]])
        salida = tmp_path / "out.jsonl"
        with patch("services.supabase_client.get_supabase", return_value=sb), \
             patch("eval.etiquetas.fetch_etiquetas", return_value=({}, {("job-1", 1): _etiqueta("job-1", 1, True)})), \
             patch.object(ex, "cargar_referencias", _refs):
            assert ex.main(["--salida", str(salida)]) == 0
        filas = ex.leer_jsonl(salida)
        assert [f["posteable"] for f in filas] == [True, None]

    def test_tabla_inexistente_da_jsonl_vacio(self, tmp_path):
        sb = MagicMock()
        sb.table.return_value.select.side_effect = RuntimeError("relation candidate_evals does not exist")
        salida = tmp_path / "vacio.jsonl"
        with patch("services.supabase_client.get_supabase", return_value=sb), \
             patch("eval.etiquetas.fetch_etiquetas", return_value=({}, {})):
            assert ex.main(["--salida", str(salida)]) == 0
        assert ex.leer_jsonl(salida) == []
