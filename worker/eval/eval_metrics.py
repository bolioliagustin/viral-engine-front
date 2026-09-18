"""
Métricas y umbrales del golden set por tier (smoke / analysis / full / e2e).

Separado de run_golden_set.py para tests unitarios y evolución del eval.

El tier `e2e` tiene su propio agregador (`aggregate_e2e_results`) y chequeo
(`check_e2e_thresholds`): sus métricas salen de clips reales (juez, flags,
densidad, duraciones, costo), no de la Pasada A sobre el transcript.
"""
from __future__ import annotations

import statistics
from typing import Any


DEFAULT_TIER = "analysis"

TIER_ORDER = ("smoke", "analysis", "full", "e2e")

# Densidad de palabras plausible (palabras/s). Fuera de este rango el clip es
# basura: sin habla o con timestamps Whisper rotos (PLAN_CALIDAD §1.1).
DENSITY_MIN_WPS = 1.2
DENSITY_MAX_WPS = 5.0

# Juez ≥ 7 en las tres métricas = clip "bueno" según la rúbrica.
JUDGE_GOOD_MIN = 7


def resolve_tier_config(golden: dict, tier: str) -> dict:
    """Merge tier thresholds sobre defaults globales."""
    tiers = golden.get("tiers") or {}
    if tier not in tiers:
        raise ValueError(f"Tier desconocido: {tier!r}. Usar: {', '.join(TIER_ORDER)}")
    cfg = tiers[tier]
    thresholds = dict(golden.get("thresholds") or {})
    thresholds.update(cfg.get("thresholds") or {})
    return {
        "tier": tier,
        "description": cfg.get("_doc", ""),
        "include_copy": bool(cfg.get("include_copy", tier == "full")),
        "video_ids": cfg.get("video_ids"),
        "thresholds": thresholds,
    }


def filter_videos_for_tier(videos: list[dict], tier: str, tier_video_ids: list[str] | None) -> list[dict]:
    """Filtra videos habilitados según tier y tags opcionales por video."""
    enabled = [v for v in videos if v.get("enabled", True)]
    if tier_video_ids:
        # Respeta el orden de la lista del tier (permite dejar el video largo al final)
        by_id = {v.get("id"): v for v in enabled}
        enabled = [by_id[i] for i in tier_video_ids if i in by_id]
    else:
        enabled = [
            v for v in enabled
            if tier in (v.get("tiers") or TIER_ORDER)
        ]
    return enabled


def aggregate_results(results: list[dict], *, with_copy: bool) -> dict[str, Any]:
    """Agrega métricas de evaluate_video() a nivel suite."""
    ok_results = [r for r in results if r.get("ok")]
    cat_checked = [r for r in ok_results if r.get("category_match") is not None]
    category_accuracy = (
        sum(1 for r in cat_checked if r["category_match"]) / len(cat_checked)
        if cat_checked else None
    )
    total_selected = sum(r.get("moments_selected", 0) for r in ok_results)
    total_valid = sum(r.get("moments_valid", 0) for r in ok_results)
    total_truncated = sum(r.get("moments_truncated", 0) for r in ok_results)
    duration_pass_rate = (total_valid / total_selected) if total_selected else None
    untruncated_rate = (1 - total_truncated / total_selected) if total_selected else None

    strict_total = sum(r.get("verification_strict_total", 0) for r in ok_results)
    strict_pass = sum(r.get("verification_strict_pass", 0) for r in ok_results)
    anchor_total = sum(r.get("phrase_anchor_total", 0) for r in ok_results)
    anchor_pass = sum(r.get("phrase_anchor_pass", 0) for r in ok_results)

    verification_strict_pass_rate = (strict_pass / strict_total) if strict_total else None
    phrase_anchor_pass_rate = (anchor_pass / anchor_total) if anchor_total else None

    punct_rates = [
        r["last_phrase_punct_rate"]
        for r in ok_results
        if r.get("last_phrase_punct_rate") is not None
    ]
    last_phrase_punct_rate = statistics.mean(punct_rates) if punct_rates else None
    min_moments = min((r.get("moments_valid", 0) for r in ok_results), default=0)

    summary: dict[str, Any] = {
        "videos_evaluated": len(results),
        "videos_ok": len(ok_results),
        "category_accuracy": category_accuracy,
        "duration_pass_rate": duration_pass_rate,
        "duration_untruncated_rate": untruncated_rate,
        "verification_strict_pass_rate": verification_strict_pass_rate,
        "phrase_anchor_pass_rate": phrase_anchor_pass_rate,
        "last_phrase_punct_rate": last_phrase_punct_rate,
        "min_moments_per_video": min_moments,
        "results": results,
    }

    if with_copy:
        copy_moments = sum(r.get("copy_moments", 0) for r in ok_results)
        copy_clean = sum(r.get("copy_clean_moments", 0) for r in ok_results)
        summary["copy_clean_rate"] = (copy_clean / copy_moments) if copy_moments else None
        deltas = [r.get("judge_delta_avg") for r in ok_results if r.get("judge_delta_avg") is not None]
        summary["judge_llm_delta_avg"] = (
            round(statistics.mean(deltas), 2) if deltas else None
        )
        judge_ok = sum(r.get("judge_ok_count", 0) for r in ok_results)
        judge_total = sum(r.get("judge_total", 0) for r in ok_results)
        summary["judge_response_rate"] = (judge_ok / judge_total) if judge_total else None

    return summary


def check_thresholds(summary: dict, thresholds: dict, *, with_copy: bool) -> list[str]:
    """Devuelve lista de fallos de umbral (vacía = pass)."""
    failures: list[str] = []

    def _min_check(name: str, value: float | None, key: str) -> None:
        thr = thresholds.get(key)
        if thr is None or value is None:
            return
        if value < thr:
            failures.append(f"{name}={value:.2f} < {thr} ({key})")

    def _max_check(name: str, value: float | None, key: str) -> None:
        thr = thresholds.get(key)
        if thr is None or value is None:
            return
        if value > thr:
            failures.append(f"{name}={value:.2f} > {thr} ({key})")

    if summary.get("videos_ok", 0) < summary.get("videos_evaluated", 0):
        n = summary["videos_evaluated"] - summary["videos_ok"]
        failures.append(f"{n} video(s) fallaron la evaluación")

    _min_check("category_accuracy", summary.get("category_accuracy"), "category_accuracy_min")
    _min_check("duration_pass_rate", summary.get("duration_pass_rate"), "duration_pass_rate_min")
    _min_check(
        "duration_untruncated_rate",
        summary.get("duration_untruncated_rate"),
        "duration_untruncated_rate_min",
    )
    _min_check(
        "phrase_anchor_pass_rate",
        summary.get("phrase_anchor_pass_rate"),
        "phrase_anchor_pass_rate_min",
    )
    _min_check(
        "verification_strict_pass_rate",
        summary.get("verification_strict_pass_rate"),
        "verification_strict_pass_rate_min",
    )
    # Legacy alias
    if thresholds.get("verification_pass_rate_min") is not None:
        _min_check(
            "phrase_anchor_pass_rate",
            summary.get("phrase_anchor_pass_rate"),
            "verification_pass_rate_min",
        )
    _min_check(
        "last_phrase_punct_rate",
        summary.get("last_phrase_punct_rate"),
        "last_phrase_punct_rate_min",
    )
    if thresholds.get("min_moments_per_video") is not None:
        mm = summary.get("min_moments_per_video", 0)
        thr = thresholds["min_moments_per_video"]
        if mm < thr:
            failures.append(f"min_moments_per_video={mm} < {thr}")

    if with_copy:
        _min_check("copy_clean_rate", summary.get("copy_clean_rate"), "copy_clean_rate_min")
        _max_check(
            "judge_llm_delta_avg",
            summary.get("judge_llm_delta_avg"),
            "judge_llm_delta_max",
        )
        _min_check(
            "judge_response_rate",
            summary.get("judge_response_rate"),
            "judge_response_rate_min",
        )

    return failures


# ─── Tier e2e ────────────────────────────────────────────────────────────────

def _mean(values: list) -> float | None:
    vals = [float(v) for v in values if v is not None]
    return round(statistics.mean(vals), 3) if vals else None


def _rate(count: int, total: int) -> float | None:
    return round(count / total, 4) if total else None


def clip_starts_capitalized(first_words: list[str]) -> bool | None:
    """True si la primera palabra Whisper empieza con mayúscula (inicio de oración)."""
    for w in first_words or []:
        txt = (w or "").strip()
        for ch in txt:
            if ch.isalpha():
                return ch.isupper()
    return None


def density_out_of_range(words_per_sec: float | None) -> bool | None:
    if words_per_sec is None:
        return None
    return not (DENSITY_MIN_WPS <= float(words_per_sec) <= DENSITY_MAX_WPS)


def judge_all_ge(score_judge: dict | None, threshold: int = JUDGE_GOOD_MIN) -> bool | None:
    if not score_judge:
        return None
    try:
        return all(
            float(score_judge.get(k) or 0) >= threshold
            for k in ("hook", "retention", "shareability")
        )
    except (TypeError, ValueError):
        return None


def aggregate_e2e_results(results: list[dict]) -> dict[str, Any]:
    """
    Agrega los resultados por video del tier e2e (cada uno con `clips`).

    Métricas de juez: promedio por métrica y global, % de clips con juez ≥7
    en las tres. Flags: % verification_failed, late_hook, whisper_mismatch_last.
    Cortes: % que arrancan con mayúscula, % densidad fuera de [1.2, 5.0],
    duración elegida media → final media. Recursos: costo y tiempo totales.
    """
    ok_results = [r for r in results if r.get("ok")]
    clips = [c for r in ok_results for c in (r.get("clips") or [])]
    judged = [c for c in clips if c.get("score_judge")]

    def _judge_metric(key: str) -> float | None:
        return _mean([c["score_judge"].get(key) for c in judged])

    judge_hook = _judge_metric("hook")
    judge_ret = _judge_metric("retention")
    judge_share = _judge_metric("shareability")
    judge_avg = _mean([
        (float(c["score_judge"].get("hook") or 0)
         + float(c["score_judge"].get("retention") or 0)
         + float(c["score_judge"].get("shareability") or 0)) / 3
        for c in judged
    ])

    all_ge7 = [judge_all_ge(c.get("score_judge")) for c in judged]
    all_ge7 = [v for v in all_ge7 if v is not None]

    with_flags = [c for c in clips if c.get("verification_failed") is not None]
    verification_failed = sum(1 for c in with_flags if c.get("verification_failed"))
    late_hook = sum(1 for c in clips if "late_hook" in (c.get("clip_quality_issues") or []))
    mismatch_last = sum(
        1 for c in clips if "whisper_mismatch_last" in (c.get("clip_quality_issues") or [])
    )

    cap = [c.get("starts_capitalized") for c in clips if c.get("starts_capitalized") is not None]
    dens = [c.get("density_out_of_range") for c in clips if c.get("density_out_of_range") is not None]
    rendered = sum(1 for c in clips if c.get("clip_rendered"))

    total_cost = round(sum(float(r.get("cost_usd") or 0) for r in ok_results), 4)
    total_seconds = round(sum(float(r.get("elapsed_sec") or 0) for r in results), 1)

    return {
        "videos_evaluated": len(results),
        "videos_ok": len(ok_results),
        "clips_total": len(clips),
        "clips_judged": len(judged),
        "clips_rendered_rate": _rate(rendered, len(clips)),
        "judge_hook_avg": judge_hook,
        "judge_retention_avg": judge_ret,
        "judge_shareability_avg": judge_share,
        "judge_avg": judge_avg,
        "judge_all_ge7_rate": _rate(sum(1 for v in all_ge7 if v), len(all_ge7)),
        "verification_failed_rate": _rate(verification_failed, len(with_flags)),
        "late_hook_rate": _rate(late_hook, len(clips)),
        "whisper_mismatch_last_rate": _rate(mismatch_last, len(clips)),
        "capitalized_start_rate": _rate(sum(1 for v in cap if v), len(cap)),
        "density_out_of_range_rate": _rate(sum(1 for v in dens if v), len(dens)),
        "duration_chosen_avg": _mean([c.get("duration_chosen_sec") for c in clips]),
        "duration_final_avg": _mean([c.get("duration_final_sec") for c in clips]),
        "total_cost_usd": total_cost,
        "total_seconds": total_seconds,
        "results": results,
    }


# Métrica e2e → (clave de umbral, dirección). "min": el valor debe ser ≥ umbral;
# "max": el valor debe ser ≤ umbral. compare_runs usa la misma tabla para saber
# si un delta es mejora (▲) o empeoramiento (▼).
E2E_METRIC_DIRECTIONS: dict[str, str] = {
    "judge_hook_avg": "higher",
    "judge_retention_avg": "higher",
    "judge_shareability_avg": "higher",
    "judge_avg": "higher",
    "judge_all_ge7_rate": "higher",
    "clips_rendered_rate": "higher",
    "capitalized_start_rate": "higher",
    "verification_failed_rate": "lower",
    "late_hook_rate": "lower",
    "whisper_mismatch_last_rate": "lower",
    "density_out_of_range_rate": "lower",
    "total_cost_usd": "lower",
    "total_seconds": "lower",
    "duration_chosen_avg": "neutral",
    "duration_final_avg": "neutral",
    "clips_total": "neutral",
    "clips_judged": "neutral",
    "videos_ok": "higher",
    "videos_evaluated": "neutral",
}

E2E_THRESHOLD_KEYS: dict[str, tuple[str, str]] = {
    "judge_avg": ("judge_avg_min", "min"),
    "judge_all_ge7_rate": ("judge_all_ge7_rate_min", "min"),
    "verification_failed_rate": ("verification_failed_rate_max", "max"),
    "density_out_of_range_rate": ("density_out_of_range_rate_max", "max"),
    "capitalized_start_rate": ("capitalized_start_rate_min", "min"),
    "late_hook_rate": ("late_hook_rate_max", "max"),
    "whisper_mismatch_last_rate": ("whisper_mismatch_last_rate_max", "max"),
    "clips_rendered_rate": ("clips_rendered_rate_min", "min"),
    "total_cost_usd": ("total_cost_usd_max", "max"),
}


def check_e2e_thresholds(summary: dict, thresholds: dict) -> list[str]:
    """Fallos de umbral del tier e2e (vacío = pass). Umbrales null = informativos."""
    failures: list[str] = []
    if summary.get("videos_ok", 0) < summary.get("videos_evaluated", 0):
        n = summary["videos_evaluated"] - summary["videos_ok"]
        failures.append(f"{n} video(s) fallaron la evaluación")

    for metric, (key, kind) in E2E_THRESHOLD_KEYS.items():
        thr = thresholds.get(key)
        value = summary.get(metric)
        if thr is None or value is None:
            continue
        if kind == "min" and value < thr:
            failures.append(f"{metric}={value:.2f} < {thr} ({key})")
        elif kind == "max" and value > thr:
            failures.append(f"{metric}={value:.2f} > {thr} ({key})")

    min_clips = thresholds.get("min_clips_total")
    if min_clips is not None and summary.get("clips_total", 0) < min_clips:
        failures.append(f"clips_total={summary.get('clips_total', 0)} < {min_clips}")
    return failures


def build_e2e_clip_record(
    video: dict,
    rows: list[dict],
    *,
    first_n: int = 10,
    last_n: int = 8,
) -> dict[str, Any]:
    """
    Construye el registro por clip del tier e2e a partir de las filas que
    save_content_result habría insertado para ese momento (en dry-run hay
    hasta tres, una por pieza de copy, con los metadatos repetidos: se usa la
    primera).
    """
    row = rows[0]
    ww = row.get("whisper_words") or {}
    words = [(w.get("word") or "").strip() for w in (ww.get("words") or [])]
    first_words = words[:first_n]
    last_words = words[-last_n:] if words else []

    start = row.get("start_time")
    end = row.get("end_time")
    duration_chosen = (
        round(float(end) - float(start), 2)
        if start is not None and end is not None else None
    )
    duration_final = ww.get("duration_sec")
    duration_final = round(float(duration_final), 2) if duration_final is not None else None

    clip_url = row.get("clip_url") or ""
    rendered = bool(clip_url) and "youtube.com/watch" not in clip_url
    # Mismo redondeo que save_content_result al persistir
    wps = row.get("words_per_sec")
    wps = round(float(wps), 3) if wps is not None else None
    cov = row.get("sub_coverage")
    cov = round(float(cov), 4) if cov is not None else None
    snap = ww.get("snap_trim_start")
    snap = round(float(snap), 2) if snap is not None else None
    judge = row.get("score_judge")

    return {
        "video_id": video.get("id"),
        "youtube_id": video.get("youtube_id"),
        "moment_index": row.get("moment_index"),
        "start_time": start,
        "end_time": end,
        "duration_chosen_sec": duration_chosen,
        "duration_final_sec": duration_final,
        "snap_trim_start": snap,
        "first_words": first_words,
        "last_words": last_words,
        "starts_capitalized": clip_starts_capitalized(first_words),
        "words_per_sec": wps,
        "density_out_of_range": density_out_of_range(wps),
        "sub_coverage": cov,
        "verification_failed": row.get("verification_failed"),
        "clip_quality_issues": list(row.get("clip_quality_issues") or []),
        "score_llm": row.get("score_llm"),
        "score_judge": judge,
        "judge_all_ge7": judge_all_ge(judge),
        "hook": row.get("hook"),
        "viral_overlay": row.get("viral_overlay"),
        "clip_rendered": rendered,
        "clip_url": clip_url or None,
        "clip_generation_error": row.get("clip_generation_error"),
        "copy_types": sorted({r.get("type") for r in rows if r.get("type")}),
    }
