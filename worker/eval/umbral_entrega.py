"""
Calibración del umbral de entrega (INT-5, docs/PLAN_CALIDAD.md §9,
decisión pendiente #2 de Agustín: "¿DELIVERY_JUDGE_MIN/DELIVERY_MAX_CLIPS
están bien calibrados?").

No llama a ninguna API de LLM: re-simula `moment_selector.select_finalists`
sobre las notas del juez que ya quedaron guardadas en `analysis_cache`
(`candidates_all`, W0) de la medición final de INT-3
(`2026-09-20-integracion-fase0.json`, prompt_version `v8+whisper_full`,
99 candidatos evaluados: 30+30+9+30 sobre 4 videos). Cada candidato trae
`w2_score` = `score_candidate()` ya con las penalizaciones de W2-C
aplicadas (no hace falta recalcularlo), así que barrer `DELIVERY_JUDGE_MIN`
de 12 a 21 es una comparación pura contra ese número, sin re-evaluar nada.

Solo lee Supabase (analysis_cache, sin costo de LLM) y el JSON de la
corrida (para los costos ya medidos por tarea). Uso:

    cd worker && ENVIRONMENT=development .venv/bin/python eval/umbral_entrega.py
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from services.moment_selector import (  # noqa: E402
    MAX_HOOK_SIMILARITY,
    MAX_OVERLAP_RATIO,
    _hook_similarity,
    _overlap_ratio,
    target_moment_count,
)

RUN_JSON = Path(__file__).parent / "runs" / "2026-09-20-integracion-fase0.json"
PROMPT_VERSION = "v8+whisper_full"
COST_PER_DELIVERED_CLIP_USD = 0.0073  # Pasada B, medido (eval/runs/README.md)

# video_id (golden set) -> youtube_id (clave de analysis_cache)
VIDEO_YOUTUBE_ID = {
    "business_spanish_01": "Lqq78q17jDY",
    "podcast_general_01": "XxoVRjTySsM",
    "claude_hacks_regression_01": "KXKzgeHOr7A",
    "user_recommended_01": "MaXgAEI4Vm8",
}


@dataclass
class _Cand:
    start_time: float
    end_time: float
    hook: str
    w2_score: float


def _fetch_candidates_all() -> dict[str, list[_Cand]]:
    """{video_id: [candidatos]} desde analysis_cache. None si no hay fila."""
    from services.supabase_client import get_supabase

    supabase = get_supabase()
    if not supabase:
        raise RuntimeError("Supabase no disponible — no se puede leer analysis_cache")

    out: dict[str, list[_Cand]] = {}
    for video_id, youtube_id in VIDEO_YOUTUBE_ID.items():
        res = (
            supabase.table("analysis_cache")
            .select("result")
            .eq("video_id", youtube_id)
            .eq("prompt_version", PROMPT_VERSION)
            .limit(1)
            .execute()
        )
        if not res.data:
            print(f"⚠️  {video_id} ({youtube_id}): sin fila en analysis_cache para {PROMPT_VERSION!r}")
            continue
        result = res.data[0]["result"]
        if isinstance(result, str):
            result = json.loads(result)
        candidates_all = result.get("candidates_all") or []
        cands = []
        for c in candidates_all:
            js = c.get("judge_scores")
            w2 = c.get("w2_score")
            if w2 is None:
                continue  # candidato sin evaluar (no debería pasar en esta corrida)
            cands.append(_Cand(
                start_time=float(c.get("start_time") or 0),
                end_time=float(c.get("end_time") or 0),
                hook=c.get("hook") or "",
                w2_score=float(w2),
            ))
        out[video_id] = cands
        print(f"   {video_id}: {len(cands)} candidatos con w2_score (de {len(candidates_all)} totales)")
    return out


def _deliver_at_threshold(
    candidates: list[_Cand], *, judge_min: float, floor: int, max_clips: int | None,
) -> list[_Cand]:
    """
    Mismo algoritmo que `moment_selector.select_finalists`, pero con
    `w2_score` ya calculado (no hace falta CandidateEval completo): rankea
    por score, aplica diversidad (solapamiento + hook), entrega los que
    pasan `judge_min` hasta `max_clips` (None = sin tope), y si no llega a
    `floor` completa con los siguientes mejores (ignora diversidad, igual
    que el pipeline real).
    """
    cap = max_clips if max_clips is not None else len(candidates)
    ranked = sorted(candidates, key=lambda c: c.w2_score, reverse=True)
    selected: list[_Cand] = []
    deferred: list[_Cand] = []
    for c in ranked:
        if len(selected) >= cap:
            deferred.append(c)
            continue
        conflict = any(
            _overlap_ratio(c, s) > MAX_OVERLAP_RATIO or _hook_similarity(c.hook, s.hook) > MAX_HOOK_SIMILARITY
            for s in selected
        )
        if conflict:
            deferred.append(c)
            continue
        if c.w2_score >= judge_min:
            selected.append(c)
        else:
            deferred.append(c)
    if len(selected) < floor:
        selected_ids = {id(c) for c in selected}
        for c in deferred:
            if len(selected) >= floor or len(selected) >= cap:
                break
            if id(c) in selected_ids:
                continue
            selected.append(c)
            selected_ids.add(id(c))
    return selected


def _video_durations_hours(run: dict) -> dict[str, float]:
    out = {}
    for r in run["results"]:
        dur = r.get("video_duration_sec")
        if dur:
            out[r["id"]] = float(dur) / 3600
    return out


def _eval_cost_fixed_per_video(run: dict) -> dict[str, float]:
    """Costo YA gastado por video (análisis+whisper+juez), no depende del
    umbral — solo la Pasada B (copy) escala con cuántos se entregan."""
    out = {}
    for r in run["results"]:
        cb = r.get("cost_by_task") or {}
        out[r["id"]] = sum(v or 0 for k, v in cb.items() if k != "copy")
    return out


def run_calibration(thresholds: range = range(12, 22)) -> None:
    print("📊 Calibración del umbral de entrega (INT-5)\n")
    candidates_by_video = _fetch_candidates_all()
    if not candidates_by_video:
        print("❌ No hay candidates_all en analysis_cache para ningún video — nada que calibrar.")
        return

    run = json.loads(RUN_JSON.read_text())
    duration_hours = _video_durations_hours(run)
    eval_cost_fixed = _eval_cost_fixed_per_video(run)

    videos = list(candidates_by_video.keys())
    floors = {
        vid: max(target_moment_count(duration_hours.get(vid, 0) * 3600), 3)
        for vid in videos
    }

    header = (
        f"{'umbral':>6} | {'tope':>6} | "
        + " | ".join(f"{vid[:14]:>14}" for vid in videos)
        + f" | {'clips/h avg':>11} | {'≥8/h (n)':>8} | {'juez entreg.':>12} | {'juez top5':>9} | {'US$/job avg':>11}"
    )
    print(header)
    print("-" * len(header))

    for judge_min in thresholds:
        for cap_label, cap in (("12 (actual)", 12), ("15", 15), ("20", 20), ("sin tope", None)):
            delivered_by_video: dict[str, list[_Cand]] = {}
            for vid in videos:
                delivered_by_video[vid] = _deliver_at_threshold(
                    candidates_by_video[vid], judge_min=judge_min, floor=floors[vid], max_clips=cap,
                )

            counts = {vid: len(delivered_by_video[vid]) for vid in videos}
            per_hour = {
                vid: (counts[vid] / duration_hours[vid]) if duration_hours.get(vid) else None
                for vid in videos
            }
            valid_per_hour = [v for v in per_hour.values() if v is not None]
            avg_per_hour = sum(valid_per_hour) / len(valid_per_hour) if valid_per_hour else 0.0
            n_ge8 = sum(1 for v in valid_per_hour if v >= 8.0)

            all_delivered = [c for vid in videos for c in delivered_by_video[vid]]
            judge_avg_delivered = (
                sum(c.w2_score for c in all_delivered) / 3 / len(all_delivered)
                if all_delivered else 0.0
            )

            top5_scores = []
            for vid in videos:
                d = sorted(delivered_by_video[vid], key=lambda c: c.w2_score, reverse=True)[:5]
                top5_scores.extend(c.w2_score for c in d)
            judge_avg_top5 = (sum(top5_scores) / 3 / len(top5_scores)) if top5_scores else 0.0

            costs = []
            for vid in videos:
                costs.append(eval_cost_fixed.get(vid, 0.0) + counts[vid] * COST_PER_DELIVERED_CLIP_USD)
            avg_cost = sum(costs) / len(costs) if costs else 0.0

            row = (
                f"{judge_min:>6} | {cap_label:>6} | "
                + " | ".join(f"{counts[vid]:>14}" for vid in videos)
                + f" | {avg_per_hour:>11.2f} | {n_ge8:>8} | {judge_avg_delivered:>12.2f} | {judge_avg_top5:>9.2f} | {avg_cost:>11.4f}"
            )
            print(row)
        print()


if __name__ == "__main__":
    run_calibration()
