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

TIER_ORDER = ("smoke", "analysis", "full", "e2e", "seleccion")

# Tiers que corren aislados de las cachés de producción (PLAN_MEJORA §4.1).
TIERS_AISLADOS = ("seleccion", "e2e")

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
        # False = los umbrales se informan pero no hacen fallar la corrida
        # (exit 0). Útil mientras son objetivos y no regresiones.
        "thresholds_blocking": bool(cfg.get("thresholds_blocking", True)),
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


# ─── Posteable — la fuente de verdad de calidad (W12, PLAN_CALIDAD.md §2/§5) ─
def point_biserial(pairs: list[tuple[float, bool]]) -> float | None:
    """
    Correlación punto-biserial entre un score continuo (juez, Jev, o
    cualquier rankeador) y una etiqueta binaria (`posteable`) — caso
    particular de Pearson con una variable 0/1, que es justo lo que
    `statistics.correlation` calcula. None con <3 pares o sin varianza en
    alguna de las dos variables (score constante, o todas las etiquetas
    iguales) — ahí la correlación no está definida, no es cero.
    """
    if len(pairs) <= 2:
        return None
    xs = [float(s) for s, _ in pairs]
    ys = [1.0 if b else 0.0 for _, b in pairs]
    if len(set(xs)) <= 1 or len(set(ys)) <= 1:
        return None
    try:
        return round(statistics.correlation(xs, ys), 3)
    except statistics.StatisticsError:
        return None


def precision_at_k(ranked_labels: list[bool], k: int) -> float | None:
    """
    De los `k` mejores según el ranking (la lista ya viene ordenada,
    mejor primero), qué fracción son `posteable=True`. Mide si el
    RANKEADOR sirve (aplica igual al Juez, a Jev, o a cualquier otro):
    un rankeador que no discrimina da precision@k parecida para cualquier
    k, uno que sí discrimina la sube al bajar k. None con menos de `k`
    clips etiquetados (no hay top-k que calcular).
    """
    if k <= 0 or len(ranked_labels) < k:
        return None
    top = ranked_labels[:k]
    return round(sum(1 for v in top if v) / k, 3)


def clip_starts_capitalized(first_words: list[str]) -> bool | None:
    """True si la primera palabra Whisper empieza con mayúscula (inicio de oración)."""
    for w in first_words or []:
        txt = (w or "").strip()
        for ch in txt:
            if ch.isalpha():
                return ch.isupper()
    return None


# ─── Mayúscula inicial por Línea (INT-4, docs/PLAN_CALIDAD.md §9 W1-C) ──────
# Hallazgo de INT-3: `clip_starts_capitalized` mide la primera palabra de la
# RE-TRANSCRIPCIÓN Whisper aislada del clip final (`whisper_words`) — un
# Whisper nuevo sobre solo esos 30-60s, sin el contexto de la oración
# anterior. Un corte bien alineado al inicio de una Línea (W1-C,
# `line_aligned` en `clip_quality_issues`) puede seguir dando ahí una palabra
# en minúscula porque Whisper, sin ese contexto, no siempre la capitaliza.
# La métrica que hay que comparar contra el objetivo de W1-C (≥90%) es la
# mayúscula de la Línea del TRANSCRIPT COMPLETO (W4, TRANSCRIPT_SOURCE=
# whisper_full) que decidió el corte, no la re-transcripción aislada.
#
# Tolerancia (INT-5): `content_results.start_time` se persiste como
# `integer` (supabase/migrations/20260708000000_remote_schema.sql); medio
# segundo de redondeo antes de cualquier imprecisión real del corte, así
# que ±0,3s no puede matchear más de la mitad de los casos por
# construcción — medido en 2026-09-20-integracion-fase0.json: 17/27 (63%)
# con ±0,3s, 25/27 (93%) con ±0,6s (8 casos caían justo en la ventana
# 0,3-0,6s, todos por el mismo redondeo). Subida a 0,6s en INT-5.
LINE_START_TOLERANCE_SEC = 0.6


def find_line_at_start(lines: list[dict] | None, start_time: float | None, tolerance: float = LINE_START_TOLERANCE_SEC) -> dict | None:
    """
    Línea del transcript completo (W4, `services.transcript_lines`) cuyo
    `start` cae a ≤`tolerance` s de `start_time` (mismo eje absoluto que
    `content_results.start_time`). Devuelve la más cercana, o None si
    ninguna Línea cae dentro de la tolerancia (o no hay Líneas).
    """
    if not lines or start_time is None:
        return None
    best, best_dist = None, None
    for ln in lines:
        ls = ln.get("start")
        if ls is None:
            continue
        dist = abs(float(ls) - float(start_time))
        if dist <= tolerance and (best_dist is None or dist < best_dist):
            best, best_dist = ln, dist
    return best


def line_starts_capitalized(line: dict | None) -> bool | None:
    """True si la primera letra del texto de la Línea es mayúscula."""
    if not line:
        return None
    txt = (line.get("text") or "").strip()
    for ch in txt:
        if ch.isalpha():
            return ch.isupper()
    return None


def resolve_capitalization(
    *,
    first_words: list[str],
    clip_quality_issues: list[str] | None,
    start_time: float | None,
    lines: list[dict] | None,
) -> tuple[bool | None, bool | None]:
    """
    (starts_capitalized, starts_capitalized_whisper). El segundo es SIEMPRE
    la métrica vieja (re-transcripción aislada, para seguir comparando con
    corridas anteriores). El primero usa la Línea del transcript completo
    cuando el clip está `line_aligned` (W1-C) y hay Líneas disponibles para
    ese job (`TRANSCRIPT_SOURCE=whisper_full|hybrid`); si no hay match o no
    hay Líneas, cae a la métrica vieja (degradación grácil).
    """
    whisper_val = clip_starts_capitalized(first_words)
    if "line_aligned" in (clip_quality_issues or []):
        line = find_line_at_start(lines, start_time)
        line_val = line_starts_capitalized(line)
        if line_val is not None:
            return line_val, whisper_val
    return whisper_val, whisper_val


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
    cap_whisper = [
        c.get("starts_capitalized_whisper") for c in clips
        if c.get("starts_capitalized_whisper") is not None
    ]
    dens = [c.get("density_out_of_range") for c in clips if c.get("density_out_of_range") is not None]
    rendered = sum(1 for c in clips if c.get("clip_rendered"))

    total_cost = round(sum(float(r.get("cost_usd") or 0) for r in ok_results), 4)
    total_seconds = round(sum(float(r.get("elapsed_sec") or 0) for r in results), 1)

    # INT-4 (docs/PLAN_CALIDAD.md §9 W9-B/W1-C): agregados que antes había
    # que recalcular a mano en cada lectura.
    def _jsum(c: dict) -> float:
        j = c.get("score_judge") or {}
        try:
            return (
                float(j.get("hook") or 0) + float(j.get("retention") or 0)
                + float(j.get("shareability") or 0)
            )
        except (TypeError, ValueError):
            return 0.0

    top5_clips: list[dict] = []
    for r in ok_results:
        judged_v = [c for c in (r.get("clips") or []) if c.get("score_judge")]
        top5_clips.extend(sorted(judged_v, key=_jsum, reverse=True)[:5])
    judge_avg_top5 = _mean([_jsum(c) / 3 for c in top5_clips])

    delivered_per_video = [
        {"video_id": r.get("id"), "clips_count": len(r.get("clips") or [])}
        for r in results
    ]

    videos_with_duration = [r for r in ok_results if r.get("video_duration_sec")]
    total_hours = sum(float(r["video_duration_sec"]) for r in videos_with_duration) / 3600
    clips_with_duration = sum(len(r.get("clips") or []) for r in videos_with_duration)
    clips_per_hour = round(clips_with_duration / total_hours, 3) if total_hours > 0 else None

    # W12 (docs/PLAN_CALIDAD.md §2/§5): posteable pasa a ser la métrica
    # PRINCIPAL del tier — el juez es secundario, calibrado contra esto.
    # Todo se calcula solo sobre los clips que TIENEN etiqueta (`posteable`
    # no es None); con 0 etiquetados (el caso normal del tier e2e hoy, que
    # corre en dry-run y nunca llega a un usuario real) todo queda en None
    # y `posteable_labeled_n=0` — nunca 0.0 disfrazado de "0% posteable".
    labeled = [c for c in clips if c.get("posteable") is not None]
    posteable_si = [c for c in labeled if c.get("posteable")]
    posteable_no = [c for c in labeled if not c.get("posteable")]

    motivos_rechazo: dict[str, int] = {}
    for c in posteable_no:
        motivo = c.get("posteable_motivo") or "sin_motivo"
        motivos_rechazo[motivo] = motivos_rechazo.get(motivo, 0) + 1

    def _judge_avg_de(cs: list[dict]) -> float | None:
        vals = [_jsum(c) / 3 for c in cs if c.get("score_judge")]
        return round(statistics.fmean(vals), 3) if vals else None

    judge_posteable_avg = _judge_avg_de(posteable_si)
    judge_no_posteable_avg = _judge_avg_de(posteable_no)
    judge_gap = (
        round(judge_posteable_avg - judge_no_posteable_avg, 3)
        if judge_posteable_avg is not None and judge_no_posteable_avg is not None
        else None
    )

    corr_pairs = [
        (_jsum(c), bool(c.get("posteable"))) for c in labeled if c.get("score_judge")
    ]
    judge_humano_corr = point_biserial(corr_pairs)

    ranked_labeled = [c for c in sorted(labeled, key=_jsum, reverse=True)]
    ranked_labels = [bool(c.get("posteable")) for c in ranked_labeled]
    precision_at_3 = precision_at_k(ranked_labels, 3)
    precision_at_5 = precision_at_k(ranked_labels, 5)
    precision_at_10 = precision_at_k(ranked_labels, 10)

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
        "judge_avg_top5": judge_avg_top5,
        "verification_failed_rate": _rate(verification_failed, len(with_flags)),
        "late_hook_rate": _rate(late_hook, len(clips)),
        "whisper_mismatch_last_rate": _rate(mismatch_last, len(clips)),
        "capitalized_start_rate": _rate(sum(1 for v in cap if v), len(cap)),
        "capitalized_start_whisper_rate": _rate(sum(1 for v in cap_whisper if v), len(cap_whisper)),
        "density_out_of_range_rate": _rate(sum(1 for v in dens if v), len(dens)),
        "clips_per_hour": clips_per_hour,
        "delivered_per_video": delivered_per_video,
        "duration_chosen_avg": _mean([c.get("duration_chosen_sec") for c in clips]),
        "duration_final_avg": _mean([c.get("duration_final_sec") for c in clips]),
        "total_cost_usd": total_cost,
        "total_seconds": total_seconds,
        # W12 — posteable, la métrica principal a partir de ahora (PLAN_CALIDAD §2/§5).
        "posteable_labeled_n": len(labeled),
        "posteable_rate": _rate(len(posteable_si), len(labeled)),
        "motivos_rechazo": motivos_rechazo,
        "judge_posteable_avg": judge_posteable_avg,
        "judge_no_posteable_avg": judge_no_posteable_avg,
        "judge_gap": judge_gap,
        "judge_humano_corr": judge_humano_corr,
        "precision_at_3": precision_at_3,
        "precision_at_5": precision_at_5,
        "precision_at_10": precision_at_10,
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
    "judge_avg_top5": "higher",
    "clips_rendered_rate": "higher",
    "capitalized_start_rate": "higher",
    "capitalized_start_whisper_rate": "higher",
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
    "clips_per_hour": "higher",
    "videos_ok": "higher",
    "videos_evaluated": "neutral",
    # W12 (docs/PLAN_CALIDAD.md §2/§5): posteable es la métrica PRINCIPAL
    # a partir de ahora — el juez queda secundario.
    "posteable_rate": "higher",
    "posteable_labeled_n": "neutral",  # tamaño de muestra, no una mejora en sí
    "judge_posteable_avg": "neutral",  # informativo: no es una meta subir esto solo
    "judge_no_posteable_avg": "neutral",
    "judge_gap": "higher",  # separación del juez entre unos y otros — si no crece, el juez no sirve
    "judge_humano_corr": "higher",
    "precision_at_3": "higher",
    "precision_at_5": "higher",
    "precision_at_10": "higher",
}

E2E_THRESHOLD_KEYS: dict[str, tuple[str, str]] = {
    # W12: posteable_rate es el umbral que importa a partir de ahora
    # (objetivo 0.70, PLAN_CALIDAD §3); los del juez quedan informativos
    # (siguen acá, thresholds_blocking los sigue mandando a informativo).
    "posteable_rate": ("posteable_rate_min", "min"),
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
    lines: list[dict] | None = None,
    etiqueta: Any = None,
) -> dict[str, Any]:
    """
    Construye el registro por clip del tier e2e a partir de las filas que
    save_content_result habría insertado para ese momento (en dry-run hay
    hasta tres, una por pieza de copy, con los metadatos repetidos: se usa la
    primera).

    `lines` (INT-4): Líneas del transcript completo de este video (W4,
    TRANSCRIPT_SOURCE=whisper_full|hybrid), para `starts_capitalized` por
    Línea en vez de por la re-transcripción aislada del clip — ver
    `resolve_capitalization`. None si el job corrió con `supadata` o no se
    pudo obtener el transcript cacheado (degrada a la métrica vieja).

    `etiqueta` (W12, `eval.etiquetas.Etiqueta`): la etiqueta humana
    "posteable" de este Momento, si alguna corrida REAL (no el dry-run del
    tier e2e, que nunca llega a un usuario) la etiquetó — ver
    `eval.etiquetas`. None en el caso normal de hoy (el golden set corre en
    dry-run); cuando un video del golden set trae `real_job_id` (§ eval/README.md),
    el caller la resuelve y la pasa acá.
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
    issues = list(row.get("clip_quality_issues") or [])
    starts_capitalized, starts_capitalized_whisper = resolve_capitalization(
        first_words=first_words, clip_quality_issues=issues, start_time=start, lines=lines,
    )

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
        "starts_capitalized": starts_capitalized,
        "starts_capitalized_whisper": starts_capitalized_whisper,
        "words_per_sec": wps,
        "density_out_of_range": density_out_of_range(wps),
        "sub_coverage": cov,
        "verification_failed": row.get("verification_failed"),
        "clip_quality_issues": issues,
        "score_llm": row.get("score_llm"),
        "score_judge": judge,
        "judge_all_ge7": judge_all_ge(judge),
        "hook": row.get("hook"),
        "viral_overlay": row.get("viral_overlay"),
        "clip_rendered": rendered,
        "clip_url": clip_url or None,
        "clip_generation_error": row.get("clip_generation_error"),
        "copy_types": sorted({r.get("type") for r in rows if r.get("type")}),
        "posteable": etiqueta.posteable if etiqueta is not None else None,
        "posteable_motivo": etiqueta.motivo if etiqueta is not None else None,
    }


# ─── W19: métricas contra Referencias (tiers `seleccion` y `e2e`) ────────────
#
# Definiciones en eval/README.md ("Métricas contra Referencias"). Todas son
# funciones puras: reciben intervalos (dicts con start/end) y Referencias
# (dicts con nucleo_inicio/nucleo_fin/calidad) y no tocan la red.

# Tolerancia de "contenido": el candidato puede empezar hasta 2 s después del
# núcleo y terminar hasta 2 s antes (timestamps de Líneas redondeados).
TOLERANCIA_CONTENIDO_SEC = 2.0
COBERTURA_PARCIAL_MIN = 0.5
COBERTURA_PARTIDA_MIN = 0.8
SOLAPE_EXCLUSION_MIN = 0.5
K_PRECISION_REF = (5, 10)


def _intervalo(c: dict) -> tuple[float, float] | None:
    ini = c.get("start_time", c.get("start"))
    fin = c.get("end_time", c.get("end"))
    if ini is None or fin is None:
        return None
    ini, fin = float(ini), float(fin)
    return (ini, fin) if fin > ini else None


def _nucleo(ref: dict) -> tuple[float, float]:
    return float(ref["nucleo_inicio"]), float(ref["nucleo_fin"])


def contiene_nucleo(cand: dict, ref: dict, tol: float = TOLERANCIA_CONTENIDO_SEC) -> bool:
    iv = _intervalo(cand)
    if iv is None:
        return False
    ni, nf = _nucleo(ref)
    return iv[0] <= ni + tol and iv[1] >= nf - tol


def cobertura_nucleo(cand: dict, ref: dict) -> float:
    """Fracción del núcleo que cubre el candidato (0–1)."""
    iv = _intervalo(cand)
    ni, nf = _nucleo(ref)
    if iv is None or nf <= ni:
        return 0.0
    return max(0.0, min(iv[1], nf) - max(iv[0], ni)) / (nf - ni)


def _union_cubre(intervalos: list[tuple[float, float]], ni: float, nf: float) -> float:
    """Fracción de [ni, nf] cubierta por la unión de los intervalos."""
    recortes = sorted((max(a, ni), min(b, nf)) for a, b in intervalos if min(b, nf) > max(a, ni))
    total, cur_a, cur_b = 0.0, None, None
    for a, b in recortes:
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                total += cur_b - cur_a
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        total += cur_b - cur_a
    return total / (nf - ni) if nf > ni else 0.0


def clasificar_referencia(ref: dict, candidatos: list[dict]) -> str:
    """
    'completa' (algún candidato contiene el núcleo ±2 s), 'partida' (ninguno
    lo contiene pero la unión de ≥ 2 candidatos cubre ≥ 80 %), 'parcial'
    (algún candidato cubre ≥ 50 %) o 'ausente'.
    """
    if any(contiene_nucleo(c, ref) for c in candidatos):
        return "completa"
    ni, nf = _nucleo(ref)
    tocan = [iv for iv in (_intervalo(c) for c in candidatos) if iv and iv[1] > ni and iv[0] < nf]
    if len(tocan) >= 2 and _union_cubre(tocan, ni, nf) >= COBERTURA_PARTIDA_MIN:
        return "partida"
    if any(cobertura_nucleo(c, ref) >= COBERTURA_PARCIAL_MIN for c in candidatos):
        return "parcial"
    return "ausente"


def candidatos_por_cuarto(candidatos: list[dict], duracion_sec: float) -> list[int]:
    """Cuenta candidatos por cuarto del video, según el punto medio de cada uno."""
    cuartos = [0, 0, 0, 0]
    if not duracion_sec or duracion_sec <= 0:
        return cuartos
    for c in candidatos:
        iv = _intervalo(c)
        if iv is None:
            continue
        medio = (iv[0] + iv[1]) / 2
        cuartos[min(3, max(0, int(medio / (duracion_sec / 4))))] += 1
    return cuartos


def en_exclusion(cand: dict, excluir: list[dict], umbral: float = SOLAPE_EXCLUSION_MIN) -> bool:
    """El candidato se solapa > 50 % de su duración con algún tramo excluido."""
    iv = _intervalo(cand)
    if iv is None:
        return False
    dur = iv[1] - iv[0]
    for e in excluir or []:
        inter = max(0.0, min(iv[1], float(e["fin"])) - max(iv[0], float(e["inicio"])))
        if dur > 0 and inter / dur > umbral:
            return True
    return False


def ordenar_por_ranking(candidatos: list[dict]) -> list[dict]:
    """Orden del pipeline: `rank_score` desc (Pasada A); sin score, orden original."""
    indexados = list(enumerate(candidatos))
    indexados.sort(key=lambda t: (-(t[1].get("rank_score") if t[1].get("rank_score") is not None else float("-inf")), t[0]))
    return [c for _, c in indexados]


def metricas_referencias(
    candidatos: list[dict],
    referencias: list[dict],
    *,
    duracion_sec: float | None,
    excluir: list[dict] | None = None,
    k_precision: tuple[int, ...] = K_PRECISION_REF,
) -> dict[str, Any]:
    """
    Métricas de cobertura de un conjunto de intervalos (candidatos de la
    Pasada A, o clips entregados) contra las Referencias de un video.
    """
    refs_a = [r for r in referencias if r.get("calidad") == "A"]
    estado = {r["id"]: clasificar_referencia(r, candidatos) for r in referencias}

    def _recall(refs, ok) -> float | None:
        return _rate(sum(1 for r in refs if ok(r)), len(refs))

    completa = lambda r: estado[r["id"]] == "completa"  # noqa: E731
    parcial = lambda r: any(cobertura_nucleo(c, r) >= COBERTURA_PARCIAL_MIN for c in candidatos)  # noqa: E731

    cuartos = candidatos_por_cuarto(candidatos, duracion_sec or 0)
    total_cuartos = sum(cuartos)
    ranked = ordenar_por_ranking(candidatos)
    precision = {}
    for k in k_precision:
        top = ranked[:k]
        precision[f"precision_ref@{k}"] = _rate(
            sum(1 for c in top if any(cobertura_nucleo(c, r) >= COBERTURA_PARCIAL_MIN for r in referencias)),
            len(top),
        )

    return {
        "n_candidatos": len(candidatos),
        "n_referencias": len(referencias),
        "n_referencias_a": len(refs_a),
        "recall_completo": _recall(refs_a, completa),
        "recall_completo_ab": _recall(referencias, completa),
        "recall_parcial": _recall(refs_a, parcial),
        "recall_parcial_ab": _recall(referencias, parcial),
        "historias_partidas": sum(1 for e in estado.values() if e == "partida"),
        "historias_partidas_ids": sorted(rid for rid, e in estado.items() if e == "partida"),
        "candidatos_por_cuarto": cuartos,
        "min_cuarto": _rate(min(cuartos), total_cuartos) if total_cuartos else None,
        "candidatos_en_exclusion": sum(1 for c in candidatos if en_exclusion(c, excluir or [])),
        **precision,
        "estado_por_referencia": estado,
    }


def captura_de_lo_mejor(
    referencias: list[dict],
    entregados: list[dict],
    *,
    posteable: list[bool | None] | None = None,
) -> dict[str, Any]:
    """
    Métrica norte (PLAN_MEJORA §3): Referencias A cuyo núcleo está contenido
    en un clip **entregado** y etiquetado posteable. `posteable[i]` es la
    etiqueta del entregado i (None = sin etiqueta). Si ningún entregado tiene
    etiqueta, devuelve la versión "contenido en un entregado" y lo marca en
    `tipo` para que nadie la confunda con la norte.
    """
    refs_a = [r for r in referencias if r.get("calidad") == "A"]
    etiquetas = list(posteable) if posteable is not None else [None] * len(entregados)
    con_etiqueta = any(e is not None for e in etiquetas)
    if con_etiqueta:
        validos = [c for c, e in zip(entregados, etiquetas) if e is True]
        tipo = "posteable"
    else:
        validos = list(entregados)
        tipo = "contenido_en_entregado_sin_etiquetas"
    capturadas = sorted(r["id"] for r in refs_a if any(contiene_nucleo(c, r) for c in validos))
    return {
        "captura_de_lo_mejor": _rate(len(capturadas), len(refs_a)),
        "captura_tipo": tipo,
        "captura_ids": capturadas,
        "captura_etiquetados_n": sum(1 for e in etiquetas if e is not None),
    }


# Métricas numéricas que se promedian entre repeticiones y videos.
METRICAS_SELECCION = (
    "recall_completo", "recall_completo_ab", "recall_parcial", "recall_parcial_ab",
    "historias_partidas", "min_cuarto", "candidatos_en_exclusion",
    "precision_ref@5", "precision_ref@10", "n_candidatos",
)


def media_y_desvio(valores: list[float | None]) -> dict[str, Any]:
    vs = [float(v) for v in valores if v is not None]
    if not vs:
        return {"media": None, "desvio": None, "n": 0}
    return {
        "media": round(statistics.mean(vs), 4),
        "desvio": round(statistics.stdev(vs), 4) if len(vs) > 1 else 0.0,
        "n": len(vs),
    }


def agregar_repeticiones(reps: list[dict]) -> dict[str, Any]:
    """Media y desvío por métrica sobre las repeticiones de un video."""
    ok = [r["metricas"] for r in reps if r.get("metricas")]
    out = {m: media_y_desvio([r.get(m) for r in ok]) for m in METRICAS_SELECCION}
    # Estado por Referencia: en cuántas reps quedó completa (estabilidad)
    completas: dict[str, int] = {}
    for r in ok:
        for rid, e in (r.get("estado_por_referencia") or {}).items():
            completas[rid] = completas.get(rid, 0) + (1 if e == "completa" else 0)
    out["completa_en_reps"] = completas
    return out


def agregar_seleccion(por_video: list[dict]) -> dict[str, Any]:
    """Promedio macro (por video) de las medias de cada métrica."""
    ok = [v for v in por_video if v.get("agregado")]
    return {
        m: media_y_desvio([(v["agregado"].get(m) or {}).get("media") for v in ok])
        for m in METRICAS_SELECCION
    }
