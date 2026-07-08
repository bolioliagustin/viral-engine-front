# Recomendación de Modelos LLM — YouTube Viral Content Engine

**Fecha:** 8 de julio de 2026
**Documento complementario a:** `INFORME_LLMS.md`
**Criterio de optimización acordado:** 1) calidad de respuesta, 2) velocidad, 3) precio como restricción razonable (se acepta pagar más por mejor resultado, pero el pipeline debe seguir siendo rápido y eficiente).

---

## 1. Resumen ejecutivo

Tras un análisis del informe de modelos, del estado actual del mercado (Google, OpenAI, Anthropic, DeepSeek, Alibaba) y de benchmarks independientes de calidad y velocidad (Artificial Analysis, LLM Stats), la configuración recomendada es:

| Tarea | Modelo actual (default código) | Modelo recomendado | Motivo principal |
|---|---|---|---|
| Classifier | `gemini-2.0-flash-001` ⚠️ **apagado** | `google/gemini-2.5-flash-lite` | Fix urgente; menor latencia del mercado |
| Analysis (Pasada A) | `gemini-2.5-pro` | `google/gemini-3.5-flash` (thinking low) | Calidad frontier + 289 tok/s + precio razonable |
| Copy (Pasada B) | `gemini-2.5-flash` | A/B: `gemini-3.5-flash` (thinking minimal) vs `openai/gpt-5.4-mini` | Maximizar pass rate de validación |
| Judge | `gemini-2.5-flash-lite` | `openai/gpt-5.4-nano` | Juez independiente cross-family |
| STT per-clip | Groq `whisper-large-v3-turbo` | **Sin cambios** (+ fallback OpenAI) | Imbatible en precio, velocidad y word-timestamps |

**Costo estimado de IA por job:** USD 0.20–0.25 (vs ~0.15 de la config actual).
**Tiempo de IA por job:** ~80–95 segundos (el resto del tiempo es descarga/FFmpeg).
**Trade-off asumido:** ~50% más de costo de IA a cambio de saltar dos generaciones de modelo en la tarea crítica (Analysis), mayor velocidad de punta a punta y un juez genuinamente independiente. Alineado al criterio calidad-primero.

**Acción urgente independiente de todo lo demás:** verificar el `.env` del VPS hoy. El default del classifier apunta a un modelo apagado (sección 2).

---

## 2. ⚠️ Acción urgente: el default del Classifier está apagado

Google apagó **Gemini 2.0 Flash el 1 de junio de 2026** (Flash y Flash-Lite figuran como deprecados/apagados en la documentación oficial de pricing de Google). El default en `worker/config/model_tiers.py` es `gemini-2.0-flash-001`.

Consecuencia probable: si producción no pisa `MODEL_CLASSIFIER` en el `.env` del VPS, cada job está cayendo silenciosamente al fallback de keywords (sin error visible, porque el fallback existe justamente para eso). La clasificación por keywords es más pobre en edge cases y elige el prompt de la Pasada A.

**Checklist inmediato:**

- [ ] Leer `MODEL_CLASSIFIER` efectivo en el `.env` del VPS (o logs de arranque de `validate_env.py`).
- [ ] Revisar logs recientes: ¿hay excepciones de API en `get_video_category()` seguidas de fallback a keywords?
- [ ] Setear `MODEL_CLASSIFIER=google/gemini-2.5-flash-lite` (o el modelo que se decida) y actualizar el default en `model_tiers.py`.
- [ ] Invalidar `category_cache` si la clave incluye el modelo (la incluye: `video + modelo`), para regenerar con el modelo nuevo.

---

## 3. Contexto de mercado — julio 2026

### 3.1 Lineup Gemini vigente (lo que cambió desde el informe)

| Modelo | Precio in/out (por 1M tokens) | Estado | Nota |
|---|---|---|---|
| Gemini 3.5 Flash | $1.50 / $9 | GA (19 mayo 2026) | Gana a 3.1 Pro en coding/agentic; 289 tok/s |
| Gemini 3.1 Pro (preview) | $2 / $12 (>200K: $4/$18) | Preview | Mejor razonamiento puro; 2M contexto; ~134 tok/s |
| Gemini 3 Flash (preview) | $0.50 / $3 | Preview | Generación anterior de Flash |
| Gemini 3.1 Flash-Lite | $0.25 / $1.50 | GA (marzo 2026) | ~347 tok/s; supera a 2.5 Flash-Lite |
| Gemini 2.5 Pro | $1.25 / $10 | Legacy | Familia en retirada |
| Gemini 2.5 Flash / Flash-Lite | $0.30/$2.50 · $0.10/$0.40 | Legacy | Disponibles pero con vida útil limitada |
| Gemini 2.0 Flash / Flash-Lite | — | **Apagados** (1/6/2026) | Migrar de inmediato |
| **Gemini 3.5 Pro** | (no publicado) | **No lanzado** | Reportado para ~17 de julio; candidato futuro para Analysis |

Corrección a una premisa del informe: el `.env.example` sugiere `gemini-3.5-flash` como opción "más nueva/barata". Es más nueva pero **no** más barata: cuesta 5x el input de 2.5 Flash. La familia Flash subió de precio generación a generación ($0.30 → $0.50 → $1.50 de input).

### 3.2 Alternativas cross-provider evaluadas

| Proveedor | Modelo | Precio in/out | Velocidad | Evaluación para este pipeline |
|---|---|---|---|---|
| OpenAI | GPT-5.4 | $2.50 / $15 | Buena | Sin ventaja clara vs 3.5 Flash a mayor precio |
| OpenAI | GPT-5.4 mini | $0.75 / $4.50 | Buena | **Candidato Copy** — mejor precio/calidad de OpenAI |
| OpenAI | GPT-5.4 nano | $0.20 / $1.25 | Muy alta | **Recomendado Judge** — scoring de alto volumen |
| Anthropic | Sonnet 5 | $2 / $10 (intro hasta 31/8, luego $3/$15) | Buena | Calidad top; precio sube en septiembre |
| Anthropic | Haiku 4.5 | $1 / $5 | Alta | Candidato secundario Copy (formato estricto) |
| DeepSeek | V4-Pro | ~$0.44 / $0.87 | **~120 tok/s (lento)** | Calidad casi-frontier al mejor precio, pero la mitad de velocidad que 3.5 Flash → descartado como principal por el criterio de velocidad; queda como referencia de costo en el eval |
| DeepSeek | V4-Flash | $0.14 / $0.28 | ~180 tok/s | El 1M-contexto más barato del mercado; validar calidad de español antes de producción |
| Alibaba | Qwen 3.7 Max | $1.25 / $3.75 (promo 50%) | Media | Top-10 en calidad; precio promocional (lista $2.50/$7.50) → riesgo de suba |

**Referencias de benchmarks independientes (julio 2026):** Gemini 3.5 Flash es el modelo frontier más barato en relación precio/inteligencia entre los cerrados. DeepSeek V4 Pro puntúa en la misma liga que Gemini 3.1 Pro en el índice de Artificial Analysis a una fracción del precio, pero su velocidad de servicio (~120 tok/s vs 289 de 3.5 Flash) lo relega a workloads batch — no a un pipeline donde el usuario mira la barra de progreso.

**Nota operativa:** como todo el worker habla con OpenRouter, probar cualquiera de estos modelos es cambiar una variable de entorno. Costo de experimentación: cero código.

---

## 4. Recomendación detallada por tarea

### 4.1 Classifier — `MODEL_CLASSIFIER`

**Recomendado:** `google/gemini-2.5-flash-lite` ($0.10/$0.40). Alternativa futuro-proof: `google/gemini-3.1-flash-lite` ($0.25/$1.50).

Justificación por criterio: la tarea (título + 1500 chars → una palabra) no exige calidad frontier; 2.5 Flash-Lite está entre los modelos de menor latencia del mercado (TTFT ~0.3s); el costo es despreciable en ambas opciones (<$0.001/job).

Gotcha si se elige la familia 3.x: los modelos Gemini 3 razonan por defecto y los tokens de thinking consumen `max_tokens`. Con `max_tokens=5` la respuesta llega vacía. Setear `thinking_level: minimal` (API Gemini) o `reasoning: {enabled: false}` (OpenRouter).

Experimento pendiente del informe que sigue vigente: comparar contra "solo keywords" en el golden set. Si la accuracy es comparable, se elimina una dependencia.

### 4.2 Analysis (Pasada A) — `MODEL_ANALYSIS`

**Recomendado:** `google/gemini-3.5-flash` con `thinking_level: low` (subir a `medium` si el golden set muestra mejora en timestamps que lo justifique).

Justificación por criterio:

- **Calidad:** frontier-class; supera a Gemini 3.1 Pro en benchmarks de coding y agentic siendo una generación más nuevo que el 2.5 Pro actual. Es la tarea de mayor impacto del producto (timestamps malos = clips inutilizables): acá se justifica pagar el tier alto.
- **Velocidad:** 289 tok/s → ~30s para el output de la Pasada A, contra >60s de las alternativas económicas (DeepSeek ~120 tok/s). Es la llamada más larga del pipeline; su latencia define la percepción del job.
- **Precio:** ~$0.13–0.16/job con thinking low (35k tokens de input promedio con `COMPACT_TRANSCRIPT=true`). Vs ~$0.10 del 2.5 Pro actual: +40% en la tarea crítica, aceptable bajo el criterio acordado.

Plan de validación (sección 8): correr golden set con baseline 2.5-pro, 3.5-flash (low) y DeepSeek V4-Pro como referencia de costo. Métricas de corte: precisión de timestamps, tasa de `verification_failed`, recall de momentos, latencia.

**Calendario:** Gemini 3.5 Pro está reportado para ~17 de julio. Cuando salga, correrlo en el golden set como techo de calidad para este slot. Si mejora `verification_failed` de forma medible, evaluar upgrade (el costo extra en la tarea crítica está pre-aprobado por el criterio calidad-primero).

**No recomendado quedarse en `gemini-2.5-pro`** más allá de este trimestre: la familia 2.x ya fue apagada y la 2.5 es legacy; Google demostró que retira familias con poco margen de aviso.

### 4.3 Copy (Pasada B) — `MODEL_COPY_WRITING`

**Recomendado:** A/B con el golden set (`run_golden_set.py --copy`) entre:

1. `google/gemini-3.5-flash` con `thinking_level: minimal` (~$0.09/job) — hipótesis: máximo pass rate de validación (7 tweets exactos, rango LinkedIn) y unifica el stack en el mejor modelo. Cada retry evitado ahorra costo y ~6s de latencia, doble ganancia bajo el criterio.
2. `openai/gpt-5.4-mini` (~$0.045/job) — muy buen instruction-following, buen español.
3. `google/gemini-3-flash-preview` (~$0.03/job) — control económico de la generación 3.

Métricas de decisión, en orden: pass rate de validación sin retry → calidad percibida del copy en español (evaluación humana sobre 10-15 clips) → fidelidad al transcript (no inventar) → costo. El costo es marginal en las tres opciones; **la decisión es por calidad y pass rate, no por precio.**

Candidato suplente si ninguno convence en español: `anthropic/claude-haiku-4.5` ($1/$5), fuerte en cumplimiento de formato estricto.

### 4.4 Judge — `MODEL_JUDGE`

**Recomendado:** `openai/gpt-5.4-nano` ($0.20/$1.25, ~$0.01/job).

Justificación arquitectónica además de económica: hoy el juez es de la misma familia que el generador, y los LLM tienden a favorecer outputs de su propia familia — exactamente el sesgo de auto-evaluación que el juez existe para corregir. Un juez **cross-family** (OpenAI juzgando outputs de Gemini) mejora la independencia sin costo relevante. GPT-5.4 nano está posicionado específicamente para routing/scoring/extracción de alto volumen, con output de 400 tokens y latencia de ~1-2s por clip.

Validación: correr la correlación juez-vs-humano del golden set con el juez actual (2.5-flash-lite) y con gpt-5.4-nano. Mantener el que correlacione mejor. Nota: setear razonamiento en mínimo/off también acá (`max_tokens=400` + thinking = mismo problema del classifier).

### 4.5 STT — Whisper

**Sin cambios.** Groq `whisper-large-v3-turbo` a $0.04/hora (9x más barato que OpenAI a $0.36/hora), 216–228x tiempo real (un clip de 60s se transcribe en <1 segundo), con `verbose_json` y timestamps word-level — el requisito duro de todo el pipeline de verificación, subtítulos y refinado de cortes. Ninguna alternativa del mercado compite en la combinación precio + velocidad + word timestamps.

**Mantener el fallback dual-provider** a OpenAI `whisper-1`: ya está implementado, cuesta cero en complejidad y salva jobs si Groq se cae. Además sigue siendo obligatorio para el path chunked de audio >20 min. Dato menor: Groq factura mínimo 10 segundos por request — irrelevante para clips de 30–60s.

---

## 5. Costos y latencia estimados por job

Supuestos: video de 30–60 min, 5 clips, transcript compactado (~35k tokens de input en Analysis), happy path sin retries.

| Componente | Config actual (defaults) | Config recomendada |
|---|---|---|
| Classifier | ~$0.000 (roto/keywords) | <$0.001 · ~0.5s |
| Analysis | ~$0.10 · ~35s | ~$0.14 · ~30s |
| Copy ×5 | ~$0.023 · ~30s | $0.045–0.09 · ~30s |
| Judge ×5 | ~$0.003 · ~8s | ~$0.01 · ~8s |
| Whisper ×5 | ~$0.003 · ~5s | ~$0.003 · ~5s |
| **Total IA** | **~$0.13 · ~80s** | **~$0.20–0.25 · ~75–90s** |

Lectura: el upgrade cuesta ~$0.10 más por job y no empeora (levemente mejora) la latencia total, concentrando la inversión en la tarea que define la calidad del producto. A 1.000 jobs/mes, la diferencia es ~USD 100/mes.

Importante: estas cifras son estimaciones sobre precios de lista. Los thinking tokens de la generación 3.x se facturan como output y pueden desviar el costo real — por eso el paso 0 del plan (sección 8) es implementar el tracking de `response.usage`.

---

## 6. Alternativas evaluadas y descartadas

| Opción | Por qué se descarta (bajo el criterio calidad + velocidad + precio razonable) |
|---|---|
| DeepSeek V4-Pro como Analysis principal | Mejor precio absoluto (~$0.02/job) y calidad casi-frontier, pero ~120 tok/s duplica la latencia de la llamada más larga del pipeline. Apto para batch, no para UX con barra de progreso. Queda como referencia de costo en el eval. Si algún día se usa: vía OpenRouter se pueden fijar providers occidentales (Together/Fireworks) si importa dónde se procesa el dato; los endpoints legacy de la API directa (`deepseek-chat`/`deepseek-reasoner`) dejan de funcionar el 24/7/2026. |
| Gemini 3.1 Pro para Analysis | 3.5 Flash lo supera en los benchmarks relevantes siendo ~25% más barato y ~4x más rápido. Solo ganaría si el golden set mostrara ventaja en razonamiento sobre transcript largo — improbable pero verificable. |
| Qwen 3.7 Max | Buen puesto calidad/precio, pero el precio es promocional (50% off) → riesgo de duplicación sin aviso. |
| Claude Sonnet 5 | Calidad top para copy/analysis, pero el precio intro ($2/$10) vence el 31/8 y pasa a $3/$15; a igualdad de criterio, 3.5 Flash domina en velocidad y precio. Haiku 4.5 queda como suplente de Copy. |
| Modelos `:free` de OpenRouter | Rate limits y calidad inferior; el propio worker ya advierte al arranque. No aptos para producción. |
| Reemplazar Whisper (Chirp 3, gpt-4o-transcribe, etc.) | Ninguna alternativa iguala la combinación $0.04/hora + 216x tiempo real + timestamps word-level en `verbose_json`. |
| Eliminar el Judge | Se pierde la calibración de scores (el problema original de auto-evaluación 7–9). El ahorro es ~$0.01/job: no justifica. |

---

## 7. Gotchas técnicos de migración (revisar antes de tocar producción)

**7.1 Thinking tokens (familia Gemini 3.x y modelos razonadores de OpenAI).** Se facturan como output y consumen `max_tokens`. Impacto directo en el código actual:

- Classifier con `max_tokens=5` → respuesta vacía si el modelo razona. Fijar `thinking_level: minimal` / `reasoning off`.
- Judge con `max_tokens=400` → mismo riesgo. Ídem.
- Analysis con `max_tokens=8000` → subir el límite (p. ej. 16000) para acomodar thinking + output, o el JSON puede llegar truncado.
- Vía OpenRouter, el control es el parámetro `reasoning` (`effort` / `enabled: false`).

**7.2 Temperature.** Google recomienda explícitamente no fijar temperature en Gemini 3 y dejar el default de 1.0; valores bajos pueden causar loops o degradación en tareas complejas. `TASK_TEMPERATURES` (0.0 / 0.1 / 0.3 / 0.65) fue calibrado para la familia 2.x: al migrar, probar primero sin el parámetro y solo re-fijarlo si el golden set muestra inconsistencia estructural. Hacer la config de temperature condicional por familia de modelo en `model_tiers.py`.

**7.3 response_format / JSON.** Mantener `json_object` + json_repair como hoy. Si Copy queda en GPT-5.4 mini, considerar `json_schema` (structured outputs de OpenAI) que garantiza el schema y podría eliminar una clase entera de retries. Verificar soporte del parámetro vía OpenRouter para cada modelo elegido.

**7.4 Prompt caching.** Los system prompts de Analysis y Copy son prefijos fijos grandes. Las lecturas de cache cuestan ~10% del precio de input (Gemini y OpenRouter lo aplican automáticamente si el prefijo es estable). Estructurar los prompts con el contenido fijo primero y el transcript al final para maximizar hits.

**7.5 Versionado de modelos.** Pinnear versiones explícitas en los env vars. Evitar aliases tipo `gemini-flash-latest` (auto-upgrade silencioso = cambios de comportamiento sin aviso). Tras cada cambio de modelo en Analysis, bump de `PROMPT_VERSION` en `analysis_cache.py` (actual: v3) para invalidar cache.

---

## 8. Plan de validación (golden set, una variable por experimento)

Siguiendo el proceso definido en el informe original — no cambiar todo a la vez:

**Paso 0 — Prerrequisitos (antes de cualquier experimento):**

- [ ] Implementar logging de `response.usage` (tokens in/out/thinking por llamada) y minutos de Whisper → costo real por job. Sin esto, los experimentos comparan calidad a ciegas contra precios de lista.
- [ ] Documentar los modelos efectivos de producción (`.env` del VPS).
- [ ] Fix urgente del Classifier (sección 2).

**Paso 1 — Baseline:** `run_golden_set.py` y `run_golden_set.py --copy` con la config de producción actual. Guardar métricas: `verification_failed`, recall de momentos, pass rate de copy, delta `score_llm` vs `score_judge`, latencia por tarea, costo por job.

**Paso 2 — Experimento Analysis (el de mayor impacto):** solo cambiar `MODEL_ANALYSIS`.

| Variante | Aceptar si… |
|---|---|
| `gemini-3.5-flash` (thinking low) | `verification_failed` ≤ baseline y recall ≥ baseline |
| `deepseek/deepseek-v4` (referencia) | Solo informativo: cuantifica cuánta calidad compra el precio |
| `gemini-3.5-pro` (cuando salga, ~17/7) | Mejora medible en timestamps que justifique el costo extra |

**Paso 3 — Experimento Copy:** solo cambiar `MODEL_COPY_WRITING`. Variantes: 3.5-flash (minimal) / gpt-5.4-mini / 3-flash-preview. Decidir por pass rate sin retry → evaluación humana del español → fidelidad al audio.

**Paso 4 — Experimento Judge:** correr el scoring del golden set con juez actual y con `gpt-5.4-nano`. Mantener el de mayor correlación con evaluación humana. Verificar estabilidad JSON.

**Paso 5 — Cierre:** fijar config final, actualizar defaults en `model_tiers.py` y `.env.example` para alinear las tres capas de configuración (hoy divergen), bump de `PROMPT_VERSION`, documentar en `WORKER.md`.

---

## 9. Variables de entorno propuestas

```bash
# --- Configuración recomendada (post-validación en golden set) ---

# Classifier: fix urgente (default actual apunta a modelo apagado)
MODEL_CLASSIFIER=google/gemini-2.5-flash-lite

# Analysis (Pasada A): tarea crítica, calidad frontier + velocidad
MODEL_ANALYSIS=google/gemini-3.5-flash
# → configurar thinking_level=low / reasoning effort low vía OpenRouter
# → subir max_tokens de 8000 a 16000 (thinking cuenta contra el límite)
# → re-evaluar con google/gemini-3.5-pro cuando esté disponible (~17/7)

# Copy (Pasada B): ganador del A/B del golden set --copy
MODEL_COPY_WRITING=google/gemini-3.5-flash    # o openai/gpt-5.4-mini
# → thinking_level=minimal

# Judge: cross-family para independencia real del scoring
MODEL_JUDGE=openai/gpt-5.4-nano
# → reasoning off / minimal (max_tokens=400)

# STT: sin cambios
# GROQ_API_KEY=...   (whisper-large-v3-turbo, preferido)
# OPENAI_API_KEY=... (whisper-1, fallback + audio >20min)
```

---

## Anexo — Fuentes consultadas (julio 2026)

Precios y disponibilidad: documentación oficial de pricing de Google AI (ai.google.dev), OpenAI (developers.openai.com), Anthropic (platform.claude.com), Groq (groq.com/pricing), páginas de modelos de OpenRouter (openrouter.ai). Benchmarks de calidad y velocidad: Artificial Analysis (artificialanalysis.ai — Intelligence Index, output speed, time-to-first-token), LLM Stats. Estado de Gemini 3.5 Pro: al 8/7/2026 no figura en la API pública de Google (solo `gemini-3.5-flash` y `gemini-3.1-pro-preview`); el lanzamiento reportado por prensa (~17/7) no está confirmado oficialmente. Los precios citados son de lista, sin descuentos de batch (−50%) ni prompt caching (lecturas ~10% del input), que aplican como optimización adicional.

*Este documento asume el pipeline descrito en `INFORME_LLMS.md` (dos pasadas, ~17 llamadas de IA por job). Cualquier cambio de arquitectura invalida las estimaciones de costo/latencia.*
