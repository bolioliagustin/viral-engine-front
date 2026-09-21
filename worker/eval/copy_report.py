"""
W13 (docs/PLAN_CALIDAD.md §9 W10; etiquetas del 21-sep-2026) — mide el copy
por clip (`title`/`description`) YA generado en `content_results`, sin
correr el pipeline ni gastar en LLM.

Motivo: Agustín rechazó clips publicables porque "los títulos y la
descripción no explican de qué hablan" (A-m11) / "las redacciones no son
buenas" (A-m8) — el patrón visible es "Tema: ¡La verdad sobre X!" repetido,
que no dice el dato concreto del clip. Este reporte cuantifica el problema
sobre corridas reales ya guardadas, ANTES de tocar el prompt.

Uso:
    python eval/copy_report.py 9e739c7b-f07b-4a5a-a9b3-54569372fd39 c7ea4108-2409-4e51-af40-f6c3c6137dbe
    python eval/copy_report.py 9e739c7b-f07b-4a5a-a9b3-54569372fd39 --json

Acepta el job_id completo o un prefijo (se resuelve contra `jobs.id`).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKER_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(WORKER_DIR.parent / ".env")
load_dotenv(WORKER_DIR / ".env")

from services.content_validators import (  # noqa: E402
    count_exclamations,
    title_has_forbidden_pattern,
    title_is_faithful,
)
from services.supabase_client import get_supabase  # noqa: E402


def _resolve_job_id(prefix: str) -> str:
    """Acepta un job_id completo o un prefijo — resuelve contra jobs.id."""
    if len(prefix) == 36 and prefix.count("-") == 4:
        return prefix
    sb = get_supabase()
    res = sb.table("jobs").select("id, created_at").order("created_at", desc=True).limit(500).execute()
    matches = [row["id"] for row in (res.data or []) if row["id"].startswith(prefix)]
    if not matches:
        raise ValueError(f"ningún job en los últimos 500 empieza con {prefix!r}")
    if len(matches) > 1:
        raise ValueError(f"{prefix!r} es ambiguo: matchea {matches}")
    return matches[0]


def clip_text_from_whisper_words(ww) -> str:
    """Reconstruye el texto real del clip desde whisper_words (segments ya
    puntuados; si no hay segments, cae a la lista cruda de words)."""
    if isinstance(ww, str):
        try:
            ww = json.loads(ww)
        except (json.JSONDecodeError, TypeError):
            return ""
    if not isinstance(ww, dict):
        return ""
    segments = ww.get("segments")
    if segments:
        return " ".join((s.get("text") or "").strip() for s in segments).strip()
    words = ww.get("words") or []
    return " ".join((w.get("word") or "") for w in words).strip()


def fetch_moments(job_id: str) -> list[dict]:
    """Una fila por moment_index — content_results tiene 3 filas por Momento
    (una por pieza de copy) con title/description/whisper_words repetidos."""
    sb = get_supabase()
    res = (
        sb.table("content_results")
        .select("job_id,moment_index,title,description,hashtags,whisper_words,hook")
        .eq("job_id", job_id)
        .order("moment_index")
        .execute()
    )
    by_moment: dict[int, dict] = {}
    for row in res.data or []:
        mi = row.get("moment_index")
        if mi is not None and mi not in by_moment:
            by_moment[mi] = row
    return [by_moment[k] for k in sorted(by_moment)]


def analyze_title(job_id: str, moment_index: int, title: str | None, description: str | None, clip_text: str) -> dict:
    title = title or ""
    formula = title_has_forbidden_pattern(title)
    exclam = count_exclamations(title)
    fiel = title_is_faithful(title, clip_text) if title and clip_text else False
    severity = (
        (2 if formula else 0)
        + (2 if title and clip_text and not fiel else 0)
        + (1 if exclam > 1 else 0)
        + (1 if len(title) > 60 else 0)
        + (1 if title and description and title.strip().lower() in (description or "").lower() else 0)
    )
    return {
        "job_id": job_id,
        "moment_index": moment_index,
        "title": title,
        "description": description or "",
        "len_title": len(title),
        "formula_prohibida": formula,
        "exclamaciones": exclam,
        "fiel_al_texto": fiel,
        "titulo_repetido_en_descripcion": bool(
            title and description and title.strip().lower() in (description or "").lower()
        ),
        "severity": severity,
    }


def build_report(job_ids: list[str]) -> dict:
    rows = []
    for job_id in job_ids:
        for m in fetch_moments(job_id):
            clip_text = clip_text_from_whisper_words(m.get("whisper_words"))
            rows.append(
                analyze_title(job_id, m.get("moment_index"), m.get("title"), m.get("description"), clip_text)
            )

    n = len(rows)

    def rate(pred) -> float | None:
        return round(sum(1 for r in rows if pred(r)) / n, 3) if n else None

    worst = sorted(rows, key=lambda r: -r["severity"])[:10]
    return {
        "job_ids": job_ids,
        "n_titulos": n,
        "pct_formula_prohibida": rate(lambda r: r["formula_prohibida"]),
        "pct_mas_de_un_signo_exclamacion": rate(lambda r: r["exclamaciones"] > 1),
        "largo_medio_titulo": round(sum(r["len_title"] for r in rows) / n, 1) if n else None,
        "pct_fiel_al_texto_del_clip": rate(lambda r: r["fiel_al_texto"]),
        "pct_titulo_repetido_en_descripcion": rate(lambda r: r["titulo_repetido_en_descripcion"]),
        "peores_10": worst,
        "todos": rows,
    }


def render_text(report: dict) -> str:
    lines = [
        f"Jobs: {', '.join(report['job_ids'])}  |  {report['n_titulos']} títulos",
        "",
        f"% con fórmula prohibida ('La verdad sobre', 'El peligro de', ...): {report['pct_formula_prohibida']:.1%}",
        f"% con más de un signo de exclamación: {report['pct_mas_de_un_signo_exclamacion']:.1%}",
        f"Largo medio del título: {report['largo_medio_titulo']} chars",
        f"% que comparte ≥1 palabra clave con el texto real del clip: {report['pct_fiel_al_texto_del_clip']:.1%}",
        f"% que repite el título casi textual en la descripción: {report['pct_titulo_repetido_en_descripcion']:.1%}",
        "",
        "Los 10 peores (formula_prohibida + no_fiel + exceso de '!' + >60 chars + título repetido en descripción):",
    ]
    for r in report["peores_10"]:
        flags = []
        if r["formula_prohibida"]:
            flags.append("fórmula")
        if not r["fiel_al_texto"]:
            flags.append("no_fiel")
        if r["exclamaciones"] > 1:
            flags.append(f"{r['exclamaciones']}x!")
        if r["len_title"] > 60:
            flags.append(">60chars")
        if r["titulo_repetido_en_descripcion"]:
            flags.append("desc=titulo")
        lines.append(f"  [{','.join(flags) or '—'}] {r['title']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reporte de calidad de títulos/descripciones ya generados (sin gastar en LLM)")
    parser.add_argument("job_ids", nargs="+", help="job_id completo o prefijo (se resuelve contra jobs.id)")
    parser.add_argument("--json", action="store_true", help="Salida JSON en vez de tabla")
    args = parser.parse_args(argv)

    # En modo --json, cualquier print() suelto (cliente de Supabase, etc. —
    # igual que eval/run_golden_set.py y eval/comparar_jueces.py) va a
    # stderr; el JSON final es lo único que sale por el stdout real.
    real_stdout = sys.stdout
    if args.json:
        sys.stdout = sys.stderr
    try:
        resolved = [_resolve_job_id(j) for j in args.job_ids]
        report = build_report(resolved)
    finally:
        sys.stdout = real_stdout

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2), file=real_stdout)
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
