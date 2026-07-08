#!/usr/bin/env python3
"""
Invalida el analysis_cache de un video de YouTube para forzar re-análisis Gemini.

Uso local (desde worker/):
  python scripts/invalidate-analysis-cache.py KXKzgeHOr7A

En el VPS (contenedor Docker, WORKDIR=/app):
  docker compose -f docker-compose.worker.yml exec worker \
    python scripts/invalidate-analysis-cache.py KXKzgeHOr7A
"""
import sys
from pathlib import Path

# /app en Docker; worker/ en desarrollo local
APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

# .env local (en Docker las vars vienen de env_file del compose)
_env_file = APP_ROOT.parent / ".env"
if not _env_file.is_file():
    _env_file = APP_ROOT / ".env"
if _env_file.is_file():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_file)
    except ImportError:
        pass

from services.analysis_cache import delete_cached_analysis  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("Uso: python scripts/invalidate-analysis-cache.py <video_id>")
        print("Ejemplo: python scripts/invalidate-analysis-cache.py KXKzgeHOr7A")
        return 1
    video_id = sys.argv[1].strip()
    if not video_id:
        print("video_id vacio")
        return 1
    try:
        n = delete_cached_analysis(video_id)
    except Exception as e:
        print(f"Error al invalidar cache: {e}")
        return 1
    if n == 0:
        print(
            f"No se borro nada para {video_id} "
            "(ya vacio o faltan SUPABASE_URL / SUPABASE_SERVICE_KEY)"
        )
    else:
        print("Listo: re-procesa el video para obtener analisis fresco (sin cache hit).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
