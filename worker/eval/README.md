# Plan de evaluación IA — YouTube Viral Content Engine

Documento operativo para regresión de calidad LLM. Complementa [`docs/INFORME_LLMS.md`](../../docs/INFORME_LLMS.md) y [`docs/RECOMENDACION_MODELOS_LLM.md`](../../docs/RECOMENDACION_MODELOS_LLM.md).

> **Canvas `costos-y-llms`:** no está versionado en el repo; los costos estimados por job están en `RECOMENDACION_MODELOS_LLM.md` §5.

---

## Objetivo

Medir lo que **importa al producto**, no lo que el modelo promete en el prompt:

| Fase real del job | Qué validamos |
|-------------------|---------------|
| Clasificador | `category_accuracy` |
| Pasada A | momentos válidos, duración, solapamiento, **phrase anchors** |
| Pasada B (tier full) | validadores de copy (`copy_clean_rate`) |
| Juez (tier full) | respuesta JSON + calibración vs `score_llm` |
| Producción clip (tier e2e) | pipeline real por clip: juez del clip final, flags, densidad, cortes, costo, tiempo |

La métrica **`phrase_anchor_pass_rate`** reemplaza el antiguo `verification_pass_rate` estricto: verifica que las palabras clave de `first/last_phrase_in_audio` aparecen **en orden** dentro del slice del transcript, sin exigir match literal al primer token (Gemini suele citar frases reales pero desplazadas).

`verification_strict_pass_rate` queda como métrica **informativa** (umbral `null` por defecto).

---

## Tiers

| Tier | Duración | Videos | Copy+Juez | Cuándo usar |
|------|----------|--------|-----------|-------------|
| **smoke** | ~2-4 min | 1 (`claude_hacks_regression_01`) | No | Antes de cada deploy |
| **analysis** | ~10-20 min | 4 enabled | No | CI / cambio de `MODEL_ANALYSIS` |
| **full** | ~30-60 min | 4 enabled | Sí | Cambio de copy, juez, o release |
| **e2e** | ~15-30 min | 4 enabled (el de 108 min al final) | Sí, sobre el clip real | Antes y después de **cualquier** cambio del pipeline de IA (regla de oro de `docs/PLAN_CALIDAD.md` §5) |

### Comandos

**Local (repo root):**
```bash
python worker/eval/run_golden_set.py --tier smoke
python worker/eval/run_golden_set.py --tier analysis
python worker/eval/run_golden_set.py --tier full
```

**VPS (Docker):**
```bash
docker compose -f docker-compose.worker.yml stop worker

docker compose -f docker-compose.worker.yml exec worker \
  python eval/run_golden_set.py --tier smoke

docker compose -f docker-compose.worker.yml exec worker \
  python eval/run_golden_set.py --tier analysis

# JSON válido: logs en stderr, JSON en stdout
docker compose -f docker-compose.worker.yml exec worker \
  python eval/run_golden_set.py --tier full --json 2>/tmp/golden.log > /tmp/golden.json

docker compose -f docker-compose.worker.yml start worker
```

**Un solo video:**
```bash
python eval/run_golden_set.py --tier analysis --video claude_hacks_regression_01
```

**Tier e2e (desde `worker/`, en la Mac con `ENVIRONMENT=development`):**
```bash
EVAL_DRY_RUN=1 ENVIRONMENT=development python eval/run_golden_set.py --tier e2e --json \
  2>eval/runs/$(date +%F)-v4.log >eval/runs/$(date +%F)-v4.json

# Comparar contra el baseline
python eval/compare_runs.py eval/runs/2026-09-17-v4-baseline.json eval/runs/$(date +%F)-v4.json
```

---

## Tier `e2e`: cómo funciona

Ejecuta **`main.process_job`** (el mismo código que corre el worker de la cola)
por cada video habilitado con `"e2e"` en `tiers`, con `EVAL_DRY_RUN=1`:

- El pipeline corre entero: transcript, Pasada A (normalmente cache hit),
  filtros, descarga, pre-corte, Whisper por clip, snap/refinamiento, ancla de
  hook, verificación, Pasada B, juez y render FFmpeg.
- **No persiste nada**: `update_job_*` y `save_content_result` acumulan en
  memoria (`services.supabase_client.DRY_RUN_JOBS` / `DRY_RUN_RESULTS`),
  `upload_clip_to_storage` / `upload_raw_clip_to_storage` devuelven
  `dryrun://<job>/<n>.mp4`, `deduct_credit` no descuenta y `usage_tracker` no
  inserta en `job_usage_events` (pero mantiene el rollup → `DRY_RUN_ROLLUPS`).
  Las caches (`analysis_cache`, `category_cache`, `transcription_cache`)
  se leen y escriben como siempre.
- Cada video tiene un **presupuesto de reloj** (`--video-budget-sec`, default
  25 min): si se agota, el video queda `timed_out` y se sigue con el resto.
- Sentry queda apagado durante el eval (`SENTRY_DSN_WORKER=""` si no está
  seteado) y los logs del worker van a stderr en modo `--json`.

Salida por clip (`results[].clips[]`): `video_id`, `moment_index`,
`start_time`/`end_time` elegidos, `duration_chosen_sec` → `duration_final_sec`,
`snap_trim_start`, `first_words` (10) / `last_words` (8) Whisper,
`starts_capitalized`, `words_per_sec`, `density_out_of_range`, `sub_coverage`,
`verification_failed`, `clip_quality_issues`, `score_llm`, `score_judge`
(con `reasoning`), `judge_all_ge7`, `hook`, `viral_overlay`, `clip_rendered`.
Por video: `cost_usd` (rollup), `cost_by_task`, `elapsed_sec`, `status`,
`errors`. Agregados: ver tabla abajo. El JSON lleva además `models`,
`prompt_version`, `git_commit`, `run_at`.

Costo típico de una corrida con las 4 análisis cacheadas: < US$0.50 (Whisper
Groq + Pasada B + juez por clip). Corre en la Mac (IP residencial, yt-dlp sin
proxies); en el VPS hay que parar el worker de la cola primero.

### `compare_runs.py`

```bash
python eval/compare_runs.py baseline.json nuevo.json          # tabla legible
python eval/compare_runs.py baseline.json nuevo.json --json   # para scripts
```

Imprime, por métrica agregada, A / B / delta con ▲ (mejora) o ▼ (empeora)
según la dirección de cada métrica (`eval_metrics.E2E_METRIC_DIRECTIONS`), y
el juez clip por clip cuando `(video_id, moment_index)` existe en ambas
corridas. Si cambió la Pasada A (`PROMPT_VERSION`), el momento N puede ser
otro fragmento: la comparación por clip es orientativa; la agregada es la
que vale.

### Historial

Cada corrida se guarda en `eval/runs/<fecha>-<PROMPT_VERSION>[-nota].json`
y se anota en `eval/runs/README.md` (el `.log` no se versiona: `*.log` está en `.gitignore`).

---

## Plan de trabajo (roadmap)

### Fase 1 — Hecho (este PR)

- [x] Tiers smoke / analysis / full con umbrales distintos
- [x] Métrica `phrase_anchor_pass_rate` alineada al producto
- [x] `--json` con stdout limpio (logs → stderr)
- [x] `copy_clean_rate`, `judge_response_rate` en tier full
- [x] Tag `tiers` por video en `golden_set.json`

### Fase 2 — Próximo (manual + datos)

- [ ] Completar slots pendientes en `golden_set.json` (business ES, EN, largo >1h)
- [ ] Correr baseline con config actual y guardar `/tmp/golden_baseline_analysis.json`
- [ ] Ajustar umbrales tras 2-3 runs estables (no bloquear CI con métricas informativas)

### Fase 3 — Experiments (una variable por vez)

Ver `RECOMENDACION_MODELOS_LLM.md` §8:

1. Solo `MODEL_ANALYSIS` → comparar `phrase_anchor` y `duration_pass`
2. Solo `MODEL_COPY_WRITING` → `copy_clean_rate` en tier full
3. Solo `MODEL_JUDGE` → `judge_response_rate` y delta

Registrar en cada run: `models` del summary JSON + fecha.

### Fase 4 — Tier `e2e` (hecho, sep 2026 — W0 de `docs/PLAN_CALIDAD.md`)

- [x] `--tier e2e` con `EVAL_DRY_RUN=1` (pipeline real, sin persistir)
- [x] Métricas por clip y agregadas (juez, flags, densidad, cortes, costo, tiempo)
- [x] `compare_runs.py` y carpeta `eval/runs/` con el baseline
- [x] `analysis_cache` guarda `candidates_all` (todos los candidatos de la Pasada A con `rank_score`)
- [x] Etiquetas humanas "posteable" por clip (W7, tabla `clip_feedback`) leídas por `eval/etiquetas.py`

### Fase 4.5 — `posteable` como métrica principal (hecho, W12, 21-sep-2026)

El juez resultó casi ciego al criterio real del usuario (ver
`docs/PLAN_CALIDAD.md` §5 para los números). Desde W12:

- [x] `eval/etiquetas.py`: lee `clip_feedback`, resuelve `(job_id, moment_index)` → última etiqueta por usuario. Sin Supabase/tabla/filas → vacío, no rompe nada.
- [x] `eval_metrics.aggregate_e2e_results` calcula, **solo sobre clips con etiqueta**: `posteable_rate` (métrica principal del tier), `motivos_rechazo`, `judge_posteable_avg`/`judge_no_posteable_avg`/`judge_gap`, `judge_humano_corr`, `precision_at_3/5/10`.
- [x] `golden_set.json` (tier e2e): `thresholds.posteable_rate_min = 0.70`; un video puede declarar `real_job_id` (uuid de un job real ya etiquetado) para que `run_golden_set.py` le pegue las etiquetas reales por `moment_index`. Hoy ningún video del golden set lo declara, así que `posteable_rate` da `null` con `posteable_labeled_n=0` — correcto: el tier e2e corre en dry-run y nunca llega a un usuario real, no puede autoetiquetarse.
- [x] `compare_runs.py`: nuevas métricas en la tabla (dirección correcta: posteable ↑, motivos de rechazo ↓) y tabla aparte de `motivos_rechazo`; marca **"sin datos"** (no `0`) cuando una corrida no tiene etiquetas.
- [x] `eval/calibracion.py`: compara cualquier rankeador (Juez, Jev, o futuro) contra las etiquetas reales — ver sección propia más abajo.

### Fase 5 — CI

```yaml
# Ejemplo GitHub Actions (manual hasta tener secrets en CI)
- run: python worker/eval/run_golden_set.py --tier smoke --json
  env:
    OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
    SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
    SUPABASE_SERVICE_KEY: ${{ secrets.SUPABASE_SERVICE_KEY }}
```

---

## Interpretación de métricas

| Métrica | Umbral típico | Significado |
|---------|---------------|-------------|
| `category_accuracy` | ≥75% | Clasificador podcast/business |
| `duration_pass_rate` | ≥85% | Momentos dentro de `CLIP_MIN/MAX_DURATION_SEC` (15-120 s desde W2-B) tras validadores |
| `phrase_anchor_pass_rate` | ≥55% | Frases citadas existen en el clip (fuzzy) |
| `verification_strict_pass_rate` | null | Match estricto inicio/fin (suele ser bajo) |
| `copy_clean_rate` | ≥85% | Momentos sin problemas de validación de copy |
| `judge_llm_delta_avg` | ≤4.0 | Delta sano con juez cross-family (GPT vs Gemini) |
| `judge_response_rate` | ≥90% | Juez devuelve JSON válido |

### Métricas del tier `e2e`

| Métrica | Umbral (objetivo; `thresholds_blocking: false` → no bloquea) | Significado |
|---------|--------------------------|-------------|
| `judge_avg` (y `judge_hook/retention/shareability_avg`) | ≥ 7.0 | Juez promedio sobre el clip final |
| `judge_all_ge7_rate` | ≥ 60% | Clips con juez ≥7 en las tres métricas |
| `verification_failed_rate` | ≤ 15% | Frases citadas no coinciden con el audio, cola incompleta o hook tardío |
| `late_hook_rate` | — | El snap recortó >3 s al inicio |
| `whisper_mismatch_last_rate` | — | La última frase elegida no está en el clip (remate afuera) |
| `capitalized_start_rate` | ≥ 90% | Primera palabra Whisper en mayúscula (arranca en inicio de oración) |
| `density_out_of_range_rate` | 0% | Clips con < 1.2 o > 5 palabras/s (sin habla o timestamps rotos) |
| `duration_chosen_avg` → `duration_final_avg` | — | Cuánto recorta el refinamiento |
| `clips_rendered_rate` | — | Clips con MP4 (no deep-link de YouTube) |
| `total_cost_usd` / `total_seconds` | — | Costo (rollup de `usage_tracker`) y tiempo de reloj |

### Métricas `posteable` (W12 — solo sobre clips con etiqueta humana)

| Métrica | Umbral | Significado |
|---------|--------|-------------|
| `posteable_labeled_n` | — | Cuántos clips de la corrida tienen etiqueta; con pocos, el resto de esta tabla no significa nada |
| `posteable_rate` | ≥ 0.70 — **métrica principal del tier** | Fracción de los clips etiquetados que el usuario publicaría tal cual |
| `motivos_rechazo` | — (menos es mejor) | Conteo por motivo de rechazo (`arranca_mal`, `termina_mal`, `momento_flojo`, `subtitulos_mal`, `se_ve_mal`, `copy_malo`, `otro`) — dice **dónde** está el problema |
| `judge_posteable_avg` / `judge_no_posteable_avg` / `judge_gap` | informativo | Promedio del juez en cada grupo y su diferencia; si el gap no crece, el juez sigue sin servir |
| `judge_humano_corr` | informativo | Correlación punto-biserial entre el puntaje del juez y `posteable` |
| `precision_at_3` / `_5` / `_10` | informativo (mide al **rankeador**, no al juez en sí) | De los k mejores según el ranking, cuántos son posteables |

Hallazgo que motivó este cambio (21-sep-2026, 20 clips reales etiquetados de
dos jobs sobre el mismo video, uno rankeado con el Juez y otro con Jev):
`judge_gap = +0.29` sobre 30, `judge_humano_corr = 0.06` — el juez no
discrimina lo que el usuario publicaría. Sí hay señal en el extremo superior:
`precision@3 = precision@5 = 1.0`, cayendo a `0.83` en `@6` y `0.70` en
`@10`. Detalle completo reproducible con `eval/calibracion.py` (ver abajo) y
en `eval/runs/2026-09-21-calibracion-posteable.json`.

### `eval/calibracion.py` — decidir entre rankeadores

Herramienta para responder "¿este rankeador (Juez con tal modelo, Jev, lo que
sea) sirve para predecir qué publicaría el usuario?" usando las etiquetas
reales de `clip_feedback`, sin correr el pipeline:

```bash
cd worker
python eval/calibracion.py --job <job_id_1> --job <job_id_2> --k 3 5 10 --json
```

Reporta, sobre los clips etiquetados de los jobs dados: `posteable_rate`,
promedio de puntaje en posteables vs no posteables y el gap, correlación
punto-biserial, `precision_at_k` para cada `k` pedido, y los peores **falsos
negativos** (posteable=sí, puntaje bajo) y **falsos positivos** (posteable=no,
puntaje alto) con el texto reconstruido del clip para poder leerlos. Es
genérica por diseño: `--score-field` elige qué campo del clip usar como
puntaje (`score_judge_sum` por defecto), así que el mismo cálculo aplica a
cualquier rankeador futuro sin tocar el script.

Limitación conocida: el puntaje crudo de Jev por candidato **no se persiste**
fuera de la corrida en memoria (`main.py` solo guarda `judge_scores`,
`w2_score` y `w2_selected`/`w2_discard_reason` en `candidates_all`), así que
hoy `calibracion.py` solo puede evaluar retroactivamente al Juez sobre jobs
históricos; evaluar Jev "de verdad" requeriría correr el pipeline de nuevo o
tocar `main.py` para persistir su score — ninguna de las dos entra en W12.

---

## Archivos

| Archivo | Rol |
|---------|-----|
| `golden_set.json` | Videos, tiers, umbrales (incluye `real_job_id` opcional por video) |
| `run_golden_set.py` | CLI (todos los tiers, incluido `e2e`) |
| `eval_metrics.py` | Agregación y chequeo de umbrales; registro por clip del tier e2e; métricas `posteable` (W12) |
| `compare_runs.py` | Delta entre dos corridas e2e, incluidas las métricas `posteable` y `motivos_rechazo` |
| `etiquetas.py` | Lee `clip_feedback` (posteable/motivo) por `content_result_id` y por `(job_id, moment_index)` (W12) |
| `calibracion.py` | Compara cualquier rankeador contra las etiquetas reales: precision@k, correlación, peores errores (W12) |
| `runs/` | Corridas e2e versionadas + `README.md` con el historial |
| `services/validation.py` | `phrase_anchor_in_clip`, `evaluate_moment_phrase_metrics` |

---

*Última actualización: septiembre 2026 (W12 — `posteable` como métrica principal del tier e2e)*
