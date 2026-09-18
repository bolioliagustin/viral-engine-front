"""
Compara dos corridas del golden set (tier e2e) y muestra el delta por métrica.

    python eval/compare_runs.py eval/runs/2026-09-17-v4-baseline.json eval/runs/2026-09-24-v5.json
    python eval/compare_runs.py a.json b.json --json > delta.json

Para cada métrica agregada imprime el valor A, el valor B y el delta con una
flecha según la dirección de la métrica (ver eval_metrics.E2E_METRIC_DIRECTIONS):
▲ mejora, ▼ empeora, = sin cambio, · neutral (duraciones, conteos).

Además compara el juez clip por clip cuando el video y el momento coinciden en
las dos corridas (mismo `video_id` y `moment_index`). Ojo: si la Pasada A
cambió (PROMPT_VERSION distinto), el momento N de un video puede ser otro
fragmento; la comparación por clip es orientativa, la agregada es la que vale.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_metrics import E2E_METRIC_DIRECTIONS  # noqa: E402

# Orden de impresión (las que no están acá van después, en orden alfabético)
_METRIC_ORDER = [
    "videos_ok",
    "clips_total",
    "clips_judged",
    "clips_rendered_rate",
    "judge_hook_avg",
    "judge_retention_avg",
    "judge_shareability_avg",
    "judge_avg",
    "judge_all_ge7_rate",
    "verification_failed_rate",
    "late_hook_rate",
    "whisper_mismatch_last_rate",
    "capitalized_start_rate",
    "density_out_of_range_rate",
    "duration_chosen_avg",
    "duration_final_avg",
    "total_cost_usd",
    "total_seconds",
]

_RATE_METRICS = {m for m in E2E_METRIC_DIRECTIONS if m.endswith("_rate")}


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _fmt(metric: str, value) -> str:
    if value is None:
        return "n/a"
    if metric in _RATE_METRICS:
        return f"{value:.0%}"
    if metric == "total_cost_usd":
        return f"${value:.4f}"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _fmt_delta(metric: str, delta) -> str:
    if delta is None:
        return "n/a"
    sign = "+" if delta > 0 else ""
    if metric in _RATE_METRICS:
        return f"{sign}{delta * 100:.0f} pp"
    if metric == "total_cost_usd":
        return f"{sign}${delta:.4f}"
    if isinstance(delta, float):
        return f"{sign}{delta:.2f}"
    return f"{sign}{delta}"


def classify_delta(metric: str, delta) -> str:
    """'better' | 'worse' | 'same' | 'neutral' | 'unknown' según la dirección de la métrica."""
    if delta is None:
        return "unknown"
    direction = E2E_METRIC_DIRECTIONS.get(metric, "neutral")
    if abs(delta) < 1e-9:
        return "same"
    if direction == "neutral":
        return "neutral"
    improved = delta > 0 if direction == "higher" else delta < 0
    return "better" if improved else "worse"


_ARROW = {"better": "▲", "worse": "▼", "same": "=", "neutral": "·", "unknown": "?"}


def compare_metrics(a: dict, b: dict) -> list[dict]:
    """Lista de {metric, a, b, delta, verdict} para todas las métricas numéricas."""
    keys = [k for k in _METRIC_ORDER if k in a or k in b]
    extra = sorted(
        k for k in set(a) | set(b)
        if k not in keys and isinstance(a.get(k, b.get(k)), (int, float))
        and not isinstance(a.get(k, b.get(k)), bool)
        and k in E2E_METRIC_DIRECTIONS
    )
    rows = []
    for k in keys + extra:
        va, vb = a.get(k), b.get(k)
        delta = None
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            delta = round(vb - va, 4)
        rows.append({"metric": k, "a": va, "b": vb, "delta": delta, "verdict": classify_delta(k, delta)})
    return rows


def _clips_by_key(summary: dict) -> dict[tuple, dict]:
    out = {}
    for r in summary.get("results") or []:
        for c in r.get("clips") or []:
            out[(c.get("video_id"), c.get("moment_index"))] = c
    return out


def _judge_avg(clip: dict | None) -> float | None:
    j = (clip or {}).get("score_judge")
    if not j:
        return None
    try:
        return round((float(j["hook"]) + float(j["retention"]) + float(j["shareability"])) / 3, 2)
    except (KeyError, TypeError, ValueError):
        return None


def compare_clips(a: dict, b: dict) -> list[dict]:
    """Juez por clip cuando (video_id, moment_index) existe en ambas corridas."""
    ca, cb = _clips_by_key(a), _clips_by_key(b)
    rows = []
    for key in sorted(set(ca) | set(cb), key=lambda k: (str(k[0]), k[1] or 0)):
        va, vb = _judge_avg(ca.get(key)), _judge_avg(cb.get(key))
        delta = round(vb - va, 2) if va is not None and vb is not None else None
        rows.append({
            "video_id": key[0],
            "moment_index": key[1],
            "in_a": key in ca,
            "in_b": key in cb,
            "judge_a": va,
            "judge_b": vb,
            "delta": delta,
            "verdict": classify_delta("judge_avg", delta),
            "flags_a": (ca.get(key) or {}).get("clip_quality_issues"),
            "flags_b": (cb.get(key) or {}).get("clip_quality_issues"),
            "start_a": (ca.get(key) or {}).get("first_words"),
            "start_b": (cb.get(key) or {}).get("first_words"),
        })
    return rows


def _header(summary: dict, label: str) -> str:
    return (
        f"{label}: {summary.get('run_at', '?')} | PROMPT_VERSION={summary.get('prompt_version', '?')} "
        f"| commit={summary.get('git_commit', '?')} | modelos={summary.get('models', {})}"
    )


def render_text(a: dict, b: dict, metric_rows: list[dict], clip_rows: list[dict]) -> str:
    lines = [_header(a, "A"), _header(b, "B"), ""]
    lines.append(f"{'métrica':<28} {'A':>10} {'B':>10} {'delta':>12}  ")
    lines.append("─" * 66)
    for r in metric_rows:
        m = r["metric"]
        lines.append(
            f"{m:<28} {_fmt(m, r['a']):>10} {_fmt(m, r['b']):>10} "
            f"{_fmt_delta(m, r['delta']):>12} {_ARROW[r['verdict']]}"
        )
    better = sum(1 for r in metric_rows if r["verdict"] == "better")
    worse = sum(1 for r in metric_rows if r["verdict"] == "worse")
    lines.append("")
    lines.append(f"▲ {better} mejoran · ▼ {worse} empeoran")

    if clip_rows:
        lines.append("")
        lines.append("Juez por clip (video/momento presente en ambas corridas):")
        lines.append(f"{'clip':<34} {'A':>6} {'B':>6} {'delta':>7}")
        lines.append("─" * 58)
        for r in clip_rows:
            name = f"{r['video_id']} m{r['moment_index']}"
            if not (r["in_a"] and r["in_b"]):
                side = "solo en A" if r["in_a"] else "solo en B"
                lines.append(f"{name:<34} {side}")
                continue
            fa = "n/a" if r["judge_a"] is None else f"{r['judge_a']:.2f}"
            fb = "n/a" if r["judge_b"] is None else f"{r['judge_b']:.2f}"
            fd = "n/a" if r["delta"] is None else f"{r['delta']:+.2f}"
            lines.append(f"{name:<34} {fa:>6} {fb:>6} {fd:>7} {_ARROW[r['verdict']]}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Delta entre dos corridas del tier e2e")
    parser.add_argument("baseline", help="JSON de la corrida A (baseline)")
    parser.add_argument("candidate", help="JSON de la corrida B (nueva)")
    parser.add_argument("--json", action="store_true", help="Salida JSON en vez de tabla")
    args = parser.parse_args(argv)

    a, b = _load(args.baseline), _load(args.candidate)
    metric_rows = compare_metrics(a, b)
    clip_rows = compare_clips(a, b)

    if args.json:
        out = {
            "a": {k: a.get(k) for k in ("run_at", "prompt_version", "git_commit", "models")},
            "b": {k: b.get(k) for k in ("run_at", "prompt_version", "git_commit", "models")},
            "metrics": metric_rows,
            "clips": clip_rows,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(render_text(a, b, metric_rows, clip_rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
