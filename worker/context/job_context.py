"""
Thread-safe job context via contextvars.

Set at the start of _process_job_inner (or clip_edit_processor) and read
from usage_tracker / log_llm_usage without threading job_id through every call.
"""
from __future__ import annotations

import contextvars
from contextvars import ContextVar
from typing import Any, Callable, Optional, TypeVar

_job_id: ContextVar[Optional[str]] = ContextVar("usage_job_id", default=None)
_user_id: ContextVar[Optional[str]] = ContextVar("usage_user_id", default=None)
_moment_index: ContextVar[Optional[int]] = ContextVar("usage_moment_index", default=None)
_clip_edit_id: ContextVar[Optional[str]] = ContextVar("usage_clip_edit_id", default=None)


def set_job_context(
    *,
    job_id: str,
    user_id: str | None = None,
    clip_edit_id: str | None = None,
) -> None:
    _job_id.set(job_id)
    _user_id.set(user_id)
    _clip_edit_id.set(clip_edit_id)


def clear_job_context() -> None:
    _job_id.set(None)
    _user_id.set(None)
    _moment_index.set(None)
    _clip_edit_id.set(None)


def set_moment_index(moment_index: int | None) -> None:
    _moment_index.set(moment_index)


def get_job_context() -> dict[str, Any]:
    return {
        "job_id": _job_id.get(),
        "user_id": _user_id.get(),
        "moment_index": _moment_index.get(),
        "clip_edit_id": _clip_edit_id.get(),
    }


_T = TypeVar("_T")


def in_current_context(fn: Callable[..., _T]) -> Callable[..., _T]:
    """
    Envuelve `fn` para que, al correr en otro hilo, vea el contexto de job del
    hilo que llamó a este helper. Los hilos de un `ThreadPoolExecutor` arrancan
    con un contexto vacío, así que `usage_tracker` descartaría sus eventos
    (medido en W4: el rollup registró US$0.02 de los US$0.146 de Whisper de los
    tramos paralelos). No se puede reusar `Context.run` en varios hilos a la vez
    ("context is already entered"), por eso se copian los valores y se vuelven a
    setear dentro del hilo hijo.

        pool.map(in_current_context(_una_tarea), items)
    """
    captured = list(contextvars.copy_context().items())

    def _wrapped(*args, **kwargs) -> _T:
        for var, value in captured:
            var.set(value)
        return fn(*args, **kwargs)

    return _wrapped
