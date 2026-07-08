"""
Helpers centralizados para llamadas chat vía OpenRouter.

- build_chat_kwargs: temperature condicional, reasoning effort, max_tokens por tarea
- log_llm_usage: logging de response.usage (paso 0 del plan de optimización)
"""
from __future__ import annotations

import os
from typing import Any

from config.model_tiers import (
    get_max_tokens,
    get_reasoning_effort,
    get_temperature,
)


def build_chat_kwargs(
    task: str,
    model: str,
    messages: list[dict[str, Any]],
    *,
    response_format: dict[str, str] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """
    Construye kwargs para client.chat.completions.create().

    Gemini 3.x / GPT-5.x: omite temperature (default del proveedor) y añade
    reasoning.effort vía extra_body cuando aplica.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": get_max_tokens(task, model),
    }

    temp = get_temperature(task, model)
    if temp is not None:
        kwargs["temperature"] = temp

    if response_format:
        kwargs["response_format"] = response_format

    if timeout is not None:
        kwargs["timeout"] = timeout

    effort = get_reasoning_effort(task, model)
    if effort is not None:
        kwargs["extra_body"] = {"reasoning": {"effort": effort}}

    return kwargs


def log_llm_usage(
    task: str,
    model: str,
    response: Any,
    *,
    job_id: str | None = None,
    moment_index: int | None = None,
) -> None:
    """Log prompt/completion/total tokens cuando el proveedor devuelve usage."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return

    prompt_tokens = getattr(usage, "prompt_tokens", None) or 0
    completion_tokens = getattr(usage, "completion_tokens", None) or 0
    total_tokens = getattr(usage, "total_tokens", None) or 0

    parts = [f"LLM usage task={task} model={model}"]
    if job_id:
        parts.append(f"job={job_id[:8]}")
    if moment_index is not None:
        parts.append(f"m={moment_index}")
    parts.append(f"in={prompt_tokens} out={completion_tokens} total={total_tokens}")

    # OpenRouter puede incluir reasoning tokens en completion_details (opcional)
    details = getattr(usage, "completion_tokens_details", None)
    if details is not None:
        reasoning = getattr(details, "reasoning_tokens", None)
        if reasoning:
            parts.append(f"reasoning={reasoning}")

    if os.getenv("LOG_LLM_USAGE", "true").lower() not in ("0", "false", "no"):
        print(f"   📊 {' '.join(parts)}")

    try:
        from context.job_context import get_job_context
        from services.usage_tracker import record_llm_usage

        ctx = get_job_context()
        record_llm_usage(
            task,
            model,
            response,
            moment_index=moment_index if moment_index is not None else ctx.get("moment_index"),
            metadata={"job_id_short": (ctx.get("job_id") or job_id or "")[:8] or None},
        )
    except Exception:
        pass
