"""
Calibración de rankeadores contra "posteable" (W12, docs/PLAN_CALIDAD.md
§2/§5) — la herramienta con la que se deciden rankeadores de acá en
adelante: antes de cambiar `MODEL_JUDGE`, activar `RANKER=jev` en
producción, o probar cualquier otro rankeador, se corre esto contra las
etiquetas REALES de `clip_feedback` (W7) — no contra el juez de esa misma
corrida, que es la comparación que ya sabemos que no sirve (el motivo de
esta tarea: 20 clips reales etiquetados el 21-sep dieron gap +0,29 y
correlación 0,06 entre el Juez y "lo publicaría").

Genérico por diseño: compara la etiqueta humana contra CUALQUIER score
numérico por clip (`--score-field`, default `score_judge_sum` — la suma
0-30 del Juez, siempre disponible porque el Juez corre en todo job sin
importar `RANKER`, ver `services/ranker_jev.py`). Un rankeador nuevo se
evalúa con la misma función (`calibrar()`) en cuanto su score quede en
algún lado consultable por clip. HOY eso es solo `score_judge`
(persistido en `content_results`): Jev decide el ORDEN de entrega pero no
persiste su score crudo por candidato fuera de la corrida en memoria
(`main.py` solo guarda `judge_scores`/`w2_score` en `candidates_all`, ver
`main._annotate_candidates_all_with_judge`) — compararlo acá de verdad
necesitaría (a) volver a correrlo (cuesta API) o (b) que ese guardado
empiece a incluir `jev_rank_score` (cambio de código en `main.py`, fuera
del alcance de esta tarea, que no lo toca).

Uso — sobre clips que YA existen, sin llamar a ninguna API (solo lee
Supabase: content_results, jobs, clip_feedback, y transcription_cache
para reconstruir el texto de los casos donde más se equivoca):

    python eval/calibracion.py --job 9e739c7b-f07b-4a5a-a9b3-54569372fd39 \
                                --job c7ea4108-2409-4e51-af40-f6c3c6137dbe
    python eval/calibracion.py --job <id> --job <id> --json
    python eval/calibracion.py --job <id> --job <id> --k 3 5 10 --peores 5
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKER_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(WORKER_DIR.parent / ".env")
load_dotenv(WORKER_DIR / ".env")

from eval_metrics import point_biserial, precision_at_k  # noqa: E402


def _judge_sum(score_judge: dict | None) -> float | None:
    if not score_judge:
        return None
    try:
        return (
            float(score_judge.get("hook") or 0)
            + float(score_judge.get("retention") or 0)
            + float(score_judge.get("shareability") or 0)
        )
    except (TypeError, ValueError):
        return None


def _extract_youtube_id(video_url: str | None) -> str | None:
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([a-zA-Z0-9_-]{11})", video_url or "")
    return m.group(1) if m else None


def load_clips_etiquetados(job_ids: list[str]) -> list[dict]:
    """
    Clips REALES de los `job_ids` dados que TIENEN etiqueta en
    `clip_feedback`. Cada uno trae `score_judge_sum` (0-30, siempre que el
    Juez haya corrido — no depende de `RANKER`), `posteable`, `motivo`, y
    lo necesario para reconstruir el texto (`youtube_id`, tiempos).
    Un Momento tiene 3 filas de `content_results`; se usa una sola.
    """
    from etiquetas import fetch_etiquetas
    from services.supabase_client import get_supabase

    supabase = get_supabase()
    if not supabase or not job_ids:
        return []

    _, por_momento = fetch_etiquetas()

    crs = (
        supabase.table("content_results")
        .select("id, job_id, moment_index, score_judge, hook, start_time, end_time, viral_overlay")
        .in_("job_id", job_ids)
        .execute()
        .data
        or []
    )
    por_momento_cr: dict[tuple, dict] = {}
    for cr in crs:
        key = (cr["job_id"], cr.get("moment_index"))
        if key not in por_momento_cr:
            por_momento_cr[key] = cr

    jobs = {
        j["id"]: j
        for j in (supabase.table("jobs").select("id, video_url").in_("id", job_ids).execute().data or [])
    }

    out = []
    for (job_id, moment_index), cr in por_momento_cr.items():
        etiqueta = por_momento.get((job_id, moment_index))
        if etiqueta is None:
            continue
        score_judge = cr.get("score_judge")
        if isinstance(score_judge, str):
            score_judge = json.loads(score_judge)
        video_url = (jobs.get(job_id) or {}).get("video_url")
        out.append({
            "job_id": job_id,
            "moment_index": moment_index,
            "content_result_id": cr["id"],
            "youtube_id": _extract_youtube_id(video_url),
            "hook": cr.get("hook"),
            "viral_overlay": cr.get("viral_overlay"),
            "start_time": cr.get("start_time"),
            "end_time": cr.get("end_time"),
            "score_judge": score_judge,
            "score_judge_sum": _judge_sum(score_judge),
            "posteable": etiqueta.posteable,
            "motivo": etiqueta.motivo,
        })
    return out


def _lines_by_video(youtube_ids: set[str | None]) -> dict[str, list[dict] | None]:
    """Líneas del transcript completo cacheado (INT-4/W12), para reconstruir
    el texto real de un clip sin volver a transcribir nada."""
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


def _full_text(clip: dict, lines_by_video: dict[str, list[dict] | None]) -> str:
    """Texto real del clip (Líneas que se solapan con [start_time, end_time]),
    o el hook como respaldo si no hay transcript cacheado para ese video."""
    lines = lines_by_video.get(clip.get("youtube_id"))
    start, end = clip.get("start_time"), clip.get("end_time")
    if lines and start is not None and end is not None:
        start, end = float(start), float(end)
        selected = [
            ln for ln in lines
            if ln.get("start") is not None and ln.get("end") is not None
            and float(ln["end"]) > start and float(ln["start"]) < end
        ]
        if selected:
            selected.sort(key=lambda ln: ln.get("start", 0))
            text = " ".join((ln.get("text") or "").strip() for ln in selected).strip()
            if text:
                return text
    return clip.get("hook") or ""


def calibrar(
    clips: list[dict],
    *,
    score_field: str = "score_judge_sum",
    ks: tuple[int, ...] = (3, 5, 10),
    peores_n: int = 5,
    con_texto: bool = True,
) -> dict:
    """
    Calibra un rankeador (el score en `score_field` de cada clip) contra
    `posteable`: precision@k (¿el rankeador sirve para elegir qué
    entregar?), correlación punto-biserial, gap de promedio, y los casos
    donde más se equivoca (con el texto real del clip, para leerlos).
    """
    con_score = [c for c in clips if c.get(score_field) is not None]
    ranked = sorted(con_score, key=lambda c: c[score_field], reverse=True)
    labels_ranked = [bool(c.get("posteable")) for c in ranked]

    pairs = [(c[score_field], bool(c.get("posteable"))) for c in con_score]
    corr = point_biserial(pairs)

    posteable_si = [c for c in con_score if c.get("posteable")]
    posteable_no = [c for c in con_score if not c.get("posteable")]
    avg_si = round(sum(c[score_field] for c in posteable_si) / len(posteable_si), 3) if posteable_si else None
    avg_no = round(sum(c[score_field] for c in posteable_no) / len(posteable_no), 3) if posteable_no else None

    # Los casos donde más se equivoca: posteable=True con el score MÁS BAJO
    # (falsos negativos del rankeador — un clip bueno que quedaría afuera)
    # y posteable=False con el score MÁS ALTO (falsos positivos — un clip
    # malo que el rankeador pondría arriba de todo).
    falsos_negativos = sorted(posteable_si, key=lambda c: c[score_field])[:peores_n]
    falsos_positivos = sorted(posteable_no, key=lambda c: c[score_field], reverse=True)[:peores_n]

    lines_by_video: dict = {}
    if con_texto:
        lines_by_video = _lines_by_video({c.get("youtube_id") for c in falsos_negativos + falsos_positivos})

    def _describe(c: dict) -> dict:
        d = {
            "job_id": c["job_id"], "moment_index": c["moment_index"], "score": c[score_field],
            "posteable": c["posteable"], "motivo": c.get("motivo"),
        }
        if con_texto:
            d["texto"] = _full_text(c, lines_by_video)[:400]
        return d

    return {
        "score_field": score_field,
        "n_clips_con_etiqueta": len(clips),
        "n_con_score": len(con_score),
        "n_sin_score": len(clips) - len(con_score),
        "posteable_rate": round(len(posteable_si) / len(con_score), 3) if con_score else None,
        "avg_posteable": avg_si,
        "avg_no_posteable": avg_no,
        "gap": round(avg_si - avg_no, 3) if avg_si is not None and avg_no is not None else None,
        "correlacion_punto_biserial": corr,
        "precision_at_k": {str(k): precision_at_k(labels_ranked, k) for k in ks},
        "peores_falsos_negativos": [_describe(c) for c in falsos_negativos],
        "peores_falsos_positivos": [_describe(c) for c in falsos_positivos],
    }


def render_text(result: dict) -> str:
    lines = [
        f"Calibración del rankeador ({result['score_field']})",
        f"  {result['n_con_score']}/{result['n_clips_con_etiqueta']} clips etiquetados con score "
        f"({result['n_sin_score']} sin score)",
        f"  posteable_rate: {result['posteable_rate']}",
        f"  promedio posteable: {result['avg_posteable']}  |  "
        f"promedio no-posteable: {result['avg_no_posteable']}  |  gap: {result['gap']}",
        f"  correlación punto-biserial (Juez↔humano): {result['correlacion_punto_biserial']}",
        "  precision@k: " + ", ".join(f"k={k}: {v}" for k, v in result["precision_at_k"].items()),
        "",
        "  Falsos negativos — posteable=True con el score MÁS BAJO (el rankeador los pondría afuera):",
    ]
    for c in result["peores_falsos_negativos"]:
        texto = c.get("texto", "")
        lines.append(f"    job={c['job_id'][:8]} m{c['moment_index']} score={c['score']:.1f} — {texto[:140]}")
    lines.append("")
    lines.append("  Falsos positivos — posteable=False con el score MÁS ALTO (el rankeador los pondría arriba):")
    for c in result["peores_falsos_positivos"]:
        texto = c.get("texto", "")
        lines.append(
            f"    job={c['job_id'][:8]} m{c['moment_index']} score={c['score']:.1f} "
            f"motivo={c['motivo']} — {texto[:140]}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Calibra un rankeador (Juez u otro) contra las etiquetas reales de clip_feedback"
    )
    parser.add_argument("--job", action="append", dest="jobs", required=True, help="job_id a incluir (repetible)")
    parser.add_argument("--score-field", default="score_judge_sum", help="campo de score a evaluar (default: el Juez)")
    parser.add_argument("--k", type=int, nargs="+", default=[3, 5, 10])
    parser.add_argument("--peores", type=int, default=5, help="cuántos casos mostrar por categoría de error")
    parser.add_argument("--sin-texto", action="store_true", help="no reconstruir el texto (más rápido, sin transcript_cache)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    # `get_supabase()` (services/supabase_client.py) hace `print()` directo
    # ("Connecting to Supabase...") en vez de loggear — en modo --json eso
    # ensucia stdout y rompe cualquier consumidor (`| jq`, `json.load`).
    # Mismo patrón que run_golden_set.py con el logger: silenciar stdout
    # mientras se cargan los datos, y solo imprimir el JSON limpio al final.
    if args.json:
        with contextlib.redirect_stdout(io.StringIO()):
            clips = load_clips_etiquetados(args.jobs)
            result = calibrar(
                clips, score_field=args.score_field, ks=tuple(args.k), peores_n=args.peores,
                con_texto=not args.sin_texto,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        clips = load_clips_etiquetados(args.jobs)
        result = calibrar(
            clips, score_field=args.score_field, ks=tuple(args.k), peores_n=args.peores,
            con_texto=not args.sin_texto,
        )
        print(render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
