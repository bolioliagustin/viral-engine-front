"""
W8-E3 (docs/PLAN_CALIDAD.md §4 W8) — compara dos jueces sobre los clips de
una corrida e2e ya guardada, SIN re-correr el pipeline (sin descarga, sin
Whisper, sin Pasada A/B): solo dos llamadas a `services.scorer.judge_moment_scores`
por clip, una por modelo. Costo esperado: ~30 clips x 2 jueces x 1 llamada
corta, del orden de US$0.03-0.05 — no los ~US$0.5 de un e2e completo.

Uso:
    python eval/comparar_jueces.py eval/runs/2026-09-20-w8-e1.json
    python eval/comparar_jueces.py eval/runs/2026-09-20-w8-e1.json \
        --model-a openai/gpt-5.4-nano --model-b openai/gpt-5.4-mini --json
    python eval/comparar_jueces.py eval/runs/2026-09-20-w8-e1.json --partial
    python eval/comparar_jueces.py eval/runs/2026-09-20-w8-e1.json eval/runs/2026-09-20-integracion-fase0.json --json

TEXTO DEL CLIP: el JSON de una corrida e2e no guarda el transcript completo
por clip (`eval/eval_metrics.py::build_e2e_clip_record` trunca a
`first_words`/`last_words`), pero si el job corrió con
`TRANSCRIPT_SOURCE=whisper_full|hybrid` el transcript completo del video SÍ
queda cacheado (`transcription_cache`, por `youtube_id`) — el mismo mecanismo
que usó INT-4 (`eval_metrics.resolve_capitalization`/`find_line_at_start`)
para la mayúscula por Línea. Este script reconstruye el texto real del clip
concatenando las Líneas cuyo rango se solapa con `[start_time, end_time]`
(ver `_full_clip_text`). Si no hay transcript cacheado para ese video (source
distinto de whisper_full/hybrid, o cache vencido), degrada a un texto PARCIAL
(primeras + últimas palabras guardadas) y lo marca en `text_source` — con
`--partial` se fuerza ese modo parcial para todos los clips (más rápido,
sirve como punto de referencia "peor caso"). Se puede pasar más de un JSON
de corrida (los clips de todas se juntan en un solo pool, cada uno anotado
con `run_label` = nombre del archivo) para juntar tamaño de muestra sin
pagar un e2e nuevo.

CONTROL DE SANIDAD: cuando el texto es "full", se compara el re-juzgado con
`model_a` contra el `score_judge` ya guardado en el JSON de la corrida
(mismo modelo, mismo texto real — deberían parecerse; si difieren mucho, hay
un bug de reconstrucción o de matching de Líneas, no una diferencia de
juez). Con texto "partial" esa comparación no es válida (el juez vio menos
texto que en la corrida real) y se excluye del control de sanidad.

Lo que decide E3 es la DISTRIBUCIÓN entre `model_a` y `model_b` sobre el
MISMO texto (¿discrimina más el nuevo juez? ¿aparecen clips ≥7?), no el
promedio absoluto de uno contra el otro.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKER_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(WORKER_DIR.parent / ".env")
load_dotenv(WORKER_DIR / ".env")

from openai import OpenAI  # noqa: E402

from config.pricing import estimate_llm_cost_usd  # noqa: E402
from services.scorer import judge_moment_scores  # noqa: E402

GOLDEN_SET_PATH = Path(__file__).resolve().parent / "golden_set.json"
DEFAULT_MODEL_A = "openai/gpt-5.4-nano"
DEFAULT_MODEL_B = "openai/gpt-5.4-mini"


def _load_categories() -> dict[str, str]:
    data = json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))
    return {v["id"]: v.get("expected_category") or "business" for v in data.get("videos", [])}


def _load_clips(run: dict, run_label: str) -> list[dict]:
    """Clips de una corrida, anotados con `_run_label` (de qué archivo vienen
    — dos corridas pueden repetir (video_id, moment_index), así que hace
    falta para no confundirlos al mostrar resultados)."""
    clips = []
    for video in run.get("results") or []:
        for clip in video.get("clips") or []:
            clip = dict(clip)
            clip["_run_label"] = run_label
            clips.append(clip)
    return clips


def _partial_clip_text(clip: dict) -> str:
    """Texto parcial (primeras + últimas palabras guardadas en el JSON)."""
    first = " ".join(clip.get("first_words") or [])
    last = " ".join(clip.get("last_words") or [])
    if first and last:
        return f"{first} [...] {last}"
    return first or last or ""


def _load_lines_by_video(youtube_ids: set[str]) -> dict[str, list[dict] | None]:
    """Líneas del transcript completo cacheado (INT-4), una lookup por video."""
    from services.transcript_cache import get_cached_transcript
    from services.transcriber import full_transcript_model

    model = full_transcript_model()
    out: dict[str, list[dict] | None] = {}
    for yid in youtube_ids:
        if not yid:
            continue
        try:
            cached = get_cached_transcript(yid, source="whisper_full", model=model)
        except Exception:
            cached = None
        out[yid] = (cached or {}).get("lines")
    return out


def _full_clip_text(clip: dict, lines_by_video: dict[str, list[dict] | None]) -> str | None:
    """Texto real del clip: Líneas cuyo rango se solapa con [start_time, end_time]."""
    lines = lines_by_video.get(clip.get("youtube_id"))
    start, end = clip.get("start_time"), clip.get("end_time")
    if not lines or start is None or end is None:
        return None
    start, end = float(start), float(end)
    selected = [
        ln for ln in lines
        if ln.get("start") is not None and ln.get("end") is not None
        and float(ln["end"]) > start and float(ln["start"]) < end
    ]
    if not selected:
        return None
    selected.sort(key=lambda ln: ln.get("start", 0))
    text = " ".join((ln.get("text") or "").strip() for ln in selected).strip()
    return text or None


def _resolve_clip_texts(clips: list[dict], *, allow_full: bool) -> list[tuple[str, str]]:
    """(texto, fuente) por clip — fuente en {'full', 'partial', 'none'}."""
    lines_by_video: dict[str, list[dict] | None] = {}
    if allow_full:
        lines_by_video = _load_lines_by_video({c.get("youtube_id") for c in clips})

    out = []
    for clip in clips:
        text = _full_clip_text(clip, lines_by_video) if allow_full else None
        if text:
            out.append((text, "full"))
            continue
        text = _partial_clip_text(clip)
        out.append((text, "partial") if text else ("", "none"))
    return out


class _TrackedCompletions:
    """Envuelve `client.chat.completions` para registrar tokens por llamada
    sin tocar `services/scorer.py` (judge_moment_scores acepta `client=`)."""

    def __init__(self, inner, sink: list[dict]):
        self._inner = inner
        self._sink = sink

    def create(self, **kwargs):
        resp = self._inner.create(**kwargs)
        usage = getattr(resp, "usage", None)
        if usage is not None:
            self._sink.append({
                "model": kwargs.get("model"),
                "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
            })
        return resp


class _TrackedClient:
    def __init__(self, real_client, sink: list[dict]):
        self.chat = self
        self.completions = _TrackedCompletions(real_client.chat.completions, sink)


def _judge_all(
    clips: list[dict],
    texts: list[tuple[str, str]],
    model: str,
    categories: dict[str, str],
    usage_sink: list[dict],
) -> list[dict | None]:
    prev_model = os.environ.get("MODEL_JUDGE")
    os.environ["MODEL_JUDGE"] = model
    try:
        real_client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.getenv("OPENROUTER_API_KEY"))
        tracked = _TrackedClient(real_client, usage_sink)
        out = []
        for clip, (text, _source) in zip(clips, texts):
            scores = judge_moment_scores(
                text,
                hook=clip.get("hook") or "",
                viral_overlay=clip.get("viral_overlay") or "",
                category=categories.get(clip.get("video_id"), "business"),
                clip_duration_sec=clip.get("duration_final_sec") or clip.get("duration_chosen_sec") or 0,
                client=tracked,
            )
            out.append(scores)
        return out
    finally:
        if prev_model is None:
            os.environ.pop("MODEL_JUDGE", None)
        else:
            os.environ["MODEL_JUDGE"] = prev_model


def _avg(scores: dict | None) -> float | None:
    if not scores:
        return None
    return round((scores["hook"] + scores["retention"] + scores["shareability"]) / 3, 3)


def _all_ge7(scores: dict | None) -> bool:
    return bool(scores) and all((scores.get(k) or 0) >= 7 for k in ("hook", "retention", "shareability"))


def _dist(values: list[float]) -> dict:
    vals = [v for v in values if v is not None]
    if not vals:
        return {"n": 0, "mean": None, "stdev": None, "min": None, "max": None}
    return {
        "n": len(vals),
        "mean": round(statistics.fmean(vals), 3),
        "stdev": round(statistics.pstdev(vals), 3) if len(vals) > 1 else 0.0,
        "min": round(min(vals), 2),
        "max": round(max(vals), 2),
    }


def _correlation(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) <= 2 or len({p[0] for p in pairs}) <= 1 or len({p[1] for p in pairs}) <= 1:
        return None
    try:
        return round(statistics.correlation([p[0] for p in pairs], [p[1] for p in pairs]), 3)
    except statistics.StatisticsError:
        return None


def _cost(usage_sink: list[dict]) -> float:
    return round(sum(estimate_llm_cost_usd(u["model"], u["input_tokens"], u["output_tokens"]) for u in usage_sink), 4)


def _sanity_check_vs_stored(clips: list[dict], texts: list[tuple[str, str]], scores_a: list[dict | None]) -> dict:
    """model_a sobre texto 'full' vs el score_judge ya guardado en la corrida
    (mismo modelo, texto real en ambos casos — deberían parecerse)."""
    diffs = []
    pairs = []
    n_considered = 0
    for clip, (_text, source), sa in zip(clips, texts, scores_a):
        if source != "full":
            continue
        stored = clip.get("score_judge")
        new_avg, stored_avg = _avg(sa), _avg(stored)
        if new_avg is None or stored_avg is None:
            continue
        n_considered += 1
        diffs.append(abs(new_avg - stored_avg))
        pairs.append((stored_avg, new_avg))
    return {
        "n_considered": n_considered,
        "mean_abs_diff": round(statistics.fmean(diffs), 3) if diffs else None,
        "max_abs_diff": round(max(diffs), 3) if diffs else None,
        "correlation_stored_vs_new": _correlation(pairs),
    }


def compare(clips: list[dict], model_a: str, model_b: str, categories: dict[str, str], *, allow_full: bool) -> dict:
    texts = _resolve_clip_texts(clips, allow_full=allow_full)
    text_sources = {"full": 0, "partial": 0, "none": 0}
    for _text, source in texts:
        text_sources[source] += 1

    usage_a: list[dict] = []
    usage_b: list[dict] = []
    scores_a = _judge_all(clips, texts, model_a, categories, usage_a)
    scores_b = _judge_all(clips, texts, model_b, categories, usage_b)

    avgs_a = [_avg(s) for s in scores_a]
    avgs_b = [_avg(s) for s in scores_b]
    paired = [(a, b) for a, b in zip(avgs_a, avgs_b) if a is not None and b is not None]

    per_clip = []
    for clip, (text, source), sa, sb in zip(clips, texts, scores_a, scores_b):
        per_clip.append({
            "run_label": clip.get("_run_label"),
            "video_id": clip.get("video_id"),
            "moment_index": clip.get("moment_index"),
            "text_source": source,
            "text_chars": len(text),
            "scores_a": sa,
            "scores_b": sb,
            "avg_a": _avg(sa),
            "avg_b": _avg(sb),
            "ge7_a": _all_ge7(sa),
            "ge7_b": _all_ge7(sb),
        })

    n = len(clips)
    return {
        "model_a": model_a,
        "model_b": model_b,
        "n_clips": n,
        "text_sources": text_sources,
        "dist_avg_a": _dist(avgs_a),
        "dist_avg_b": _dist(avgs_b),
        "all_ge7_rate_a": round(sum(1 for s in scores_a if _all_ge7(s)) / n, 3) if n else None,
        "all_ge7_rate_b": round(sum(1 for s in scores_b if _all_ge7(s)) / n, 3) if n else None,
        "correlation_avg_a_b": _correlation(paired),
        "sanity_check_a_vs_stored": _sanity_check_vs_stored(clips, texts, scores_a),
        "cost_usd_a": _cost(usage_a),
        "cost_usd_b": _cost(usage_b),
        "cost_usd_total": round(_cost(usage_a) + _cost(usage_b), 4),
        "per_clip": per_clip,
    }


def render_text(result: dict) -> str:
    ts = result["text_sources"]
    lines = [
        f"Juez A: {result['model_a']}  |  Juez B: {result['model_b']}  |  {result['n_clips']} clips",
        f"Texto: {ts['full']} full (transcript real) · {ts['partial']} partial (first/last words) · {ts['none']} sin texto",
        "",
    ]
    da, db = result["dist_avg_a"], result["dist_avg_b"]
    lines.append(f"{'':<22} {'A':>10} {'B':>10}")
    lines.append("─" * 46)
    lines.append(f"{'avg (mean)':<22} {da['mean']!s:>10} {db['mean']!s:>10}")
    lines.append(f"{'avg (stdev)':<22} {da['stdev']!s:>10} {db['stdev']!s:>10}")
    range_a = f"{da['min']}-{da['max']}"
    range_b = f"{db['min']}-{db['max']}"
    lines.append(f"{'avg (min-max)':<22} {range_a:>10} {range_b:>10}")
    lines.append(f"{'ge7_en_las_tres':<22} {result['all_ge7_rate_a']:>9.0%} {result['all_ge7_rate_b']:>9.0%}")
    lines.append("")
    lines.append(f"Correlación (avg A vs avg B, por clip): {result['correlation_avg_a_b']}")
    sc = result["sanity_check_a_vs_stored"]
    lines.append(
        f"Control de sanidad (A sobre texto full vs score_judge guardado, n={sc['n_considered']}): "
        f"diff abs media={sc['mean_abs_diff']} max={sc['max_abs_diff']} correlación={sc['correlation_stored_vs_new']}"
    )
    lines.append(
        f"Costo: A=${result['cost_usd_a']:.4f}  B=${result['cost_usd_b']:.4f}  "
        f"total=${result['cost_usd_total']:.4f}"
    )
    lines.append("")
    lines.append("Por clip:")
    lines.append(f"{'clip':<44} {'texto':<8} {'avg A':>7} {'avg B':>7} {'≥7 A':>6} {'≥7 B':>6}")
    lines.append("─" * 80)
    for row in result["per_clip"]:
        name = f"[{row['run_label']}] {row['video_id']} m{row['moment_index']}"
        lines.append(
            f"{name:<44} {row['text_source']:<8} {row['avg_a']!s:>7} {row['avg_b']!s:>7} "
            f"{'sí' if row['ge7_a'] else '—':>6} {'sí' if row['ge7_b'] else '—':>6}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compara dos jueces sobre los clips de una o más corridas e2e guardadas")
    parser.add_argument(
        "run_jsons", nargs="+",
        help="JSON de una o más corridas del tier e2e (eval/runs/*.json) — los clips de todas se juntan en un solo pool",
    )
    parser.add_argument("--model-a", default=DEFAULT_MODEL_A, help=f"Juez actual (default {DEFAULT_MODEL_A})")
    parser.add_argument("--model-b", default=DEFAULT_MODEL_B, help=f"Juez candidato (default {DEFAULT_MODEL_B})")
    parser.add_argument(
        "--partial", action="store_true",
        help="Forzar texto parcial (first/last words), sin buscar el transcript completo cacheado",
    )
    parser.add_argument("--json", action="store_true", help="Salida JSON en vez de tabla")
    args = parser.parse_args(argv)

    clips = []
    for run_json in args.run_jsons:
        run = json.loads(Path(run_json).read_text(encoding="utf-8"))
        clips.extend(_load_clips(run, Path(run_json).stem))
    categories = _load_categories()

    # En modo --json, cualquier print() suelto durante compare() (cliente de
    # Supabase, log_llm_usage, etc. — igual que eval/run_golden_set.py) debe
    # ir a stderr; el JSON final es lo único que sale por el stdout real.
    real_stdout = sys.stdout
    if args.json:
        sys.stdout = sys.stderr
    try:
        result = compare(clips, args.model_a, args.model_b, categories, allow_full=not args.partial)
    finally:
        sys.stdout = real_stdout
    result["source_runs"] = args.run_jsons

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2), file=real_stdout)
    else:
        print(render_text(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
