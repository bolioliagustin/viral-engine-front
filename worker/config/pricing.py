"""
Estimación de costo USD para LLM (OpenRouter) y Whisper (Groq/OpenAI).

Precios LLM alineados a OpenRouter (jul 2026). Override opcional vía
PRICING_OVERRIDES_JSON sin redeploy.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

_WARNED_UNKNOWN: set[str] = set()


@dataclass(frozen=True)
class ModelPricing:
    input_per_million: float
    output_per_million: float


# LLM — USD por 1M tokens (OpenRouter, jul 2026)
_DEFAULT_LLM_PRICING: dict[str, ModelPricing] = {
    # En producción (defaults model_tiers.py)
    "google/gemini-2.5-flash-lite": ModelPricing(0.10, 0.40),
    "google/gemini-3.5-flash": ModelPricing(1.50, 9.00),
    "openai/gpt-5.4-nano": ModelPricing(0.20, 1.25),
    # Alternativas / legacy env
    "google/gemini-3-flash-preview": ModelPricing(0.50, 3.00),
    "google/gemini-3.1-pro-preview": ModelPricing(2.00, 12.00),
    # Modelos anteriores (mantener para jobs viejos / A/B)
    "google/gemini-2.5-flash": ModelPricing(0.30, 2.50),
    "google/gemini-2.5-pro": ModelPricing(1.25, 10.00),
    "openai/gpt-5.4-mini": ModelPricing(0.75, 4.50),
}

# Whisper — valores especiales (no token-based)
_WHISPER_GROQ_PER_HOUR = 0.04
_WHISPER_GROQ_MIN_SECONDS = 10.0
_WHISPER_OPENAI_PER_MINUTE = 0.006

# Fallback genérico si el modelo no está en la tabla
_GENERIC_LLM = ModelPricing(1.00, 5.00)

_overrides_cache: dict[str, Any] | None = None


def _load_overrides() -> dict[str, Any]:
    global _overrides_cache
    if _overrides_cache is not None:
        return _overrides_cache
    raw = os.getenv("PRICING_OVERRIDES_JSON", "").strip()
    if not raw:
        _overrides_cache = {}
        return _overrides_cache
    try:
        _overrides_cache = json.loads(raw)
    except json.JSONDecodeError:
        print("⚠️ PRICING_OVERRIDES_JSON inválido — ignorando overrides")
        _overrides_cache = {}
    return _overrides_cache


def lookup_model_pricing(model: str) -> ModelPricing:
    """Devuelve pricing para un modelo; warning una vez si es desconocido."""
    overrides = _load_overrides().get("llm", {})
    if model in overrides:
        o = overrides[model]
        return ModelPricing(
            float(o.get("input_per_million", o.get("input", 1.0))),
            float(o.get("output_per_million", o.get("output", 5.0))),
        )
    if model in _DEFAULT_LLM_PRICING:
        return _DEFAULT_LLM_PRICING[model]
    if model not in _WARNED_UNKNOWN:
        _WARNED_UNKNOWN.add(model)
        print(f"⚠️ pricing: modelo desconocido '{model}' — usando fallback genérico")
    return _GENERIC_LLM


def estimate_llm_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int = 0,
) -> float:
    """Costo USD estimado. Reasoning tokens se facturan como output."""
    p = lookup_model_pricing(model)
    out_total = output_tokens + reasoning_tokens
    cost = (
        (input_tokens / 1_000_000) * p.input_per_million
        + (out_total / 1_000_000) * p.output_per_million
    )
    return round(cost, 6)


def estimate_whisper_cost_usd(provider: str, audio_seconds: float) -> float:
    """
    Groq: $0.04/hora, mínimo 10s de facturación por request.
    OpenAI: $0.006/minuto.
    """
    overrides = _load_overrides().get("whisper", {})
    provider = (provider or "").lower()
    seconds = max(0.0, float(audio_seconds or 0))

    if provider == "groq":
        min_s = float(overrides.get("groq_min_seconds", _WHISPER_GROQ_MIN_SECONDS))
        per_hour = float(overrides.get("groq_per_hour", _WHISPER_GROQ_PER_HOUR))
        billed = max(seconds, min_s)
        return round((billed / 3600.0) * per_hour, 6)

    per_minute = float(overrides.get("openai_per_minute", _WHISPER_OPENAI_PER_MINUTE))
    return round((seconds / 60.0) * per_minute, 6)


def estimate_avoided_llm_cost_usd(
    model: str,
    typical_input_tokens: int = 8000,
    typical_output_tokens: int = 2000,
) -> float:
    """Estimación de costo evitado en cache hit de análisis (para metadata)."""
    return estimate_llm_cost_usd(model, typical_input_tokens, typical_output_tokens)
