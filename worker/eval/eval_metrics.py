"""
Métricas y umbrales del golden set por tier (smoke / analysis / full).

Separado de run_golden_set.py para tests unitarios y evolución del eval.
"""
from __future__ import annotations

import statistics
from typing import Any


DEFAULT_TIER = "analysis"

TIER_ORDER = ("smoke", "analysis", "full")


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
        id_set = set(tier_video_ids)
        enabled = [v for v in enabled if v.get("id") in id_set]
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
