#!/usr/bin/env python3
"""
Purga las cachés de un video (W18): analysis_cache, category_cache,
transcription_cache (clave pelada y compuestas) y los archivos locales
downloads/<video_id>* (transcripts y audio). Imprime qué borró.

Usalo después de un fix de audio, transcript o idioma (PROYECTO.md §10.3).

Uso local (desde worker/):
  python scripts/purge-video-cache.py B60BHDNFNxM --dry-run
  python scripts/purge-video-cache.py B60BHDNFNxM

En el VPS (contenedor Docker, WORKDIR=/app):
  docker compose -f docker-compose.worker.yml exec worker \
    python scripts/purge-video-cache.py B60BHDNFNxM --dry-run
"""
import argparse
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

from services.cache_purge import purge_video_cache  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Purga las cachés de un video de YouTube.")
    parser.add_argument("video_id", help="id de YouTube (11 caracteres), p. ej. B60BHDNFNxM")
    parser.add_argument("--dry-run", action="store_true", help="lista lo que borraría, sin borrar")
    args = parser.parse_args(argv)

    video_id = args.video_id.strip()
    if not video_id:
        print("video_id vacío")
        return 1
    report = purge_video_cache(video_id, dry_run=args.dry_run)
    if report["errors"]:
        for err in report["errors"]:
            print(f"   ⚠️ {err}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
