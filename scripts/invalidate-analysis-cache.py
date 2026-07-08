#!/usr/bin/env python3
"""Wrapper: delega a worker/scripts/invalidate-analysis-cache.py (incluido en la imagen Docker)."""
import runpy
import sys
from pathlib import Path

_worker_script = Path(__file__).resolve().parent.parent / "worker" / "scripts" / "invalidate-analysis-cache.py"
if not _worker_script.is_file():
    print(f"No se encontro {_worker_script}")
    sys.exit(1)
sys.argv[0] = str(_worker_script)
runpy.run_path(str(_worker_script), run_name="__main__")
