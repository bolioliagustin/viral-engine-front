#!/usr/bin/env python3
"""
Invalida el analysis_cache de un video de YouTube para forzar re-análisis Gemini.

Uso (desde la raíz del repo, con .env del worker cargado):
  python scripts/invalidate-analysis-cache.py KXKzgeHOr7A

En el VPS (dentro del contenedor worker):
  docker compose -f docker-compose.worker.yml exec worker \
    python scripts/invalidate-analysis-cache.py KXKzgeHOr7A
"""
import sys
from pathlib import Path

# Permitir importar worker/services desde la raíz del repo
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "worker"))

# Cargar .env del worker si existe (VPS / local)
_env_file = ROOT / "worker" / ".env"
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
        print("video_id vacío")
        return 1
    try:
        n = delete_cached_analysis(video_id)
    except Exception as e:
        print(f"Error al invalidar cache: {e}")
        return 1
    if n == 0:
        print(f"No se borró nada para {video_id} (¿ya vacío o sin credenciales Supabase?)")
    else:
        print(f"Listo: re-procesá el video para obtener análisis fresco (sin cache hit).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
