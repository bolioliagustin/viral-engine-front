"""
Persistencia de eventos de uso (LLM / Whisper) por job en Supabase.

Errores de persistencia son non-fatal: log warning, el job continúa.
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional

from config.pricing import (
    estimate_avoided_llm_cost_usd,
    estimate_llm_cost_usd,
    estimate_whisper_cost_usd,
)
from context.job_context import get_job_context


def _should_persist() -> bool:
    return os.getenv("PERSIST_USAGE_EVENTS", "true").lower() not in (
        "0",
        "false",
        "no",
    )


# Rollup en memoria durante el job (evita re-query al finalizar)
_job_rollups: dict[str, dict[str, Any]] = {}


def _get_rollup(job_id: str) -> dict[str, Any]:
    if job_id not in _job_rollups:
        _job_rollups[job_id] = {
            "total_cost_usd": 0.0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "reasoning_tokens": 0,
            "whisper_seconds": 0.0,
            "whisper_provider": None,
            "event_count": 0,
            "cache_hits": 0,
            "cost_avoided_usd": 0.0,
            "by_task": {},
            "by_model": {},
            "by_provider": {},
        }
    return _job_rollups[job_id]


def _update_rollup(job_id: str, event: dict[str, Any]) -> None:
    r = _get_rollup(job_id)
    cost = float(event.get("estimated_cost_usd") or 0)
    r["total_cost_usd"] = round(r["total_cost_usd"] + cost, 6)
    r["total_input_tokens"] += int(event.get("input_tokens") or 0)
    r["total_output_tokens"] += int(event.get("output_tokens") or 0)
    r["reasoning_tokens"] += int(event.get("reasoning_tokens") or 0)
    r["event_count"] += 1

    if event.get("cache_hit"):
        r["cache_hits"] += 1
        avoided = event.get("metadata", {}).get("cost_avoided_usd")
        if avoided:
            r["cost_avoided_usd"] = round(r["cost_avoided_usd"] + float(avoided), 6)

    task = event.get("task") or "unknown"
    r["by_task"][task] = round(r["by_task"].get(task, 0.0) + cost, 6)

    model = event.get("model")
    if model:
        r["by_model"][model] = round(r["by_model"].get(model, 0.0) + cost, 6)

    provider = event.get("provider")
    if provider:
        r["by_provider"][provider] = round(
            r["by_provider"].get(provider, 0.0) + cost, 6
        )

    if task == "whisper" and event.get("audio_seconds"):
        secs = float(event["audio_seconds"])
        r["whisper_seconds"] = round(r["whisper_seconds"] + secs, 3)
        if provider and not event.get("cache_hit"):
            r["whisper_provider"] = provider


def _insert_event(event: dict[str, Any]) -> None:
    if not _should_persist():
        return
    job_id = event.get("job_id")
    if not job_id:
        return

    _update_rollup(job_id, event)

    try:
        from services.supabase_client import get_supabase

        sb = get_supabase()
        if not sb:
            return
        sb.table("job_usage_events").insert(event).execute()
    except Exception as e:
        print(f"⚠️ usage_tracker: no se pudo persistir evento ({e})")


def _base_event(
    *,
    task: str,
    provider: str,
    model: str | None = None,
    moment_index: int | None = None,
    cache_hit: bool = False,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    ctx = get_job_context()
    job_id = ctx.get("job_id")
    if not job_id:
        return None

    mi = moment_index if moment_index is not None else ctx.get("moment_index")
    meta = dict(metadata or {})
    if ctx.get("clip_edit_id"):
        meta["clip_edit_id"] = ctx["clip_edit_id"]

    return {
        "job_id": job_id,
        "user_id": ctx.get("user_id"),
        "event_type": "llm_chat" if task != "whisper" else "whisper",
        "provider": provider,
        "task": task,
        "model": model,
        "moment_index": mi,
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "audio_seconds": None,
        "estimated_cost_usd": 0,
        "cache_hit": cache_hit,
        "latency_ms": None,
        "metadata": meta,
    }


def record_llm_usage(
    task: str,
    model: str,
    response: Any,
    *,
    moment_index: int | None = None,
    cache_hit: bool = False,
    latency_ms: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Registra una llamada LLM con tokens y costo estimado."""
    usage = getattr(response, "usage", None)
    if usage is None and not cache_hit:
        return

    prompt_tokens = getattr(usage, "prompt_tokens", None) or 0 if usage else 0
    completion_tokens = getattr(usage, "completion_tokens", None) or 0 if usage else 0
    reasoning_tokens = 0
    if usage:
        details = getattr(usage, "completion_tokens_details", None)
        if details is not None:
            reasoning_tokens = getattr(details, "reasoning_tokens", None) or 0

    cost = 0.0 if cache_hit else estimate_llm_cost_usd(
        model, prompt_tokens, completion_tokens, reasoning_tokens
    )

    event = _base_event(
        task=task,
        provider="openrouter",
        model=model,
        moment_index=moment_index,
        cache_hit=cache_hit,
        metadata=metadata,
    )
    if not event:
        return

    event["input_tokens"] = prompt_tokens
    event["output_tokens"] = completion_tokens
    event["reasoning_tokens"] = reasoning_tokens
    event["estimated_cost_usd"] = cost
    if latency_ms is not None:
        event["latency_ms"] = latency_ms

    finish = getattr(response, "choices", None)
    if finish and len(finish) > 0:
        fr = getattr(finish[0], "finish_reason", None)
        if fr:
            event["metadata"]["finish_reason"] = fr

    _insert_event(event)


def record_whisper_usage(
    provider: str,
    model: str,
    audio_seconds: float,
    *,
    moment_index: int | None = None,
    cache_hit: bool = False,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Registra transcripción Whisper con duración y costo estimado."""
    cost = 0.0 if cache_hit else estimate_whisper_cost_usd(provider, audio_seconds)

    event = _base_event(
        task="whisper",
        provider=provider,
        model=model,
        moment_index=moment_index,
        cache_hit=cache_hit,
        metadata=metadata,
    )
    if not event:
        return

    event["audio_seconds"] = round(float(audio_seconds or 0), 3)
    event["estimated_cost_usd"] = cost
    _insert_event(event)


def record_cache_hit(
    task: str,
    *,
    model: str | None = None,
    moment_index: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Evento de cache hit con costo 0 y cost_avoided_usd en metadata."""
    meta = dict(metadata or {})
    if model and "cost_avoided_usd" not in meta:
        meta["cost_avoided_usd"] = estimate_avoided_llm_cost_usd(model)

    event = _base_event(
        task=task,
        provider="cache",
        model=model,
        moment_index=moment_index,
        cache_hit=True,
        metadata=meta,
    )
    if not event:
        return

    event["estimated_cost_usd"] = 0
    _insert_event(event)


def finalize_job_usage(job_id: str) -> None:
    """Escribe rollup en jobs.usage_summary y limpia acumulador en memoria."""
    if not job_id:
        return

    rollup = _job_rollups.pop(job_id, None)
    if not rollup or rollup.get("event_count", 0) == 0:
        return

    rollup["total_cost_usd"] = round(rollup["total_cost_usd"], 6)

    if not _should_persist():
        return

    try:
        from services.supabase_client import get_supabase

        sb = get_supabase()
        if not sb:
            return
        sb.table("jobs").update({"usage_summary": rollup}).eq("id", job_id).execute()
        print(
            f"   📊 usage_summary: ${rollup['total_cost_usd']:.4f} "
            f"({rollup['event_count']} eventos)"
        )
    except Exception as e:
        print(f"⚠️ usage_tracker: no se pudo guardar usage_summary ({e})")
