"""
Exportador del Registro de candidatos (W30, docs/ESTUDIO_ML_EVALUADOR.md §8.2).

Lee en **solo lectura** `candidate_evals` (una fila por Candidato evaluado en
un job) y le pega las etiquetas que existan:

- **Referencias** (`eval/referencias/<youtube_id>.json`): el Candidato "toca"
  una Referencia si cubre al menos `TOCA_REFERENCIA` de su Núcleo (mismo
  criterio que `experimentos/ml_factibilidad.py`). Se reportan por separado
  las validadas por un humano y los borradores, y si el tramo cae en una
  zona excluida (publicidad).
- **Posteable** (`clip_feedback`, vía `content_results(job_id, moment_index)`):
  solo los elegidos tienen clip, así que los descartados quedan en null.

Sale un JSONL versionado (`ESQUEMA`, una fila por Candidato) con los rasgos
que ya usa `ml_factibilidad.py` (`juez_suma`, `w2_score`, `rank_score`,
`pos_rel`, `duracion`) más los de Jev y los flags, listo para evaluar por
video (dejando videos afuera). Costo US$0: no llama a ninguna API paga y no
escribe en Supabase.

Uso (desde worker/):
    python eval/exportar_candidatos.py                      # → eval/datasets/candidatos-<fecha>.jsonl
    python eval/exportar_candidatos.py --video B60BHDNFNxM --salida /tmp/b60.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

WORKER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKER_DIR))

from eval.eval_metrics import cobertura_nucleo  # noqa: E402
from eval.referencias import cargar_referencias, momentos_validados  # noqa: E402

ESQUEMA = 1
TABLA = "candidate_evals"
TOCA_REFERENCIA = 0.5   # cobertura del Núcleo para decir "el Candidato toca la Referencia"
EXCLUSION_MIN = 0.5     # fracción del tramo dentro de una zona excluida
PAGINA = 1000           # PostgREST devuelve de a 1000 filas
DATASETS_DIR = WORKER_DIR / "eval" / "datasets"


# ─── Armado (puro) ───────────────────────────────────────────────────────────

def _f(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _tramo(c: dict) -> tuple[float, float] | None:
    """Tramo que se evaluó: el final (anclado) si existe, si no el propuesto."""
    ini, fin = _f(c.get("final_start")), _f(c.get("final_end"))
    if ini is None or fin is None:
        ini, fin = _f(c.get("proposed_start")), _f(c.get("proposed_end"))
    if ini is None or fin is None or fin <= ini:
        return None
    return ini, fin


def _suma(*vals) -> float | None:
    nums = [_f(v) for v in vals]
    return None if any(n is None for n in nums) else round(sum(nums), 3)


def ultima_fila_por_candidato(filas: Iterable[dict]) -> list[dict]:
    """Si un job se reprocesó (reintento de la cola), se queda con la fila más
    reciente de cada (job_id, candidate_index)."""
    ultima: dict[tuple, dict] = {}
    for r in filas:
        k = (r.get("job_id"), r.get("candidate_index"))
        if k not in ultima or (r.get("created_at") or "") >= (ultima[k].get("created_at") or ""):
            ultima[k] = r
    return sorted(ultima.values(), key=lambda r: (r.get("created_at") or "", r.get("job_id") or "",
                                                  r.get("candidate_index") or 0))


def etiquetas_de_referencias(tramo: tuple[float, float] | None, doc: dict | None) -> dict:
    """Etiquetas del Candidato contra las Referencias de su video (None si no hay archivo)."""
    if doc is None:
        return {"hay_referencias": False, "toca_referencia": None, "referencia_ids": [],
                "referencia_calidad": None, "toca_borrador": None, "en_exclusion": None}
    if tramo is None:
        return {"hay_referencias": True, "toca_referencia": False, "referencia_ids": [],
                "referencia_calidad": None, "toca_borrador": False, "en_exclusion": False}
    c = {"start_time": tramo[0], "end_time": tramo[1]}
    validadas = [m for m in momentos_validados(doc) if cobertura_nucleo(c, m) >= TOCA_REFERENCIA]
    todas = [m for m in momentos_validados(doc, incluir_borradores=True)
             if cobertura_nucleo(c, m) >= TOCA_REFERENCIA]
    largo = tramo[1] - tramo[0]
    en_excl = any(
        max(0.0, min(tramo[1], float(e["fin"])) - max(tramo[0], float(e["inicio"]))) > EXCLUSION_MIN * largo
        for e in doc.get("excluir") or []
    )
    calidades = sorted({m.get("calidad") for m in validadas if m.get("calidad")})
    return {
        "hay_referencias": True,
        "toca_referencia": bool(validadas),
        "referencia_ids": [m["id"] for m in validadas],
        "referencia_calidad": calidades[0] if calidades else None,  # "A" gana a "B"
        "toca_borrador": bool(todas),
        "en_exclusion": en_excl,
    }


def armar_fila(c: dict, doc_ref: dict | None, etiqueta) -> dict:
    """Una línea del JSONL: identificación, rasgos y etiquetas del Candidato.
    `etiqueta` es la `eval.etiquetas.Etiqueta` vigente del clip (o None)."""
    tramo = _tramo(c)
    dur_video = _f(c.get("video_duration"))
    flags = c.get("flags") or {}
    if isinstance(flags, str):
        flags = json.loads(flags)
    return {
        "esquema": ESQUEMA,
        "id": c.get("id"),
        "job_id": c.get("job_id"),
        "video": c.get("video_id"),
        "candidate_index": c.get("candidate_index"),
        "delivery_index": c.get("delivery_index"),
        "created_at": c.get("created_at"),
        "prompt_version": c.get("prompt_version"),
        "ranker": c.get("ranker"),
        "modelos": {k: c.get(f"{k}_model") for k in ("analysis", "judge", "jev")},
        "category": c.get("category"),
        "formato": c.get("formato"),
        "proposed_start": _f(c.get("proposed_start")),
        "proposed_end": _f(c.get("proposed_end")),
        "final_start": _f(c.get("final_start")),
        "final_end": _f(c.get("final_end")),
        "hook": c.get("hook"),
        "lines_text": c.get("lines_text"),
        "clip_text": c.get("clip_text"),
        # Rasgos (mismos nombres que ml_factibilidad.py)
        "juez_suma": _suma(c.get("judge_hook"), c.get("judge_retention"), c.get("judge_shareability")),
        "judge_hook": _f(c.get("judge_hook")),
        "judge_retention": _f(c.get("judge_retention")),
        "judge_shareability": _f(c.get("judge_shareability")),
        "judge_reasoning": c.get("judge_reasoning"),
        "jev_rank_score": _f(c.get("jev_rank_score")),
        "jev_hook": _f(c.get("jev_hook")),
        "jev_retention": _f(c.get("jev_retention")),
        "jev_shareability": _f(c.get("jev_shareability")),
        "jev_confidence": _f(c.get("jev_confidence")),
        "rank_score": _f(c.get("rank_score")),
        "self_score": _f(c.get("self_score")),
        "w2_score": _f(c.get("w2_score")),
        "pos_rel": round(tramo[0] / dur_video, 4) if tramo and dur_video else None,
        "duracion": round(tramo[1] - tramo[0], 3) if tramo else None,
        "usable": c.get("usable"),
        "flags": flags,
        "selected": c.get("selected"),
        "discard_reason": c.get("discard_reason"),
        # Etiquetas
        **etiquetas_de_referencias(tramo, doc_ref),
        "posteable": etiqueta.posteable if etiqueta else None,
        "motivo": etiqueta.motivo if etiqueta else None,
    }


def armar_dataset(
    candidatos: Iterable[dict],
    etiquetas_por_momento: dict,
    cargar_ref: Callable[[str], dict | None] = cargar_referencias,
) -> list[dict]:
    """Filas del JSONL. `etiquetas_por_momento` es el segundo valor de
    `eval.etiquetas.fetch_etiquetas()`: {(job_id, moment_index): Etiqueta}."""
    refs_cache: dict[str, dict | None] = {}
    salida = []
    for c in ultima_fila_por_candidato(candidatos):
        vid = c.get("video_id")
        if vid not in refs_cache:
            refs_cache[vid] = cargar_ref(vid) if vid else None
        et = None
        if c.get("delivery_index") is not None:
            et = etiquetas_por_momento.get((c.get("job_id"), int(c["delivery_index"])))
        salida.append(armar_fila(c, refs_cache[vid], et))
    return salida


def escribir_jsonl(filas: list[dict], ruta: Path) -> Path:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        for r in filas:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return ruta


def leer_jsonl(ruta: Path) -> list[dict]:
    with open(ruta, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


# ─── Lectura de la base (solo SELECT) ────────────────────────────────────────

def leer_candidatos(sb, video: str | None = None) -> list[dict]:
    """Todas las filas de `candidate_evals` (paginado). Solo lectura."""
    filas: list[dict] = []
    desde = 0
    while True:
        q = sb.table(TABLA).select("*")
        if video:
            q = q.eq("video_id", video)
        page = q.order("created_at").range(desde, desde + PAGINA - 1).execute().data or []
        filas.extend(page)
        if len(page) < PAGINA:
            return filas
        desde += PAGINA


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", help="solo este youtube_id")
    ap.add_argument("--salida", type=Path,
                    help=f"ruta del JSONL (default: {DATASETS_DIR.relative_to(WORKER_DIR)}/candidatos-<fecha>.jsonl)")
    args = ap.parse_args(argv)

    from eval.etiquetas import fetch_etiquetas
    from services.supabase_client import get_supabase

    sb = get_supabase()
    if not sb:
        print("⚠️ Sin Supabase (SUPABASE_URL / SUPABASE_SERVICE_KEY): no hay de dónde leer", file=sys.stderr)
        return 1
    try:
        candidatos = leer_candidatos(sb, args.video)
    except Exception as e:
        print(f"⚠️ No se pudo leer `{TABLA}` (¿migración sin aplicar?): {str(e)[:200]}", file=sys.stderr)
        candidatos = []
    _, por_momento = fetch_etiquetas()
    filas = armar_dataset(candidatos, por_momento, cargar_ref=cargar_referencias)

    ruta = args.salida or DATASETS_DIR / f"candidatos-{date.today().isoformat()}.jsonl"
    escribir_jsonl(filas, ruta)
    videos = {r["video"] for r in filas}
    print(
        f"✅ {len(filas)} candidatos de {len(videos)} videos → {ruta}\n"
        f"   con Referencias: {sum(1 for r in filas if r['hay_referencias'])} "
        f"(tocan una validada: {sum(1 for r in filas if r['toca_referencia'])}) · "
        f"con Posteable: {sum(1 for r in filas if r['posteable'] is not None)} · "
        f"con Jev: {sum(1 for r in filas if r['jev_rank_score'] is not None)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
