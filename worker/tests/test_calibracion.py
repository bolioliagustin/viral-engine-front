"""
W12 (docs/PLAN_CALIDAD.md §2/§5): worker/eval/calibracion.py compara
CUALQUIER rankeador contra las etiquetas de clip_feedback. `calibrar()` no
hace I/O — se testea con datos sintéticos; la carga real
(`load_clips_etiquetados`) se prueba aparte, mockeando Supabase.
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "eval"))

from calibracion import (  # noqa: E402
    _extract_youtube_id,
    _full_text,
    calibrar,
    load_clips_etiquetados,
    render_text,
)


def _clip(job_id, mi, score, posteable, motivo=None, youtube_id="abc123", start=100, end=130):
    return {
        "job_id": job_id, "moment_index": mi, "content_result_id": f"{job_id}-{mi}",
        "youtube_id": youtube_id, "hook": f"hook {mi}", "viral_overlay": "OVERLAY",
        "start_time": start, "end_time": end,
        "score_judge": {"hook": score / 3, "retention": score / 3, "shareability": score / 3},
        "score_judge_sum": score, "posteable": posteable, "motivo": motivo,
    }


class TestCalibrar:
    def test_gap_y_promedios(self):
        clips = [
            _clip("j1", 1, 27, True), _clip("j1", 2, 24, True),
            _clip("j1", 3, 9, False), _clip("j1", 4, 6, False),
        ]
        r = calibrar(clips, con_texto=False)
        assert r["avg_posteable"] == 25.5
        assert r["avg_no_posteable"] == 7.5
        assert r["gap"] == 18.0

    def test_caso_real_el_juez_no_discrimina(self):
        """Reproduce el hallazgo que motiva W12: gap chico, correlación baja."""
        clips = [
            _clip("j1", 1, 17, True), _clip("j1", 2, 16, True), _clip("j1", 3, 15, True),
            _clip("j1", 4, 17, False), _clip("j1", 5, 16, False), _clip("j1", 6, 15, False),
        ]
        r = calibrar(clips, con_texto=False)
        assert abs(r["gap"]) < 1.0
        assert r["correlacion_punto_biserial"] is None or abs(r["correlacion_punto_biserial"]) < 0.3

    def test_precision_at_k_rankeador_perfecto(self):
        clips = [
            _clip("j1", 1, 30, True), _clip("j1", 2, 27, True), _clip("j1", 3, 24, True),
            _clip("j1", 4, 9, False), _clip("j1", 5, 6, False),
        ]
        r = calibrar(clips, ks=(3,), con_texto=False)
        assert r["precision_at_k"]["3"] == 1.0

    def test_falsos_negativos_y_positivos(self):
        clips = [
            _clip("j1", 1, 20, True),   # buen puntaje, posteable: no es un error
            _clip("j1", 2, 5, True, youtube_id=None),    # posteable pero score BAJO -> falso negativo
            _clip("j1", 3, 25, False, motivo="copy_malo", youtube_id=None),  # no posteable pero score ALTO -> falso positivo
        ]
        r = calibrar(clips, peores_n=5, con_texto=False)
        fn_indices = {c["moment_index"] for c in r["peores_falsos_negativos"]}
        fp_indices = {c["moment_index"] for c in r["peores_falsos_positivos"]}
        assert 2 in fn_indices
        assert 3 in fp_indices
        assert r["peores_falsos_positivos"][0]["motivo"] == "copy_malo"

    def test_sin_clips_no_rompe(self):
        r = calibrar([], con_texto=False)
        assert r["n_clips_con_etiqueta"] == 0
        assert r["posteable_rate"] is None
        assert r["gap"] is None
        assert r["correlacion_punto_biserial"] is None
        assert r["peores_falsos_negativos"] == []
        assert r["peores_falsos_positivos"] == []

    def test_clips_sin_score_se_cuentan_pero_no_entran_al_calculo(self):
        clips = [
            _clip("j1", 1, 20, True),
            {**_clip("j1", 2, 0, False), "score_judge_sum": None},
        ]
        r = calibrar(clips, con_texto=False)
        assert r["n_clips_con_etiqueta"] == 2
        assert r["n_con_score"] == 1
        assert r["n_sin_score"] == 1

    def test_score_field_configurable(self):
        """El mismo cálculo aplica a cualquier rankeador (Jev incluido) con
        solo cambiar qué campo se lee — no está atado al Juez."""
        clips = [
            {**_clip("j1", 1, 20, True), "jev_score": 8.0},
            {**_clip("j1", 2, 5, False), "jev_score": 2.0},
        ]
        r = calibrar(clips, score_field="jev_score", con_texto=False)
        assert r["score_field"] == "jev_score"
        assert r["avg_posteable"] == 8.0
        assert r["avg_no_posteable"] == 2.0

    def test_render_text_no_rompe_sin_clips(self):
        r = calibrar([], con_texto=False)
        text = render_text(r)
        assert "Calibración" in text

    def test_render_text_incluye_los_numeros_clave(self):
        clips = [_clip("j1", 1, 27, True), _clip("j1", 2, 9, False)]
        r = calibrar(clips, con_texto=False)
        text = render_text(r)
        assert "posteable_rate" in text
        assert "correlación punto-biserial" in text
        assert "Falsos negativos" in text and "Falsos positivos" in text


class TestExtractYoutubeId:
    def test_formatos_comunes(self):
        assert _extract_youtube_id("https://www.youtube.com/watch?v=XxoVRjTySsM") == "XxoVRjTySsM"
        assert _extract_youtube_id("https://youtu.be/XxoVRjTySsM") == "XxoVRjTySsM"
        assert _extract_youtube_id("https://www.youtube.com/shorts/XxoVRjTySsM") == "XxoVRjTySsM"

    def test_url_invalida_o_vacia(self):
        assert _extract_youtube_id("") is None
        assert _extract_youtube_id(None) is None
        assert _extract_youtube_id("no es una url") is None


class TestFullText:
    def test_reconstruye_desde_lineas_solapadas(self):
        clip = {"youtube_id": "v1", "start_time": 100, "end_time": 130, "hook": "fallback"}
        lines_by_video = {"v1": [
            {"start": 95, "end": 105, "text": "Primera oracion."},
            {"start": 106, "end": 125, "text": "Segunda oracion completa."},
            {"start": 200, "end": 210, "text": "No debería aparecer."},
        ]}
        text = _full_text(clip, lines_by_video)
        assert text == "Primera oracion. Segunda oracion completa."

    def test_sin_lineas_cae_al_hook(self):
        clip = {"youtube_id": "v1", "start_time": 100, "end_time": 130, "hook": "el hook de respaldo"}
        assert _full_text(clip, {}) == "el hook de respaldo"


class TestLoadClipsEtiquetados:
    def test_sin_supabase_devuelve_vacio(self):
        with patch("services.supabase_client.get_supabase", return_value=None):
            assert load_clips_etiquetados(["job-1"]) == []

    def test_sin_job_ids_devuelve_vacio(self):
        assert load_clips_etiquetados([]) == []

    def test_arma_clips_desde_content_results_y_etiquetas(self):
        content_results_rows = [
            {"id": "cr-1", "job_id": "job-1", "moment_index": 1, "hook": "h1",
             "viral_overlay": "O1", "start_time": 10, "end_time": 40,
             "score_judge": {"hook": 8, "retention": 8, "shareability": 8}},
        ]
        jobs_rows = [{"id": "job-1", "video_url": "https://www.youtube.com/watch?v=abc12345678"}]

        sb = MagicMock()

        def _table(name):
            t = MagicMock()
            if name == "content_results":
                t.select.return_value.in_.return_value.execute.return_value.data = content_results_rows
            elif name == "jobs":
                t.select.return_value.in_.return_value.execute.return_value.data = jobs_rows
            return t

        sb.table.side_effect = _table

        fake_etiqueta = MagicMock(posteable=True, motivo=None)
        with patch("services.supabase_client.get_supabase", return_value=sb), \
             patch("etiquetas.fetch_etiquetas", return_value=({}, {("job-1", 1): fake_etiqueta})):
            clips = load_clips_etiquetados(["job-1"])

        assert len(clips) == 1
        assert clips[0]["posteable"] is True
        assert clips[0]["score_judge_sum"] == 24.0
        assert clips[0]["youtube_id"] == "abc12345678"

    def test_content_result_sin_etiqueta_no_entra(self):
        content_results_rows = [
            {"id": "cr-1", "job_id": "job-1", "moment_index": 1, "hook": "h1",
             "viral_overlay": None, "start_time": 10, "end_time": 40, "score_judge": None},
        ]
        sb = MagicMock()

        def _table(name):
            t = MagicMock()
            if name == "content_results":
                t.select.return_value.in_.return_value.execute.return_value.data = content_results_rows
            elif name == "jobs":
                t.select.return_value.in_.return_value.execute.return_value.data = []
            return t

        sb.table.side_effect = _table
        with patch("services.supabase_client.get_supabase", return_value=sb), \
             patch("etiquetas.fetch_etiquetas", return_value=({}, {})):
            clips = load_clips_etiquetados(["job-1"])
        assert clips == []
