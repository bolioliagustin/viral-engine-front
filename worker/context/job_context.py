"""
Thread-safe job context via contextvars.

Set at the start of _process_job_inner (or clip_edit_processor) and read
from usage_tracker / log_llm_usage without threading job_id through every call.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Optional

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
