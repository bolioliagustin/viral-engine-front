"""Tests for config/pricing.py"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestPricing:
    def test_gemini_flash_cost(self):
        from config.pricing import estimate_llm_cost_usd

        # 1M input @ $1.50 + 100k output @ $9 = $1.50 + $0.90 = $2.40
        cost = estimate_llm_cost_usd(
            "google/gemini-3.5-flash",
            input_tokens=1_000_000,
            output_tokens=100_000,
        )
        assert cost == pytest.approx(2.40, rel=1e-4)

    def test_reasoning_tokens_billed_as_output(self):
        from config.pricing import estimate_llm_cost_usd

        base = estimate_llm_cost_usd("openai/gpt-5.4-nano", 1000, 1000)
        with_reasoning = estimate_llm_cost_usd(
            "openai/gpt-5.4-nano", 1000, 1000, reasoning_tokens=500
        )
        assert with_reasoning > base

    def test_groq_whisper_minimum_10s(self):
        from config.pricing import estimate_whisper_cost_usd

        five_sec = estimate_whisper_cost_usd("groq", 5.0)
        ten_sec = estimate_whisper_cost_usd("groq", 10.0)
        assert five_sec == ten_sec
        assert ten_sec == round((10.0 / 3600.0) * 0.04, 6)

    def test_openai_whisper_per_minute(self):
        from config.pricing import estimate_whisper_cost_usd

        cost = estimate_whisper_cost_usd("openai", 60.0)
        assert cost == pytest.approx(0.006, rel=1e-4)

    @pytest.mark.parametrize("model,inp,out", [
        ("google/gemini-2.5-flash-lite", 0.10, 0.40),
        ("google/gemini-3-flash-preview", 0.50, 3.00),
        ("google/gemini-3.5-flash", 1.50, 9.00),
        ("google/gemini-3.1-pro-preview", 2.00, 12.00),
        ("openai/gpt-5.4-nano", 0.20, 1.25),
    ])
    def test_openrouter_models_have_pricing(self, model, inp, out):
        from config.pricing import lookup_model_pricing

        p = lookup_model_pricing(model)
        assert p.input_per_million == inp
        assert p.output_per_million == out

    def test_pricing_overrides_json(self, monkeypatch):
        monkeypatch.setenv(
            "PRICING_OVERRIDES_JSON",
            '{"llm": {"custom/model": {"input_per_million": 2.0, "output_per_million": 4.0}}}',
        )
        import importlib
        from config import pricing as pricing_mod
        importlib.reload(pricing_mod)

        cost = pricing_mod.estimate_llm_cost_usd("custom/model", 1_000_000, 0)
        assert cost == pytest.approx(2.0, rel=1e-4)

        importlib.reload(pricing_mod)
