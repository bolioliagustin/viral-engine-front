"""
Tier e2e del golden set: candidates_all en analysis_cache, métricas por clip,
agregados/umbrales y compare_runs.
"""
import json
import sys
from pathlib import Path

import pytest

WORKER_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(WORKER_DIR))
sys.path.insert(0, str(WORKER_DIR / "eval"))


def _candidate(i: int, start: float, hook_score: int = 8) -> dict:
    return {
        "start_time": start,
        "end_time": start + 30,
        "hook": f"Hook {i}",
        "emotional_trigger": "curiosidad",
        "pillar_type": "authority",
        "viral_overlay": f"OVERLAY {i}",
        "scores": {"hook": hook_score, "retention": 8, "shareability": 8},
        "verification": {"first_phrase_in_audio": "hola", "last_phrase_in_audio": "chau"},
        "content_pieces": {},
    }


class TestCandidatesAll:
    def test_rank_and_prune_ya_no_trunca(self):
        # W9-B: ya no poda por auto-score — TODOS los candidatos se evalúan
        # de verdad en main.py, el juez decide qué se entrega por umbral.
        from services.moment_selector import rank_and_prune_candidates

        result = {
            "video_title": "t",
            "summary": "s",
            "viral_moments": [
                _candidate(1, 0, 9), _candidate(2, 100, 7), _candidate(3, 200, 8),
                _candidate(4, 300, 6), _candidate(5, 400, 5),
            ],
        }
        out = rank_and_prune_candidates(result, target=1, transcript=None)

        assert len(out["viral_moments"]) == 5
        assert [m["hook"] for m in out["viral_moments"]] == [
            "Hook 1", "Hook 2", "Hook 3", "Hook 4", "Hook 5",
        ]
        assert len(out["candidates_all"]) == 5
        # Copia independiente: modificar el momento final no toca el candidato guardado
        out["viral_moments"][0]["hook"] = "cambiado"
        assert out["candidates_all"][0]["hook"] == "Hook 1"

    def test_sin_poda_tambien_guarda_candidates_all(self):
        from services.moment_selector import rank_and_prune_candidates

        result = {"viral_moments": [_candidate(1, 0)]}
        out = rank_and_prune_candidates(result, target=3)
        assert len(out["viral_moments"]) == 1
        assert len(out["candidates_all"]) == 1

    def test_analysis_result_ignora_candidates_all(self):
        """Lo que save_analysis guarda tiene que poder volver por AnalysisResult(**cached)."""
        from models.schemas import AnalysisResult
        from services.moment_selector import rank_and_prune_candidates

        n = 8
        result = {
            "video_title": "t",
            "summary": "s",
            "viral_moments": [_candidate(i, i * 100) for i in range(1, n + 1)],
        }
        out = rank_and_prune_candidates(result, target=1)
        cached = json.loads(json.dumps(out, default=str))  # round-trip como analysis_cache
        parsed = AnalysisResult(**cached)
        assert len(parsed.viral_moments) == n
        assert not hasattr(parsed, "candidates_all") or parsed.model_extra in (None, {})
        assert "candidates_all" in cached and len(cached["candidates_all"]) == n


def _row(moment_index: int, **over) -> dict:
    row = {
        "job_id": "job-1",
        "type": "twitter_thread",
        "clip_url": f"dryrun://job-1/{moment_index}.mp4",
        "start_time": 100,
        "end_time": 134,
        "hook": "hook",
        "moment_index": moment_index,
        "viral_overlay": "OVERLAY",
        "whisper_words": {
            "words": [{"word": w} for w in "Hola a todos hoy vamos a hablar de un tema que importa mucho".split()],
            "duration_sec": 29.0,
            "snap_trim_start": 4.0,
        },
        "score_llm": {"hook": 8, "retention": 8, "shareability": 9},
        "score_judge": {"hook": 7, "retention": 7, "shareability": 8, "reasoning": "ok"},
        "verification_failed": False,
        "sub_coverage": 0.95,
        "words_per_sec": 2.4,
        "clip_quality_issues": [],
        "clip_generation_error": None,
    }
    row.update(over)
    return row


VIDEO = {"id": "vid_01", "youtube_id": "abc"}


class TestClipRecord:
    def test_registro_por_clip(self):
        from eval_metrics import build_e2e_clip_record

        rows = [_row(1), _row(1, type="linkedin_post"), _row(1, type="tiktok_caption")]
        c = build_e2e_clip_record(VIDEO, rows)

        assert c["video_id"] == "vid_01" and c["moment_index"] == 1
        assert c["duration_chosen_sec"] == 34.0
        assert c["duration_final_sec"] == 29.0
        assert c["snap_trim_start"] == 4.0
        assert c["first_words"] == ["Hola", "a", "todos", "hoy", "vamos", "a", "hablar", "de", "un", "tema"]
        assert c["last_words"] == ["a", "hablar", "de", "un", "tema", "que", "importa", "mucho"]
        assert c["starts_capitalized"] is True
        assert c["density_out_of_range"] is False
        assert c["judge_all_ge7"] is True
        assert c["clip_rendered"] is True
        assert c["score_judge"]["reasoning"] == "ok"
        assert c["copy_types"] == ["linkedin_post", "tiktok_caption", "twitter_thread"]

    def test_clip_no_renderizado_y_sin_whisper(self):
        from eval_metrics import build_e2e_clip_record

        c = build_e2e_clip_record(VIDEO, [_row(
            2, clip_url="https://www.youtube.com/watch?v=abc&t=100s", whisper_words=None,
            score_judge=None, words_per_sec=None, clip_quality_issues=["clip_not_rendered"],
            clip_generation_error="403",
        )])
        assert c["clip_rendered"] is False
        assert c["first_words"] == [] and c["starts_capitalized"] is None
        assert c["duration_final_sec"] is None
        assert c["density_out_of_range"] is None
        assert c["judge_all_ge7"] is None

    def test_minuscula_y_densidad_fuera_de_rango(self):
        from eval_metrics import build_e2e_clip_record

        c = build_e2e_clip_record(VIDEO, [_row(
            3, words_per_sec=7.9,
            whisper_words={"words": [{"word": "¿y"}, {"word": "entonces"}], "duration_sec": 9.0,
                           "snap_trim_start": 24.0},
        )])
        assert c["starts_capitalized"] is False  # primer alfa: "y"
        assert c["density_out_of_range"] is True

    def test_starts_capitalized_whisper_siempre_presente(self):
        """El campo viejo se conserva siempre, con o sin Líneas."""
        from eval_metrics import build_e2e_clip_record

        c = build_e2e_clip_record(VIDEO, [_row(4)])
        assert c["starts_capitalized_whisper"] is True
        assert c["starts_capitalized"] == c["starts_capitalized_whisper"]  # sin line_aligned, iguales


class TestCapitalizacionPorLinea:
    """
    INT-4/INT-5 (docs/PLAN_CALIDAD.md §9): con `line_aligned` en
    clip_quality_issues y Líneas del transcript completo disponibles,
    `starts_capitalized` se calcula sobre la Línea cuyo inicio coincide con
    el del clip (±0,6 s — `content_results.start_time` se persiste como
    `integer`, medio segundo de redondeo antes de cualquier imprecisión
    real del corte), no sobre la re-transcripción Whisper aislada del clip.
    """

    LINES = [
        {"id": 0, "start": 0.0, "end": 5.0, "text": "minúscula a propósito para el test."},
        {"id": 1, "start": 100.05, "end": 134.2, "text": "Mayúscula real de la Línea completa."},
        {"id": 2, "start": 200.0, "end": 210.0, "text": "otra línea en minúscula."},
    ]

    def test_usa_la_linea_cuando_hay_line_aligned_y_match(self):
        from eval_metrics import build_e2e_clip_record

        # whisper_words (re-transcripción aislada) dice minúscula, pero la
        # Línea real (con el contexto de la oración anterior) arranca en
        # mayúscula: el valor nuevo tiene que divergir del viejo.
        c = build_e2e_clip_record(
            VIDEO,
            [_row(
                1, start_time=100, clip_quality_issues=["line_aligned"],
                whisper_words={"words": [{"word": "luego"}, {"word": "tenemos"}], "duration_sec": 34.0},
            )],
            lines=self.LINES,
        )
        assert c["starts_capitalized_whisper"] is False  # "luego" en minúscula
        assert c["starts_capitalized"] is True  # la Línea (id=1) arranca en mayúscula

    def test_linea_en_minuscula_pisa_un_whisper_en_mayuscula(self):
        from eval_metrics import build_e2e_clip_record

        c = build_e2e_clip_record(
            VIDEO,
            [_row(3, start_time=200, clip_quality_issues=["line_aligned"])],  # whisper dice "Hola..." (mayúscula)
            lines=self.LINES,
        )
        assert c["starts_capitalized_whisper"] is True
        assert c["starts_capitalized"] is False  # la Línea real (id=2) está en minúscula

    def test_sin_line_aligned_no_usa_la_linea_aunque_haya_match(self):
        from eval_metrics import build_e2e_clip_record

        c = build_e2e_clip_record(
            VIDEO,
            [_row(3, start_time=200, clip_quality_issues=[])],  # sin el flag
            lines=self.LINES,
        )
        assert c["starts_capitalized"] == c["starts_capitalized_whisper"]

    def test_sin_lineas_degrada_a_la_metrica_vieja(self):
        from eval_metrics import build_e2e_clip_record

        c = build_e2e_clip_record(
            VIDEO,
            [_row(1, start_time=100, clip_quality_issues=["line_aligned"])],
            lines=None,
        )
        assert c["starts_capitalized"] == c["starts_capitalized_whisper"]

    def test_sin_match_dentro_de_tolerancia_degrada(self):
        from eval_metrics import build_e2e_clip_record

        c = build_e2e_clip_record(
            VIDEO,
            [_row(1, start_time=100.8, clip_quality_issues=["line_aligned"])],  # 0.75s de la Línea id=1, fuera de ±0.6
            lines=self.LINES,
        )
        assert c["starts_capitalized"] == c["starts_capitalized_whisper"]

    def test_match_justo_en_el_borde_de_tolerancia(self):
        from eval_metrics import find_line_at_start

        line = find_line_at_start(self.LINES, 100.64)  # 0.59s de la Línea id=1 (100.05), dentro de ±0.6
        assert line is not None and line["id"] == 1

    def test_fuera_del_borde_de_tolerancia_no_matchea(self):
        from eval_metrics import find_line_at_start

        line = find_line_at_start(self.LINES, 100.7)  # 0.65s, apenas fuera de ±0.6
        assert line is None

    def test_dentro_de_la_vieja_tolerancia_0_3_sigue_matcheando(self):
        """La tolerancia subió, no bajó: lo que matcheaba con ±0,3s sigue matcheando."""
        from eval_metrics import find_line_at_start

        line = find_line_at_start(self.LINES, 100.3)  # 0.25s de la Línea id=1
        assert line is not None and line["id"] == 1

    def test_find_line_at_start_sin_lineas_o_sin_start_time(self):
        from eval_metrics import find_line_at_start

        assert find_line_at_start(None, 100) is None
        assert find_line_at_start(self.LINES, None) is None
        assert find_line_at_start([], 100) is None

    def test_line_starts_capitalized_ignora_puntuacion_inicial(self):
        from eval_metrics import line_starts_capitalized

        assert line_starts_capitalized({"text": "¿Empieza con signo de apertura?"}) is True
        assert line_starts_capitalized({"text": "123 empieza con número"}) is False
        assert line_starts_capitalized({"text": ""}) is None
        assert line_starts_capitalized(None) is None


class TestAgregadosNuevos:
    """INT-4: judge_avg_top5, clips_per_hour, delivered_per_video."""

    @staticmethod
    def _clip(mi, judge_sum, video_duration_sec=None, **over):
        per = judge_sum / 3
        c = {
            "score_judge": {"hook": per, "retention": per, "shareability": per},
            "moment_index": mi,
            "clip_rendered": True,
            "starts_capitalized": True,
            "density_out_of_range": False,
        }
        c.update(over)
        return c

    def test_judge_avg_top5_toma_los_5_mejores_por_video(self):
        from eval_metrics import aggregate_e2e_results

        clips_a = [self._clip(i, s) for i, s in enumerate([27, 24, 21, 18, 15, 9, 6], start=1)]
        clips_b = [self._clip(i, s) for i, s in enumerate([30, 12], start=1)]
        results = [
            {"id": "va", "ok": True, "clips": clips_a, "cost_usd": 0.1, "elapsed_sec": 10},
            {"id": "vb", "ok": True, "clips": clips_b, "cost_usd": 0.1, "elapsed_sec": 10},
        ]
        agg = aggregate_e2e_results(results)
        # top5 de A: 27,24,21,18,15 (sum=105) + los 2 de B: 30,12 (sum=42) -> 7 clips, sum=147
        # promedio de juez (0-10) = (147/3)/7 = 7.0
        assert agg["judge_avg_top5"] == 7.0

    def test_clips_per_hour_solo_cuenta_videos_con_duracion_conocida(self):
        from eval_metrics import aggregate_e2e_results

        results = [
            {"id": "va", "ok": True, "clips": [self._clip(1, 21)] * 6, "video_duration_sec": 3600, "cost_usd": 0, "elapsed_sec": 0},
            {"id": "vb", "ok": True, "clips": [self._clip(1, 21)] * 3, "cost_usd": 0, "elapsed_sec": 0},  # sin duración
        ]
        agg = aggregate_e2e_results(results)
        assert agg["clips_per_hour"] == 6.0  # solo va: 6 clips / 1 hora

    def test_clips_per_hour_none_sin_ningun_video_con_duracion(self):
        from eval_metrics import aggregate_e2e_results

        results = [{"id": "va", "ok": True, "clips": [self._clip(1, 21)], "cost_usd": 0, "elapsed_sec": 0}]
        agg = aggregate_e2e_results(results)
        assert agg["clips_per_hour"] is None

    def test_delivered_per_video_lista_todos_incluidos_los_no_ok(self):
        from eval_metrics import aggregate_e2e_results

        results = [
            {"id": "va", "ok": True, "clips": [self._clip(1, 21), self._clip(2, 18)], "cost_usd": 0, "elapsed_sec": 0},
            {"id": "vb", "ok": False, "clips": [], "cost_usd": 0, "elapsed_sec": 0},
        ]
        agg = aggregate_e2e_results(results)
        assert agg["delivered_per_video"] == [
            {"video_id": "va", "clips_count": 2},
            {"video_id": "vb", "clips_count": 0},
        ]

    def test_capitalized_start_whisper_rate_presente(self):
        from eval_metrics import aggregate_e2e_results

        results = [{
            "id": "va", "ok": True, "cost_usd": 0, "elapsed_sec": 0,
            "clips": [
                self._clip(1, 21, starts_capitalized=True, starts_capitalized_whisper=False),
                self._clip(2, 18, starts_capitalized=False, starts_capitalized_whisper=True),
            ],
        }]
        agg = aggregate_e2e_results(results)
        assert agg["capitalized_start_rate"] == 0.5
        assert agg["capitalized_start_whisper_rate"] == 0.5


def _video_result(vid: str, clips: list[dict], cost=0.05, elapsed=120.0, ok=True) -> dict:
    return {"id": vid, "ok": ok, "clips": clips, "cost_usd": cost, "elapsed_sec": elapsed}


def _clip(vid, mi, judge, *, flags=(), cap=True, dens_out=False, vf=False, rendered=True,
          chosen=34.0, final=29.0):
    return {
        "video_id": vid, "moment_index": mi,
        "score_judge": {"hook": judge[0], "retention": judge[1], "shareability": judge[2], "reasoning": ""},
        "clip_quality_issues": list(flags), "starts_capitalized": cap,
        "density_out_of_range": dens_out, "verification_failed": vf, "clip_rendered": rendered,
        "duration_chosen_sec": chosen, "duration_final_sec": final,
        "first_words": ["Hola"] if cap else ["hola"],
    }


def _summary_a() -> dict:
    results = [
        _video_result("v1", [
            _clip("v1", 1, (7, 7, 8)),
            _clip("v1", 2, (4, 3, 5), flags=["late_hook", "whisper_mismatch_last"], cap=False, vf=True),
        ]),
        _video_result("v2", [
            _clip("v2", 1, (5, 5, 5), dens_out=True, vf=True),
            _clip("v2", 2, (6, 7, 7), rendered=False),
        ], cost=0.03, elapsed=60.0),
    ]
    from eval_metrics import aggregate_e2e_results
    return aggregate_e2e_results(results)


class TestAggregateE2E:
    def test_agregados(self):
        s = _summary_a()
        assert s["videos_ok"] == 2 and s["clips_total"] == 4 and s["clips_judged"] == 4
        assert s["judge_hook_avg"] == 5.5
        assert s["judge_retention_avg"] == 5.5
        assert s["judge_shareability_avg"] == 6.25
        assert s["judge_avg"] == pytest.approx((22 + 12 + 15 + 20) / 3 / 4, abs=1e-3)
        assert s["judge_all_ge7_rate"] == 0.25
        assert s["verification_failed_rate"] == 0.5
        assert s["late_hook_rate"] == 0.25
        assert s["whisper_mismatch_last_rate"] == 0.25
        assert s["capitalized_start_rate"] == 0.75
        assert s["density_out_of_range_rate"] == 0.25
        assert s["clips_rendered_rate"] == 0.75
        assert s["duration_chosen_avg"] == 34.0 and s["duration_final_avg"] == 29.0
        assert s["total_cost_usd"] == 0.08
        assert s["total_seconds"] == 180.0

    def test_sin_clips_no_divide_por_cero(self):
        from eval_metrics import aggregate_e2e_results
        s = aggregate_e2e_results([{"id": "v", "ok": False, "clips": [], "errors": ["timeout"]}])
        assert s["clips_total"] == 0 and s["judge_avg"] is None
        assert s["judge_all_ge7_rate"] is None and s["capitalized_start_rate"] is None

    def test_umbrales_e2e(self):
        from eval_metrics import check_e2e_thresholds
        s = _summary_a()
        thresholds = {
            "judge_avg_min": 7.0,
            "judge_all_ge7_rate_min": 0.6,
            "verification_failed_rate_max": 0.15,
            "density_out_of_range_rate_max": 0.0,
            "capitalized_start_rate_min": 0.9,
            "late_hook_rate_max": None,  # informativo
            "min_clips_total": 15,
        }
        failures = check_e2e_thresholds(s, thresholds)
        keys = [f.split("=")[0] for f in failures]
        assert keys == [
            "judge_avg", "judge_all_ge7_rate", "verification_failed_rate",
            "density_out_of_range_rate", "capitalized_start_rate", "clips_total",
        ]
        assert check_e2e_thresholds(s, {}) == []

    def test_tier_e2e_en_golden_set(self):
        from eval_metrics import filter_videos_for_tier, resolve_tier_config
        golden = json.loads((WORKER_DIR / "eval" / "golden_set.json").read_text(encoding="utf-8"))
        cfg = resolve_tier_config(golden, "e2e")
        assert cfg["thresholds"]["judge_avg_min"] == 7.0
        videos = filter_videos_for_tier(golden["videos"], "e2e", cfg.get("video_ids"))
        ids = [v["id"] for v in videos]
        assert len(ids) >= 3
        assert ids[-1] == "user_recommended_01"  # el largo va último


class TestCompareRuns:
    def _summary_b(self) -> dict:
        from eval_metrics import aggregate_e2e_results
        results = [
            _video_result("v1", [
                _clip("v1", 1, (8, 8, 8)),
                _clip("v1", 2, (7, 7, 7)),
            ], cost=0.06),
            _video_result("v2", [
                _clip("v2", 1, (7, 8, 7)),
                _clip("v2", 3, (6, 6, 6)),
            ], cost=0.04, elapsed=60.0),
        ]
        return aggregate_e2e_results(results)

    def test_delta_cero_contra_si_mismo(self):
        from compare_runs import compare_clips, compare_metrics
        a = _summary_a()
        rows = compare_metrics(a, a)
        assert rows and all(r["delta"] == 0 for r in rows if r["delta"] is not None)
        assert all(r["verdict"] in ("same", "unknown") for r in rows)
        clips = compare_clips(a, a)
        assert len(clips) == 4 and all(c["delta"] == 0 for c in clips)

    def test_direccion_de_mejora(self):
        from compare_runs import compare_clips, compare_metrics, render_text
        a, b = _summary_a(), self._summary_b()
        by = {r["metric"]: r for r in compare_metrics(a, b)}
        assert by["judge_avg"]["verdict"] == "better"
        assert by["verification_failed_rate"]["verdict"] == "better"   # 0.5 → 0
        assert by["total_cost_usd"]["verdict"] == "worse"              # 0.08 → 0.10
        assert by["duration_final_avg"]["verdict"] == "same"
        assert by["clips_total"]["verdict"] == "same"

        clips = {(c["video_id"], c["moment_index"]): c for c in compare_clips(a, b)}
        assert clips[("v1", 1)]["delta"] == pytest.approx(0.67, abs=0.01)
        assert clips[("v1", 1)]["verdict"] == "better"
        assert clips[("v2", 2)]["in_b"] is False
        assert clips[("v2", 3)]["in_a"] is False

        text = render_text(a, b, compare_metrics(a, b), compare_clips(a, b))
        assert "judge_avg" in text and "▲" in text and "▼" in text
        assert "solo en A" in text and "solo en B" in text

    def test_cli_json(self, tmp_path, capsys):
        from compare_runs import main
        a, b = _summary_a(), self._summary_b()
        pa, pb = tmp_path / "a.json", tmp_path / "b.json"
        pa.write_text(json.dumps(a), encoding="utf-8")
        pb.write_text(json.dumps(b), encoding="utf-8")
        assert main([str(pa), str(pb), "--json"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert {r["metric"] for r in out["metrics"]} >= {"judge_avg", "total_cost_usd"}
        assert len(out["clips"]) == 5

    def test_sin_etiquetas_en_ninguna_corrida_marca_sin_datos(self):
        """W12: posteable_rate y compañía distinguen 'sin datos' de 0/n.a."""
        from compare_runs import _fmt, _fmt_delta, compare_metrics
        a, b = _summary_a(), self._summary_b()  # ninguna de las dos tiene clips etiquetados
        by = {r["metric"]: r for r in compare_metrics(a, b)}
        assert by["posteable_rate"]["a"] is None and by["posteable_rate"]["b"] is None
        assert by["posteable_rate"]["delta"] is None
        assert _fmt("posteable_rate", None) == "sin datos"
        assert _fmt_delta("posteable_rate", None) == "sin datos"
        assert _fmt("judge_avg", None) == "n/a"  # una métrica NO ligada a etiquetas sigue en n/a

    def test_con_etiquetas_posteable_rate_se_compara_como_porcentaje(self):
        from eval_metrics import aggregate_e2e_results
        from compare_runs import compare_metrics, _fmt

        def _labeled(mi, judge_sum, posteable):
            per = judge_sum / 3
            return {
                "moment_index": mi, "score_judge": {"hook": per, "retention": per, "shareability": per},
                "posteable": posteable, "posteable_motivo": None if posteable else "momento_flojo",
                "clip_rendered": True, "starts_capitalized": True, "density_out_of_range": False,
            }

        a = aggregate_e2e_results([{
            "id": "v1", "ok": True, "cost_usd": 0, "elapsed_sec": 0,
            "clips": [_labeled(1, 15, True), _labeled(2, 15, False)],
        }])
        b = aggregate_e2e_results([{
            "id": "v1", "ok": True, "cost_usd": 0, "elapsed_sec": 0,
            "clips": [_labeled(1, 15, True), _labeled(2, 15, True), _labeled(3, 15, True)],
        }])
        by = {r["metric"]: r for r in compare_metrics(a, b)}
        assert by["posteable_rate"]["a"] == 0.5
        assert by["posteable_rate"]["b"] == 1.0
        assert by["posteable_rate"]["verdict"] == "better"
        assert _fmt("posteable_rate", 0.5) == "50%"

    def test_compare_motivos_menos_rechazos_es_mejora(self):
        from compare_runs import compare_motivos
        a = {"motivos_rechazo": {"termina_mal": 3, "copy_malo": 1}}
        b = {"motivos_rechazo": {"termina_mal": 1, "copy_malo": 1, "arranca_mal": 2}}
        rows = {r["motivo"]: r for r in compare_motivos(a, b)}
        assert rows["termina_mal"]["delta"] == -2 and rows["termina_mal"]["verdict"] == "better"
        assert rows["copy_malo"]["verdict"] == "same"
        assert rows["arranca_mal"]["a"] == 0 and rows["arranca_mal"]["verdict"] == "worse"

    def test_compare_motivos_sin_datos_en_ninguna_corrida(self):
        from compare_runs import compare_motivos
        assert compare_motivos({}, {}) == []

    def test_render_text_incluye_motivos_cuando_hay(self):
        from compare_runs import compare_motivos, render_text
        a, b = _summary_a(), self._summary_b()
        motivo_rows = compare_motivos(
            {"motivos_rechazo": {"termina_mal": 3}}, {"motivos_rechazo": {"termina_mal": 1}},
        )
        text = render_text(a, b, [], [], motivo_rows)
        assert "Motivos de rechazo" in text and "termina_mal" in text


class TestThresholdsBlocking:
    def test_e2e_no_bloquea_por_defecto_en_golden_set(self):
        from eval_metrics import resolve_tier_config
        golden = json.loads((WORKER_DIR / "eval" / "golden_set.json").read_text(encoding="utf-8"))
        assert resolve_tier_config(golden, "e2e")["thresholds_blocking"] is False
        assert resolve_tier_config(golden, "smoke")["thresholds_blocking"] is True

    def test_e2e_tiene_posteable_rate_min_en_golden_set(self):
        """W12: posteable_rate_min es el umbral que importa a partir de ahora."""
        from eval_metrics import resolve_tier_config
        golden = json.loads((WORKER_DIR / "eval" / "golden_set.json").read_text(encoding="utf-8"))
        assert resolve_tier_config(golden, "e2e")["thresholds"]["posteable_rate_min"] == 0.7


class _Etiqueta:
    """Doble liviano de eval.etiquetas.Etiqueta — solo los dos campos que usa
    build_e2e_clip_record, para no depender de I/O en estos tests."""

    def __init__(self, posteable, motivo=None):
        self.posteable = posteable
        self.motivo = motivo


class TestPosteableEnClipRecord:
    """W12 (docs/PLAN_CALIDAD.md §2/§5): posteable pasa a ser la métrica
    principal del tier e2e; el juez queda secundario."""

    def test_sin_etiqueta_queda_en_none(self):
        from eval_metrics import build_e2e_clip_record

        c = build_e2e_clip_record(VIDEO, [_row(1)])
        assert c["posteable"] is None
        assert c["posteable_motivo"] is None

    def test_con_etiqueta_positiva(self):
        from eval_metrics import build_e2e_clip_record

        c = build_e2e_clip_record(VIDEO, [_row(1)], etiqueta=_Etiqueta(True))
        assert c["posteable"] is True
        assert c["posteable_motivo"] is None

    def test_con_etiqueta_negativa_y_motivo(self):
        from eval_metrics import build_e2e_clip_record

        c = build_e2e_clip_record(VIDEO, [_row(1)], etiqueta=_Etiqueta(False, "termina_mal"))
        assert c["posteable"] is False
        assert c["posteable_motivo"] == "termina_mal"


def _clip_posteable(mi, judge_sum, posteable, motivo=None):
    per = judge_sum / 3
    return {
        "moment_index": mi,
        "score_judge": {"hook": per, "retention": per, "shareability": per},
        "posteable": posteable,
        "posteable_motivo": motivo,
        "clip_rendered": True,
        "starts_capitalized": True,
        "density_out_of_range": False,
    }


class TestAgregadosPosteable:
    """W12: posteable_rate, motivos_rechazo, judge_gap, judge_humano_corr,
    precision_at_k — con datos sintéticos, incluido el caso sin etiquetas."""

    def test_sin_ninguna_etiqueta_no_rompe(self):
        from eval_metrics import aggregate_e2e_results

        clips = [
            {"moment_index": i, "score_judge": {"hook": 5, "retention": 5, "shareability": 5},
             "posteable": None, "posteable_motivo": None, "clip_rendered": True,
             "starts_capitalized": True, "density_out_of_range": False}
            for i in range(1, 4)
        ]
        results = [{"id": "va", "ok": True, "clips": clips, "cost_usd": 0, "elapsed_sec": 0}]
        agg = aggregate_e2e_results(results)
        assert agg["posteable_labeled_n"] == 0
        assert agg["posteable_rate"] is None
        assert agg["motivos_rechazo"] == {}
        assert agg["judge_posteable_avg"] is None
        assert agg["judge_no_posteable_avg"] is None
        assert agg["judge_gap"] is None
        assert agg["judge_humano_corr"] is None
        assert agg["precision_at_3"] is None

    def test_posteable_rate_solo_sobre_los_etiquetados(self):
        from eval_metrics import aggregate_e2e_results

        clips = [
            _clip_posteable(1, 24, True),
            _clip_posteable(2, 21, False, "momento_flojo"),
            _clip_posteable(3, 18, None),  # sin etiqueta: no cuenta ni en el denominador
        ]
        results = [{"id": "va", "ok": True, "clips": clips, "cost_usd": 0, "elapsed_sec": 0}]
        agg = aggregate_e2e_results(results)
        assert agg["posteable_labeled_n"] == 2
        assert agg["posteable_rate"] == 0.5

    def test_motivos_rechazo_cuenta_por_motivo(self):
        from eval_metrics import aggregate_e2e_results

        clips = [
            _clip_posteable(1, 24, False, "termina_mal"),
            _clip_posteable(2, 21, False, "termina_mal"),
            _clip_posteable(3, 18, False, "copy_malo"),
            _clip_posteable(4, 15, False, None),  # sin motivo -> "sin_motivo"
        ]
        results = [{"id": "va", "ok": True, "clips": clips, "cost_usd": 0, "elapsed_sec": 0}]
        agg = aggregate_e2e_results(results)
        assert agg["motivos_rechazo"] == {"termina_mal": 2, "copy_malo": 1, "sin_motivo": 1}

    def test_judge_gap_positivo_cuando_el_juez_discrimina(self):
        from eval_metrics import aggregate_e2e_results

        clips = [
            _clip_posteable(1, 27, True), _clip_posteable(2, 24, True),
            _clip_posteable(3, 9, False), _clip_posteable(4, 6, False),
        ]
        results = [{"id": "va", "ok": True, "clips": clips, "cost_usd": 0, "elapsed_sec": 0}]
        agg = aggregate_e2e_results(results)
        assert agg["judge_posteable_avg"] == 8.5  # (9+8)/2
        assert agg["judge_no_posteable_avg"] == 2.5  # (3+2)/2
        assert agg["judge_gap"] == 6.0

    def test_judge_gap_chico_cuando_el_juez_no_discrimina(self):
        """El caso real que motiva W12: juez casi ciego al criterio humano."""
        from eval_metrics import aggregate_e2e_results

        clips = [
            _clip_posteable(1, 17, True), _clip_posteable(2, 16, True),
            _clip_posteable(3, 17, False), _clip_posteable(4, 16, False),
        ]
        results = [{"id": "va", "ok": True, "clips": clips, "cost_usd": 0, "elapsed_sec": 0}]
        agg = aggregate_e2e_results(results)
        assert abs(agg["judge_gap"]) < 0.5

    def test_precision_at_k_con_rankeador_perfecto(self):
        from eval_metrics import aggregate_e2e_results

        # Orden por score: todos los True primero, todos los False después.
        clips = [
            _clip_posteable(1, 30, True), _clip_posteable(2, 27, True), _clip_posteable(3, 24, True),
            _clip_posteable(4, 9, False), _clip_posteable(5, 6, False),
        ]
        results = [{"id": "va", "ok": True, "clips": clips, "cost_usd": 0, "elapsed_sec": 0}]
        agg = aggregate_e2e_results(results)
        assert agg["precision_at_3"] == 1.0

    def test_precision_at_k_con_rankeador_al_azar(self):
        from eval_metrics import aggregate_e2e_results

        clips = [
            _clip_posteable(1, 30, False), _clip_posteable(2, 27, True), _clip_posteable(3, 24, False),
        ]
        results = [{"id": "va", "ok": True, "clips": clips, "cost_usd": 0, "elapsed_sec": 0}]
        agg = aggregate_e2e_results(results)
        assert agg["precision_at_3"] == round(1 / 3, 3)

    def test_precision_at_k_none_con_menos_clips_que_k(self):
        from eval_metrics import aggregate_e2e_results

        clips = [_clip_posteable(1, 24, True)]
        results = [{"id": "va", "ok": True, "clips": clips, "cost_usd": 0, "elapsed_sec": 0}]
        agg = aggregate_e2e_results(results)
        assert agg["precision_at_3"] is None

    def test_judge_humano_corr_alto_con_separacion_clara(self):
        from eval_metrics import aggregate_e2e_results

        clips = [
            _clip_posteable(i, 30 - i, i < 5)  # scores 29..20, True para los primeros 5
            for i in range(10)
        ]
        results = [{"id": "va", "ok": True, "clips": clips, "cost_usd": 0, "elapsed_sec": 0}]
        agg = aggregate_e2e_results(results)
        assert agg["judge_humano_corr"] is not None
        assert agg["judge_humano_corr"] > 0.8


class TestPointBiserialYPrecisionAtK:
    """Los helpers en aislamiento (eval_metrics.point_biserial / precision_at_k)."""

    def test_point_biserial_pocos_pares(self):
        from eval_metrics import point_biserial
        assert point_biserial([(1.0, True), (2.0, False)]) is None

    def test_point_biserial_sin_varianza_en_el_score(self):
        from eval_metrics import point_biserial
        assert point_biserial([(5.0, True), (5.0, False), (5.0, True)]) is None

    def test_point_biserial_sin_varianza_en_la_etiqueta(self):
        from eval_metrics import point_biserial
        assert point_biserial([(1.0, True), (2.0, True), (3.0, True)]) is None

    def test_precision_at_k_k_cero_o_negativo(self):
        from eval_metrics import precision_at_k
        assert precision_at_k([True, False, True], 0) is None
        assert precision_at_k([True, False, True], -1) is None
