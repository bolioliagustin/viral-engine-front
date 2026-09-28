"""
Registro de candidatos (W30, docs/ESTUDIO_ML_EVALUADOR.md §2.4 y §8.2).

Por cada Candidato evaluado en un job se inserta una fila en
`candidate_evals` con lo que el pipeline ya calculó: tiempos propuestos y
finales, texto de las Líneas del tramo, `rank_score` de la Pasada A, Juez,
Jev, flags de calidad, `w2_score`, elegido o descartado y el motivo, versión
de prompt y modelos. Es instrumentación: no cambia qué se elige ni qué se
entrega.

Reglas:
- Solo INSERT: las filas de un job nunca pisan las de otro (a diferencia de
  `analysis_cache.candidates_all`, que se reescribe por video).
- No fatal: si Supabase falla, se loguea y el job sigue.
- En dry-run (`EVAL_DRY_RUN=1`) no se escribe nada: las filas quedan en
  `DRY_RUN_CANDIDATE_EVALS` para que el eval las lea (PLAN_MEJORA §4.1).

Vocabulario (CONTEXT.md): Registro de candidatos, Candidato, Línea, Juez,
Jev, Pasada A.
"""
from __future__ import annotations

from typing import Any, Iterable

TABLE = "candidate_evals"

# Filas que se habrían insertado en dry-run (el eval las lee; ver reset_dry_run).
DRY_RUN_CANDIDATE_EVALS: list[dict] = []

# Flags de calidad del CandidateEval que viajan en la columna `flags`.
FLAG_FIELDS = (
    "hook_not_found",
    "payoff_not_found",
    "bad_segment",
    "insufficient_source",
    "timestamps_suspect",
    "late_hook",
    "incomplete_tail",
    "min_duration_reverted",
    "density_out_of_range",
)

# Dimensiones de Jev (services/ranker_jev._DIMENSIONS) → columnas.
_JEV_COLUMNS = {"gancho": "jev_hook", "retencion": "jev_retention", "compartir": "jev_shareability"}


def _num(v: Any, nd: int = 3) -> float | None:
    try:
        return None if v is None else round(float(v), nd)
    except (TypeError, ValueError):
        return None


def texto_de_lineas(transcript: dict | None, inicio: float, fin: float) -> str:
    """Texto de las Líneas (o, sin Líneas, de los segmentos) que tocan [inicio, fin]."""
    items = (transcript or {}).get("lines") or (transcript or {}).get("segments") or []
    return " ".join(
        (it.get("text") or "").strip() for it in items
        if float(it.get("end", 0)) > inicio and float(it.get("start", 0)) < fin
    ).strip()


def rank_score_pasada_a(self_score: float, proposed_end: float, transcript: dict | None) -> float:
    """El `rank_score` con el que la Pasada A ordena (`rank_and_prune_candidates`):
    auto-score menos la penalización por terminar a mitad de segmento."""
    from services.moment_selector import _segment_boundary_penalty

    return round(self_score - _segment_boundary_penalty({"end_time": proposed_end}, transcript), 2)


def construir_fila(
    *,
    job_id: str,
    video_id: str,
    video_duration: float | None,
    cand,
    moment,
    prepared,
    jev: dict | None,
    selected: bool,
    delivery_index: int | None,
    transcript: dict | None,
    contexto: dict,
) -> dict:
    """Una fila de `candidate_evals` a partir de lo que el loop ya tiene en mano.

    `cand` es el CandidateEval, `moment` el momento de la Pasada A, `prepared`
    el `_PreparedClip` (puede ser None), `jev` el dict de `jev_rank_scores`
    (None si Jev no corrió o falló) y `contexto` lo común al job
    (`prompt_version`, modelos, `ranker`).
    """
    from services.moment_selector import score_candidate

    final_start = getattr(prepared, "final_start", None) if prepared is not None else None
    final_end = getattr(prepared, "final_end", None) if prepared is not None else None
    if final_start is not None and final_end is not None:
        tramo = (float(final_start), float(final_end))
    else:
        tramo = (float(cand.start_time), float(cand.end_time))

    js = cand.judge_scores or {}
    jev_scores = (jev or {}).get("scores_0_4") or {}
    fila = {
        "job_id": job_id,
        "video_id": video_id,
        "video_duration": _num(video_duration),
        "candidate_index": int(cand.index),
        "delivery_index": delivery_index,
        "proposed_start": _num(cand.start_time),
        "proposed_end": _num(cand.end_time),
        "final_start": _num(final_start),
        "final_end": _num(final_end),
        "hook": cand.hook or None,
        "lines_text": texto_de_lineas(transcript, *tramo) or None,
        "clip_text": (getattr(prepared, "clip_text_final", None) or None) if prepared is not None else None,
        "rank_score": rank_score_pasada_a(cand.self_score, cand.end_time, transcript),
        "self_score": _num(cand.self_score),
        "judge_hook": _num(js.get("hook")),
        "judge_retention": _num(js.get("retention")),
        "judge_shareability": _num(js.get("shareability")),
        "judge_reasoning": js.get("reasoning") or None,
        "jev_rank_score": _num((jev or {}).get("rank_score")),
        **{col: _num(jev_scores.get(dim)) for dim, col in _JEV_COLUMNS.items()},
        "jev_confidence": _num((jev or {}).get("confidence_avg")),
        "usable": bool(cand.usable),
        "flags": {f: bool(getattr(cand, f, False)) for f in FLAG_FIELDS},
        "w2_score": _num(score_candidate(cand)),
        "selected": bool(selected),
        "discard_reason": None if selected else (cand.discard_reason or None),
        "ranker": contexto.get("ranker"),
        "prompt_version": contexto.get("prompt_version"),
        "analysis_model": contexto.get("analysis_model"),
        "judge_model": contexto.get("judge_model"),
        "jev_model": ((jev or {}).get("model") or contexto.get("jev_model")) if jev else None,
        "category": (getattr(moment, "category", None) or None) if moment is not None else None,
        "formato": (getattr(moment, "formato", None) or None) if moment is not None else None,
    }
    return fila


def contexto_del_job(transcript: dict | None) -> dict:
    """Lo común a todas las filas del job: versión de prompt, modelos y rankeador.
    Cada dato es best effort (None si no se puede resolver)."""
    ctx: dict[str, Any] = {}
    try:
        from services.analysis_cache import effective_prompt_version
        ctx["prompt_version"] = effective_prompt_version((transcript or {}).get("source"))
    except Exception:
        ctx["prompt_version"] = None
    try:
        from config.model_tiers import get_model
        ctx["analysis_model"] = get_model("analysis")
        ctx["judge_model"] = get_model("judge")
    except Exception:
        ctx.setdefault("analysis_model", None)
        ctx.setdefault("judge_model", None)
    try:
        from services.ranker_jev import ranker_is_jev, MODEL as JEV_MODEL
        ctx["ranker"] = "jev" if ranker_is_jev() else "juez"
        ctx["jev_model"] = JEV_MODEL
    except Exception:
        ctx["ranker"], ctx["jev_model"] = None, None
    return ctx


def guardar_filas(filas: list[dict]) -> bool:
    """Inserta las filas en `candidate_evals`. Nunca levanta: devuelve False si
    no pudo. En dry-run no escribe y las acumula en `DRY_RUN_CANDIDATE_EVALS`."""
    if not filas:
        return True
    from services.supabase_client import is_dry_run, get_supabase

    if is_dry_run():
        DRY_RUN_CANDIDATE_EVALS.extend(filas)
        return True
    try:
        supabase = get_supabase()
        if not supabase:
            print("   ⚠️ Registro de candidatos: sin Supabase, no se guardó (no fatal)")
            return False
        supabase.table(TABLE).insert(filas).execute()
        return True
    except Exception as e:
        print(f"   ⚠️ Registro de candidatos: no se pudo guardar (no fatal): {str(e)[:200]}")
        return False


def registrar_candidatos(
    *,
    job_id: str,
    video_id: str,
    video_duration: float | None,
    candidates: Iterable,
    selected: list,
    moment_by_index: dict,
    prepared_by_index: dict,
    jev_by_index: dict,
    transcript: dict | None,
) -> int:
    """Arma y guarda una fila por Candidato evaluado. Devuelve cuántas filas
    armó (0 si algo falló). Nunca levanta: el job sigue pase lo que pase.

    `selected` es la lista de finalistas en orden de entrega: la posición
    (1-based) es el `moment_index` con el que se guardan en `content_results`.
    """
    try:
        delivery = {c.index: i for i, c in enumerate(selected, start=1)}
        contexto = contexto_del_job(transcript)
        filas = [
            construir_fila(
                job_id=job_id,
                video_id=video_id,
                video_duration=video_duration,
                cand=c,
                moment=moment_by_index.get(c.index),
                prepared=prepared_by_index.get(c.index),
                jev=jev_by_index.get(c.index),
                selected=c.index in delivery,
                delivery_index=delivery.get(c.index),
                transcript=transcript,
                contexto=contexto,
            )
            for c in candidates
        ]
    except Exception as e:
        print(f"   ⚠️ Registro de candidatos: no se pudieron armar las filas (no fatal): {e}")
        return 0
    if guardar_filas(filas):
        print(f"   🗂️ Registro de candidatos: {len(filas)} filas")
    return len(filas)


def reset_dry_run() -> None:
    DRY_RUN_CANDIDATE_EVALS.clear()
