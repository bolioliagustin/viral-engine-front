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


class TestThresholdsBlocking:
    def test_e2e_no_bloquea_por_defecto_en_golden_set(self):
        from eval_metrics import resolve_tier_config
        golden = json.loads((WORKER_DIR / "eval" / "golden_set.json").read_text(encoding="utf-8"))
        assert resolve_tier_config(golden, "e2e")["thresholds_blocking"] is False
        assert resolve_tier_config(golden, "smoke")["thresholds_blocking"] is True
