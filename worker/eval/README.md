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
| Producción clip | *futuro tier `e2e`* — Whisper + snap + MP4 |

La métrica **`phrase_anchor_pass_rate`** reemplaza el antiguo `verification_pass_rate` estricto: verifica que las palabras clave de `first/last_phrase_in_audio` aparecen **en orden** dentro del slice del transcript, sin exigir match literal al primer token (Gemini suele citar frases reales pero desplazadas).

`verification_strict_pass_rate` queda como métrica **informativa** (umbral `null` por defecto).

---

## Tiers

| Tier | Duración | Videos | Copy+Juez | Cuándo usar |
|------|----------|--------|-----------|-------------|
| **smoke** | ~2-4 min | 1 (`claude_hacks_regression_01`) | No | Antes de cada deploy |
| **analysis** | ~10-20 min | 4 enabled | No | CI / cambio de `MODEL_ANALYSIS` |
| **full** | ~30-60 min | 4 enabled | Sí | Cambio de copy, juez, o release |

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

### Fase 4 — Tier `e2e` (futuro)

Requiere descarga + Whisper en eval (costoso). Métricas objetivo:

- `verification_failed` post-snap
- `sub_coverage` / `words_per_sec`
- % jobs con MP4 renderizado

Candidato: 1 video (`claude_hacks`) en nightly job, no en cada deploy.

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
| `duration_pass_rate` | ≥85% | Momentos 10-60s tras validadores |
| `phrase_anchor_pass_rate` | ≥55% | Frases citadas existen en el clip (fuzzy) |
| `verification_strict_pass_rate` | null | Match estricto inicio/fin (suele ser bajo) |
| `copy_clean_rate` | ≥85% | Momentos sin problemas de validación de copy |
| `judge_llm_delta_avg` | ≤4.0 | Delta sano con juez cross-family (GPT vs Gemini) |
| `judge_response_rate` | ≥90% | Juez devuelve JSON válido |

---

## Archivos

| Archivo | Rol |
|---------|-----|
| `golden_set.json` | Videos, tiers, umbrales |
| `run_golden_set.py` | CLI |
| `eval_metrics.py` | Agregación y chequeo de umbrales |
| `services/validation.py` | `phrase_anchor_in_clip`, `evaluate_moment_phrase_metrics` |

---

*Última actualización: julio 2026*
