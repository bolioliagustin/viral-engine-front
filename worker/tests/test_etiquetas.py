"""
W12 (docs/PLAN_CALIDAD.md §2/§5): lector de `clip_feedback` — la fuente de
verdad de calidad ("posteable"). Sin credenciales, sin tabla, o sin filas,
degrada a vacío sin romper (mismo patrón que services/transcript_cache.py).
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "eval"))

from etiquetas import fetch_etiquetas, etiqueta_para  # noqa: E402


def _fake_supabase(feedback_rows, content_results_rows):
    sb = MagicMock()

    def _table(name):
        t = MagicMock()
        if name == "clip_feedback":
            t.select.return_value.execute.return_value.data = feedback_rows
        elif name == "content_results":
            t.select.return_value.in_.return_value.execute.return_value.data = content_results_rows
        return t

    sb.table.side_effect = _table
    return sb


class TestFetchEtiquetas:
    def test_sin_supabase_devuelve_vacio(self):
        with patch("etiquetas._get_supabase", return_value=None):
            por_cr, por_momento = fetch_etiquetas()
        assert por_cr == {} and por_momento == {}

    def test_sin_filas_devuelve_vacio(self):
        sb = _fake_supabase([], [])
        with patch("etiquetas._get_supabase", return_value=sb):
            por_cr, por_momento = fetch_etiquetas()
        assert por_cr == {} and por_momento == {}

    def test_excepcion_de_supabase_no_rompe(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("conexión caída")
        with patch("etiquetas._get_supabase", return_value=sb):
            por_cr, por_momento = fetch_etiquetas()
        assert por_cr == {} and por_momento == {}

    def test_lee_una_etiqueta_simple(self):
        feedback = [{
            "content_result_id": "cr-1", "user_id": "u-1", "posteable": True,
            "motivo": None, "comentario": None, "created_at": "2026-09-21T10:00:00+00:00",
        }]
        crs = [{"id": "cr-1", "job_id": "job-1", "moment_index": 3}]
        sb = _fake_supabase(feedback, crs)
        with patch("etiquetas._get_supabase", return_value=sb):
            por_cr, por_momento = fetch_etiquetas()
        assert por_cr["cr-1"].posteable is True
        assert por_momento[("job-1", 3)].posteable is True

    def test_re_etiquetado_se_queda_con_el_mas_reciente(self):
        feedback = [
            {"content_result_id": "cr-1", "user_id": "u-1", "posteable": True,
             "motivo": None, "comentario": None, "created_at": "2026-09-21T10:00:00+00:00"},
            {"content_result_id": "cr-1", "user_id": "u-1", "posteable": False,
             "motivo": "termina_mal", "comentario": None, "created_at": "2026-09-21T11:00:00+00:00"},
        ]
        crs = [{"id": "cr-1", "job_id": "job-1", "moment_index": 7}]
        sb = _fake_supabase(feedback, crs)
        with patch("etiquetas._get_supabase", return_value=sb):
            por_cr, por_momento = fetch_etiquetas()
        assert por_cr["cr-1"].posteable is False
        assert por_cr["cr-1"].motivo == "termina_mal"
        assert por_momento[("job-1", 7)].posteable is False

    def test_content_result_sin_metadata_no_entra_a_por_momento(self):
        """Si content_results no devuelve la fila (borrada, o falla el query),
        la etiqueta sigue en por_cr pero no en por_momento (sin job_id/moment_index)."""
        feedback = [{
            "content_result_id": "cr-huerfano", "user_id": "u-1", "posteable": True,
            "motivo": None, "comentario": None, "created_at": "2026-09-21T10:00:00+00:00",
        }]
        sb = _fake_supabase(feedback, [])
        with patch("etiquetas._get_supabase", return_value=sb):
            por_cr, por_momento = fetch_etiquetas()
        assert "cr-huerfano" in por_cr
        assert por_momento == {}

    def test_etiqueta_para_sin_match_devuelve_none(self):
        assert etiqueta_para({}, "job-1", 1) is None
        assert etiqueta_para({}, None, None) is None
