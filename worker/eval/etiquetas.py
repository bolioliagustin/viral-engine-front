"""
Lector de etiquetas humanas — "posteable" (W12, docs/PLAN_CALIDAD.md §2/§5).

`clip_feedback` (W7, migración `20260918123640_clip_feedback.sql`) es la
FUENTE DE VERDAD de calidad: el Juez (y cualquier otro rankeador, Jev
incluido) es un proxy que se calibra contra esta etiqueta, no al revés.

Un Momento tiene 3 filas en `content_results` (una por pieza de copy) y el
feedback se guarda contra UNA sola (`content_result_id`); un usuario puede
re-etiquetar el mismo clip (se guarda historial completo, no se pisa la
fila vieja) — así que la etiqueta vigente es la de `created_at` más
reciente por `content_result_id`. Este módulo resuelve las dos formas en
que el resto del eval necesita consultarla: por `content_result_id` directo
(cuando ya se tiene, ej. un query a `content_results`) y por
`(job_id, moment_index)` (cuando se viene de un registro de clip que no
trae el id de fila, como los del tier `e2e`).

Degradación grácil (mismo patrón que `services/transcript_cache.py`): sin
credenciales de Supabase, sin la tabla, o sin filas, devuelve vacío — nunca
levanta una excepción. Todo lo que consulte esto sigue funcionando (con
`n_labeled=0`).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKER_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(WORKER_DIR.parent / ".env")
load_dotenv(WORKER_DIR / ".env")


@dataclass(frozen=True)
class Etiqueta:
    content_result_id: str
    job_id: str | None
    moment_index: int | None
    posteable: bool
    motivo: str | None
    comentario: str | None
    created_at: str
    user_id: str


def _get_supabase():
    try:
        from services.supabase_client import get_supabase
        return get_supabase()
    except Exception:
        return None


def fetch_etiquetas() -> tuple[dict[str, Etiqueta], dict[tuple[str, int], Etiqueta]]:
    """
    (por_content_result_id, por_job_momento) con la etiqueta MÁS RECIENTE de
    cada `content_result_id`. Sin Supabase, sin la tabla `clip_feedback`, o
    sin filas: `({}, {})` — nunca levanta.
    """
    supabase = _get_supabase()
    if not supabase:
        return {}, {}

    try:
        rows = supabase.table("clip_feedback").select("*").execute().data or []
    except Exception:
        return {}, {}
    if not rows:
        return {}, {}

    # Más reciente por content_result_id (un usuario puede re-etiquetar).
    mas_reciente: dict[str, dict] = {}
    for r in rows:
        cr_id = r.get("content_result_id")
        if not cr_id:
            continue
        prev = mas_reciente.get(cr_id)
        if prev is None or (r.get("created_at") or "") > (prev.get("created_at") or ""):
            mas_reciente[cr_id] = r

    # job_id/moment_index de cada content_result_id etiquetado, vía content_results.
    meta_by_cr: dict[str, dict] = {}
    cr_ids = list(mas_reciente.keys())
    if cr_ids:
        try:
            crs = (
                supabase.table("content_results")
                .select("id, job_id, moment_index")
                .in_("id", cr_ids)
                .execute()
                .data
                or []
            )
            meta_by_cr = {c["id"]: c for c in crs}
        except Exception:
            meta_by_cr = {}

    por_cr: dict[str, Etiqueta] = {}
    por_momento: dict[tuple[str, int], Etiqueta] = {}
    for cr_id, r in mas_reciente.items():
        meta = meta_by_cr.get(cr_id) or {}
        job_id = meta.get("job_id")
        moment_index = meta.get("moment_index")
        et = Etiqueta(
            content_result_id=cr_id,
            job_id=job_id,
            moment_index=moment_index,
            posteable=bool(r.get("posteable")),
            motivo=r.get("motivo"),
            comentario=r.get("comentario"),
            created_at=r.get("created_at") or "",
            user_id=r.get("user_id") or "",
        )
        por_cr[cr_id] = et
        if job_id is not None and moment_index is not None:
            key = (job_id, int(moment_index))
            existing = por_momento.get(key)
            if existing is None or et.created_at > existing.created_at:
                por_momento[key] = et

    return por_cr, por_momento


def etiqueta_para(
    por_momento: dict[tuple[str, int], Etiqueta], job_id: str | None, moment_index: int | None,
) -> Etiqueta | None:
    """Atajo: etiqueta de un (job_id, moment_index), o None si no hay."""
    if job_id is None or moment_index is None:
        return None
    return por_momento.get((job_id, int(moment_index)))


if __name__ == "__main__":
    por_cr, por_momento = fetch_etiquetas()
    print(f"{len(por_cr)} content_result_id etiquetados ({len(por_momento)} momentos únicos)")
    by_job: dict[str, int] = {}
    for et in por_momento.values():
        by_job[et.job_id or "?"] = by_job.get(et.job_id or "?", 0) + 1
    for job_id, n in by_job.items():
        print(f"  job={job_id}: {n} momentos etiquetados")
