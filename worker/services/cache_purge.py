"""
Purga de las cachés de un video (W18, docs/briefs/W18-cache-integra.md).

Borra todo lo que el worker reutiliza de una corrida anterior del mismo video:
  - Supabase: `analysis_cache` y `category_cache` (todas las filas del video),
    `transcription_cache` con la clave pelada (captions) y las compuestas
    (`<id>:whisper_full:<modelo>`, `<id>:hybrid:…`).
  - Archivos locales `downloads/<video_id>*`: transcripts, audio y restos de
    descargas. En el VPS `downloads/` es el volumen `worker_downloads`, que
    sobrevive a los deploys.

La usan `scripts/purge-video-cache.py` (a mano, después de un fix de audio,
transcript o idioma) y el Cortacircuitos de `main.py` (CONTEXT.md), antes de
rehacer el transcript.
"""
import shutil
from pathlib import Path

from services.supabase_client import get_supabase

DOWNLOADS_DIR = Path(__file__).parent.parent / "downloads"


def _supabase_targets(video_id: str) -> list[tuple[str, str, str]]:
    """(tabla, operador, valor) de cada filtro a purgar."""
    return [
        ("analysis_cache", "eq", video_id),
        ("category_cache", "eq", video_id),
        ("transcription_cache", "eq", video_id),
        ("transcription_cache", "like", f"{video_id}:%"),
    ]


def _query(table, op: str, value: str):
    return table.eq("video_id", value) if op == "eq" else table.like("video_id", value)


def local_files(video_id: str, downloads_dir: Path | None = None) -> list[Path]:
    """Archivos y carpetas locales del video (`<id>*` y `.<id>_*` de las descargas en paralelo)."""
    base = downloads_dir or DOWNLOADS_DIR
    if not base.is_dir():
        return []
    found = set(base.glob(f"{video_id}*")) | set(base.glob(f".{video_id}_*"))
    return sorted(found)


def purge_video_cache(
    video_id: str,
    *,
    dry_run: bool = False,
    downloads_dir: Path | None = None,
    include_supabase: bool = True,
) -> dict:
    """
    Borra (o con `dry_run` solo lista) las cachés del video. Nunca lanza: un
    error de Supabase queda en `errors` y la purga sigue con lo demás.

    `include_supabase=False` purga solo los archivos locales: lo usa el
    Cortacircuitos cuando el job corre en dry-run (medición del golden set),
    que nunca escribe cachés de producción (PLAN_MEJORA §4.1).

    Returns {"supabase": {tabla: [claves]}, "files": [rutas], "errors": [...],
    "dry_run": bool}.
    """
    video_id = (video_id or "").strip()
    report: dict = {"supabase": {}, "files": [], "errors": [], "dry_run": dry_run}
    if not video_id:
        report["errors"].append("video_id vacío")
        return report
    verb = "borraría" if dry_run else "borrado"

    supabase = get_supabase() if include_supabase else None
    if not include_supabase:
        print("   ℹ️ Purga solo local: Supabase queda sin tocar")
    elif not supabase:
        report["errors"].append("Supabase no configurado (faltan SUPABASE_URL / SUPABASE_SERVICE_KEY)")
    else:
        for table_name, op, value in _supabase_targets(video_id):
            try:
                res = _query(supabase.table(table_name).select("*"), op, value).execute()
                rows = res.data or []
                if not rows:
                    continue
                if not dry_run:
                    _query(supabase.table(table_name).delete(), op, value).execute()
                keys = []
                for r in rows:
                    extra = [str(r[k]) for k in ("model", "tone", "prompt_version") if r.get(k)]
                    keys.append(f"{r.get('video_id')}" + (f" ({', '.join(extra)})" if extra else ""))
                report["supabase"].setdefault(table_name, []).extend(keys)
                for k in keys:
                    print(f"   🗑️ {table_name}: {k} — {verb}")
            except Exception as e:
                report["errors"].append(f"{table_name} ({op} {value}): {e}")
                print(f"   ⚠️ Purga de {table_name} falló ({op} {value}): {e}")

    for path in local_files(video_id, downloads_dir):
        try:
            if not dry_run:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
            report["files"].append(str(path))
            print(f"   🗑️ archivo local {path.name} — {verb}")
        except Exception as e:
            report["errors"].append(f"{path}: {e}")
            print(f"   ⚠️ No se pudo borrar {path}: {e}")

    n_rows = sum(len(v) for v in report["supabase"].values())
    print(
        f"🧹 Purga de {video_id}{' (dry-run)' if dry_run else ''}: "
        f"{n_rows} fila(s) de Supabase y {len(report['files'])} archivo(s) local(es) {verb}"
        + (f"; {len(report['errors'])} error(es)" if report["errors"] else "")
    )
    return report
