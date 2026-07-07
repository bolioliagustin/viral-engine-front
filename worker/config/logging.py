"""
Worker logging — structured trace context + file persistence.

- All print() calls are routed through logging (console + rotating file).
- Context vars inject job_id / edit_id / moment / phase into every line.
- Logs persist on the host at ./worker-logs/worker.log (docker volume).
"""
from __future__ import annotations

import builtins
import logging
import os
import sys
from contextlib import contextmanager
from contextvars import ContextVar, Token
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterator, Optional

_trace_ctx: ContextVar[dict[str, Any]] = ContextVar("worker_trace", default={})
_print_patched = False
_logger: Optional[logging.Logger] = None


def get_trace_context() -> dict[str, Any]:
    return dict(_trace_ctx.get({}))


def bind_trace(**fields: Any) -> Token:
    current = dict(_trace_ctx.get({}))
    for key, value in fields.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    return _trace_ctx.set(current)


def reset_trace(token: Token) -> None:
    _trace_ctx.reset(token)


@contextmanager
def trace(**fields: Any) -> Iterator[None]:
    token = bind_trace(**fields)
    try:
        yield
    finally:
        reset_trace(token)


def set_phase(phase: str) -> None:
    bind_trace(phase=phase)


class TraceFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ctx = get_trace_context()
        tags: list[str] = []
        if ctx.get("job_id"):
            tags.append(f"job={_short(ctx['job_id'])}")
        if ctx.get("edit_id"):
            tags.append(f"edit={_short(ctx['edit_id'])}")
        if ctx.get("moment_index") is not None:
            tags.append(f"m={ctx['moment_index']}")
        if ctx.get("phase"):
            tags.append(f"phase={ctx['phase']}")
        record.trace = f"[{' '.join(tags)}] " if tags else ""
        return super().format(record)


def _short(value: Any, n: int = 8) -> str:
    s = str(value)
    return s if len(s) <= n else s[:n]


def _patch_print(logger: logging.Logger) -> None:
    global _print_patched
    if _print_patched:
        return

    original_print = builtins.print

    def patched_print(*args: Any, **kwargs: Any) -> None:
        if args or kwargs.get("end", "\n") != "\n":
            message = " ".join(str(a) for a in args)
            end = kwargs.get("end", "\n")
            if end and end != "\n":
                message += str(end)
            message = message.rstrip()
            if message:
                logger.info(message)
                return
        original_print(*args, **kwargs)

    builtins.print = patched_print
    _print_patched = True


def setup_logging() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger

    log_dir = Path(os.getenv("WORKER_LOG_DIR", Path(__file__).parent.parent / "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "worker.log"

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    use_json = os.getenv("LOG_FORMAT", "").lower() == "json"

    logger = logging.getLogger("worker")
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False

    if use_json:
        fmt = (
            '{"ts":"%(asctime)s","level":"%(levelname)s","trace":"%(trace)s",'
            '"msg":%(message)r}'
        )
        datefmt = "%Y-%m-%dT%H:%M:%S"
    else:
        fmt = "%(asctime)s %(levelname)-5s %(trace)s%(message)s"
        datefmt = "%Y-%m-%d %H:%M:%S"

    formatter = TraceFormatter(fmt, datefmt=datefmt)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=int(os.getenv("WORKER_LOG_MAX_BYTES", str(20 * 1024 * 1024))),
        backupCount=int(os.getenv("WORKER_LOG_BACKUP_COUNT", "5")),
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    error_file = log_dir / "worker-error.log"
    error_handler = RotatingFileHandler(
        error_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(formatter)
    logger.addHandler(error_handler)

    _patch_print(logger)
    _logger = logger

    logger.info(
        "Worker logging initialized "
        f"(file={log_file}, level={level_name}, json={use_json})"
    )
    return logger


def get_logger(name: str = "worker") -> logging.Logger:
    if _logger is None:
        setup_logging()
    return logging.getLogger(name)
