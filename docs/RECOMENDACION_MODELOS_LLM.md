# Recomendación de Modelos LLM — YouTube Viral Content Engine

**Fecha:** 8 de julio de 2026  
**Documento complementario a:** [`INFORME_LLMS.md`](INFORME_LLMS.md)  
**Criterio de optimización acordado:** 1) calidad de respuesta, 2) velocidad, 3) precio como restricción razonable (se acepta pagar más por mejor resultado, pero el pipeline debe seguir siendo rápido y eficiente).

**Estado de implementación en código (jul 2026):** defaults en `worker/config/model_tiers.py`, helpers en `worker/config/llm_chat.py`, `PROMPT_VERSION=v4`. Validación con golden set pendiente antes de considerar cerrada la migración en producción.

---

## 1. Resumen ejecutivo

Tras un análisis del informe de modelos, del estado actual del mercado (Google, OpenAI, Anthropic, DeepSeek, Alibaba) y de benchmarks independientes de calidad y velocidad (Artificial Analysis, LLM Stats), la configuración recomendada es:

| Tarea | Modelo anterior (default código) | Modelo recomendado | Motivo principal |
|-------|----------------------------------|--------------------|------------------|
| Classifier | `gemini-2.0-flash-001` (apagado) | `google/gemini-2.5-flash-lite` | Fix urgente; menor latencia del mercado |
| Analysis (Pasada A) | `gemini-2.5-pro` | `google/gemini-3.5-flash` (thinking low) | Calidad frontier + 289 tok/s + precio razonable |
| Copy (Pasada B) | `gemini-2.5-flash` | A/B: `gemini-3.5-flash` (thinking minimal) vs `openai/gpt-5.4-mini` | Maximizar pass rate de validación |
| Judge | `gemini-2.5-flash-lite` | `openai/gpt-5.4-nano` | Juez independiente cross-family |
| STT per-clip | Groq `whisper-large-v3-turbo` | **Sin cambios** (+ fallback OpenAI) | Imbatible en precio, velocidad y word-timestamps |

**Costo estimado de IA por job:** USD 0.20–0.25 (vs ~0.15 de la config anterior).  
**Tiempo de IA por job:** ~80–95 segundos (el resto del tiempo es descarga/FFmpeg).  
**Trade-off asumido:** ~50% más de costo de IA a cambio de saltar dos generaciones de modelo en la tarea crítica (Analysis), mayor velocidad de punta a punta y un juez genuinamente independiente.

**Acción urgente independiente de todo lo demás:** verificar el `.env` del VPS. El default anterior del classifier apuntaba a un modelo apagado (sección 2).

---

## 2. Acción urgente: el default del Classifier estaba apagado

Google apagó **Gemini 2.0 Flash el 1 de junio de 2026**. El default anterior en `model_tiers.py` era `gemini-2.0-flash-001`.

Consecuencia probable: si producción no pisaba `MODEL_CLASSIFIER` en el `.env` del VPS, cada job caía al fallback de keywords (sin error visible).

**Checklist inmediato:**

- [ ] Leer `MODEL_CLASSIFIER` efectivo en el `.env` del VPS (o logs de arranque de `validate_env.py`).
- [ ] Revisar logs recientes: excepciones de API en `get_video_category()` seguidas de fallback a keywords.
- [x] Default en código actualizado a `google/gemini-2.5-flash-lite`.
- [ ] Invalidar `category_cache` si se cambió el modelo (clave: video + modelo).

---

## 3. Contexto de mercado — julio 2026

### 3.1 Lineup Gemini vigente

| Modelo | Precio in/out (por 1M tokens) | Estado | Nota |
|--------|-------------------------------|--------|------|
| Gemini 3.5 Flash | $1.50 / $9 | GA (19 mayo 2026) | 289 tok/s |
| Gemini 3.1 Pro (preview) | $2 / $12 | Preview | Mejor razonamiento; ~134 tok/s |
| Gemini 3.1 Flash-Lite | $0.25 / $1.50 | GA | ~347 tok/s |
| Gemini 2.5 Pro | $1.25 / $10 | Legacy | En retirada |
| Gemini 2.0 Flash / Flash-Lite | — | **Apagados** (1/6/2026) | Migrar de inmediato |
| Gemini 3.5 Pro | (no publicado) | Pendiente ~17/7 | Candidato futuro Analysis |

### 3.2 Alternativas cross-provider evaluadas

| Proveedor | Modelo | Evaluación para este pipeline |
|-----------|--------|-------------------------------|
| OpenAI | GPT-5.4 mini | Candidato Copy — instruction-following |
| OpenAI | GPT-5.4 nano | **Recomendado Judge** — scoring alto volumen |
| Anthropic | Haiku 4.5 | Suplente Copy (formato estricto) |
| DeepSeek | V4-Pro | Referencia de costo; descartado como principal por velocidad (~120 tok/s) |

---

## 4. Recomendación detallada por tarea

Ver [`INFORME_LLMS.md`](INFORME_LLMS.md) sección 3 para fichas técnicas completas.

### 4.1 Classifier

**Recomendado:** `google/gemini-2.5-flash-lite`. Gotcha Gemini 3.x: `max_tokens=5` + thinking = respuesta vacía; no aplica a 2.5-flash-lite.

### 4.2 Analysis (Pasada A)

**Recomendado:** `google/gemini-3.5-flash` con `reasoning.effort: low`. `max_tokens` elevado a 16000 (thinking consume el límite).

### 4.3 Copy (Pasada B)

**Recomendado:** A/B en golden set (`run_golden_set.py --copy`) entre `gemini-3.5-flash` (minimal), `gpt-5.4-mini`, `gemini-3-flash-preview`.

### 4.4 Judge

**Recomendado:** `openai/gpt-5.4-nano` — juez cross-family. `reasoning.effort: none`.

### 4.5 STT — Whisper

**Sin cambios.** Groq preferido + fallback OpenAI.

---

## 5. Costos y latencia estimados por job

| Componente | Config anterior | Config recomendada |
|------------|-----------------|-------------------|
| Classifier | ~$0 (keywords) | <$0.001 |
| Analysis | ~$0.10 | ~$0.14 |
| Copy ×5 | ~$0.023 | $0.045–0.09 |
| Judge ×5 | ~$0.003 | ~$0.01 |
| Whisper ×5 | ~$0.003 | ~$0.003 |
| **Total IA** | **~$0.13** | **~$0.20–0.25** |

---

## 6. Gotchas técnicos de migración

1. **Thinking tokens** — configurados vía `extra_body.reasoning` en `llm_chat.py`.
2. **Temperature** — omitida para Gemini 3.x en `get_temperature()`.
3. **max_tokens** — Analysis unificado a 16000.
4. **PROMPT_VERSION** — bump a `v4` en `analysis_cache.py`.
5. **Logging** — `log_llm_usage()` en cada llamada chat (`LOG_LLM_USAGE`).

---

## 7. Plan de validación (golden set)

**Paso 0:** logging de usage (implementado), documentar `.env` VPS, fix classifier (implementado en código).

**Paso 1:** baseline con `run_golden_set.py` y `--copy`.

**Pasos 2–4:** experimentos Analysis, Copy, Judge (una variable por vez).

**Paso 5:** fijar config final tras golden set.

---

## 8. Variables de entorno propuestas

```bash
MODEL_CLASSIFIER=google/gemini-2.5-flash-lite
MODEL_ANALYSIS=google/gemini-3.5-flash
MODEL_COPY_WRITING=google/gemini-3.5-flash
MODEL_JUDGE=openai/gpt-5.4-nano
# MODEL_ANALYSIS_REASONING=low
# MODEL_COPY_REASONING=minimal
# MODEL_JUDGE_REASONING=none
# GROQ_API_KEY=...  (whisper-large-v3-turbo)
# OPENAI_API_KEY=... (whisper-1 fallback)
```

---

*Este documento asume el pipeline descrito en `INFORME_LLMS.md`. Cualquier cambio de arquitectura invalida las estimaciones.*
