"""
W8-E2 paso 1 (docs/PLAN_CALIDAD.md §4 W8) — compara los candidatos CRUDOS de
la Pasada A (antes del Juez/Pasada B/render) entre dos `MODEL_ANALYSIS`,
contra el benchmark completo de Opus Clip sobre `podcast_general_01`
(`eval/opus_benchmark.json`, 42 clips extraídos de `responde.json` en
`eval/opus-benchmark` — ver `docs/ANALISIS_OPUS_CLIP.md`).

Métrica dura: un candidato nuestro "cubre" un clip de Opus si el solapamiento
llega a `OVERLAP_THRESHOLD` (50% por defecto) de la DURACIÓN del clip de
Opus — no de la nuestra. Un clip de Opus con más de un `rango_seg` (5 de los
42 son dos tramos contiguos, ver `docs/ANALISIS_OPUS_CLIP.md` §2.2) se trata
como una sola unidad: se fusionan sus rangos y el solapamiento es la suma
sobre esa unión. Se reporta cobertura contra los 42 y contra su top-10
(`rank <= 10`) por separado.

Corre SOLO la Pasada A (`services.processor.analyze_with_openrouter`), sin
Whisper por candidato, sin Juez, sin Pasada B, sin render — el mismo camino
que usa `eval/run_golden_set.py --tier analysis`, pero conservando la lista
CRUDA de candidatos (ese tier la descarta y solo agrega tasas).

Uso:
    python eval/cobertura_opus.py --model google/gemini-3.5-flash
    python eval/cobertura_opus.py --model google/gemini-3.5-flash --model google/gemini-3.1-pro-preview --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKER_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(WORKER_DIR.parent / ".env")
load_dotenv(WORKER_DIR / ".env")

GOLDEN_SET_PATH = Path(__file__).resolve().parent / "golden_set.json"
OPUS_BENCHMARK_PATH = Path(__file__).resolve().parent / "opus_benchmark.json"
OVERLAP_THRESHOLD = 0.5


def _load_opus_benchmark() -> dict:
    return json.loads(OPUS_BENCHMARK_PATH.read_text(encoding="utf-8"))


def _merge_ranges(ranges: list[list[float]]) -> list[list[float]]:
    """Une rangos contiguos/solapados (5 de los 42 clips de Opus tienen dos
    tramos pegados) para no contar dos veces el mismo segundo."""
    ordered = sorted((float(s), float(e)) for s, e in ranges)
    merged: list[list[float]] = []
    for s, e in ordered:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


def _overlap_sec(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _clip_overlap_sec(cand_start: float, cand_end: float, opus_ranges: list[list[float]]) -> float:
    return sum(_overlap_sec(cand_start, cand_end, s, e) for s, e in _merge_ranges(opus_ranges))


def run_pasada_a(video_id: str, model: str) -> list[dict]:
    """Candidatos crudos de la Pasada A (sin Juez, sin Pasada B, sin render)."""
    os.environ["MODEL_ANALYSIS"] = model
    from eval.run_golden_set import _get_transcript  # noqa: E402 — reuso, no duplico fetch de transcript
    from services.processor import analyze_with_openrouter  # noqa: E402

    golden = json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))
    video = next((v for v in golden["videos"] if v["id"] == video_id), None)
    if video is None:
        raise ValueError(f"video {video_id!r} no está en golden_set.json")

    loaded = _get_transcript(video)
    if not loaded:
        raise RuntimeError(f"no se pudo obtener transcript para {video_id}")
    transcript, video_info = loaded
    video_info["id"] = video_id  # asegura la key de analysis_cache

    analysis = analyze_with_openrouter(transcript, video_info)
    if not analysis:
        raise RuntimeError(f"Pasada A no devolvió resultado para {video_id} con {model}")

    candidates = []
    for m in analysis.viral_moments:
        candidates.append({
            "start_time": m.start_time,
            "end_time": m.end_time,
            "hook": m.hook,
            "scores": m.scores.model_dump() if m.scores else None,
        })
    return candidates


def coverage(candidates: list[dict], opus_clips: list[dict], *, threshold: float = OVERLAP_THRESHOLD) -> list[dict]:
    """Por cada clip de Opus, el candidato nuestro con más fracción de solapamiento
    (sobre la duración del clip de Opus) y si cruza `threshold`."""
    rows = []
    for opus in opus_clips:
        best = None
        for c in candidates:
            if c["start_time"] is None or c["end_time"] is None:
                continue
            ov = _clip_overlap_sec(float(c["start_time"]), float(c["end_time"]), opus["rangos_seg"])
            frac = ov / opus["duracion_seg"] if opus["duracion_seg"] else 0.0
            if frac > 0 and (best is None or frac > best["overlap_frac"]):
                best = {
                    "start_time": c["start_time"], "end_time": c["end_time"], "hook": c["hook"],
                    "overlap_sec": round(ov, 1), "overlap_frac": round(frac, 3),
                }
        rows.append({
            "rank": opus["rank"], "score": opus["score"], "titulo": opus["titulo"],
            "duracion_seg": opus["duracion_seg"], "match": best,
            "covered": bool(best and best["overlap_frac"] >= threshold),
        })
    return rows


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    covered = sum(1 for r in rows if r["covered"])
    top10 = [r for r in rows if r["rank"] <= 10]
    covered_top10 = sum(1 for r in top10 if r["covered"])
    return {
        "n_opus_clips": n,
        "covered_all42": covered,
        "coverage_rate_all42": round(covered / n, 3) if n else None,
        "n_top10": len(top10),
        "covered_top10": covered_top10,
        "coverage_rate_top10": round(covered_top10 / len(top10), 3) if top10 else None,
    }


def render_text(video_id: str, model: str, candidates: list[dict], rows: list[dict], summary: dict) -> str:
    lines = [
        f"Video: {video_id}  |  MODEL_ANALYSIS={model}  |  {len(candidates)} candidatos crudos",
        f"Cobertura (>={int(OVERLAP_THRESHOLD * 100)}% de la duración del clip de Opus): "
        f"{summary['covered_all42']}/{summary['n_opus_clips']} de los 42 "
        f"({summary['coverage_rate_all42']:.1%}) · top-10: {summary['covered_top10']}/{summary['n_top10']} "
        f"({summary['coverage_rate_top10']:.1%})",
        "",
        "Top-10 de Opus, uno por uno:",
    ]
    for row in sorted(rows, key=lambda r: r["rank"]):
        if row["rank"] > 10:
            continue
        tag = "✅ CUBIERTO" if row["covered"] else "—"
        m = row["match"]
        detail = f"mejor solape {m['overlap_frac']:.0%} ({m['overlap_sec']}s)" if m else "sin candidato que solape"
        lines.append(f"  #{row['rank']:<2} score={row['score']:<3} {row['titulo'][:45]:<45} {tag:<12} {detail}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cobertura de candidatos crudos de la Pasada A contra el benchmark de 42 clips de Opus")
    parser.add_argument("--model", action="append", required=True, help="MODEL_ANALYSIS a probar (repetible)")
    parser.add_argument("--json", action="store_true", help="Salida JSON en vez de tabla")
    args = parser.parse_args(argv)

    benchmark = _load_opus_benchmark()
    video_id = benchmark["video_golden_set"]
    opus_clips = benchmark["clips"]

    real_stdout = sys.stdout
    if args.json:
        sys.stdout = sys.stderr
    try:
        results = []
        for model in args.model:
            candidates = run_pasada_a(video_id, model)
            rows = coverage(candidates, opus_clips)
            summary = summarize(rows)
            results.append({
                "model": model, "video_id": video_id,
                "candidates": candidates, "coverage": rows, "summary": summary,
            })
    finally:
        sys.stdout = real_stdout

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2), file=real_stdout)
    else:
        for r in results:
            print(render_text(r["video_id"], r["model"], r["candidates"], r["coverage"], r["summary"]))
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
