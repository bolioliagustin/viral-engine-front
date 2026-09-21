"""
W14 (docs/PLAN_CALIDAD.md): seam de progreso para que `downloader.py` y
`transcriber.py` reporten avance sin conocer Supabase ni `job_id`.

`main.py` es el único lugar que sabe qué job está corriendo y cómo escribir
en la base (`update_job_progress`): registra un hook con `set_progress_hook`
al arrancar el job. El resto del pipeline llama a `report(...)` sin importar
nada de acá abajo; si nadie registró un hook (tests, `eval/`, tier e2e),
`report` es no-op.
"""
from typing import Callable, Optional

ProgressHook = Callable[[str, int, int, str], None]

_hook: Optional[ProgressHook] = None

# El contrato de progreso de P1 (docs/PLAN_CALIDAD.md §9 W9,
# `main.py::compute_progress_percentage`) reserva 0-15 de
# `progress_percentage` para la fase "transcribing". Dentro de esa franja,
# la descarga del audio ocupa la primera mitad y la transcripción por
# tramos la segunda, para que el progreso nunca retroceda al pasar de una
# subfase a la otra.
_TRANSCRIBING_SUBSTEPS: dict[str, tuple[float, float]] = {
    "download_audio": (0.0, 0.5),
    "transcribe_tramos": (0.5, 1.0),
}
_TRANSCRIBING_FLOOR_PCT = 0
_TRANSCRIBING_CEIL_PCT = 15


def set_progress_hook(fn: Optional[ProgressHook]) -> None:
    """`main.py` la llama al arrancar (y al terminar, con `None`) el job."""
    global _hook
    _hook = fn


def report(step: str, current: int, total: int, message: str) -> None:
    """
    Reporta avance de un paso (`step` identifica la subfase, p. ej.
    "download_audio" o "transcribe_tramos"). No-op si nadie registró un
    hook — nunca lanza, para no romper la descarga/transcripción por un
    fallo al reportar.
    """
    if _hook is None:
        return
    try:
        _hook(step, current, total, message)
    except Exception:
        pass


def transcribing_percentage(step: str, current: int, total: int) -> int:
    """
    Mapea `(step, current, total)` a un entero 0-15 — el rango que el
    contrato de P1 reservó para la fase "transcribing". `step` fuera de
    `_TRANSCRIBING_SUBSTEPS` ocupa la franja entera (0.0, 1.0).
    """
    lo, hi = _TRANSCRIBING_SUBSTEPS.get(step, (0.0, 1.0))
    frac = (current / total) if total > 0 else 0.0
    frac = min(1.0, max(0.0, frac))
    overall = lo + frac * (hi - lo)
    span = _TRANSCRIBING_CEIL_PCT - _TRANSCRIBING_FLOOR_PCT
    return round(_TRANSCRIBING_FLOOR_PCT + overall * span)
