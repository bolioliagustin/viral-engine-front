# W30 — Registro para aprender

**Rama:** `feat/worker-registro-candidatos` · **Agente:** registro · **Ola:** 1 · **Base:** `integracion/mejora-ola-1` · **Depende de:** nada · **Categoría:** instrumentación, sin cambio de comportamiento

## Contexto

Leé `docs/ESTUDIO_ML_EVALUADOR.md` (§2.4, §8.2: la propuesta de W30) y `docs/PLAN_MEJORA.md` §4.1 (reglas de medición). Hoy cada job evalúa ~30 Candidatos, pero solo quedan rastros parciales: `analysis_cache.candidates_all` se pisa en cada job del mismo video y **la nota de Jev no se guarda en ningún lado**. Cada job que corre sin registrar es una etiqueta futura perdida.

## Comportamiento deseado

1. Por cada Candidato evaluado en un job de producción se persiste una fila con:
   - `job_id`, `video_id`, índice, `start_time`/`end_time` (propuestos y finales), texto de las Líneas del tramo;
   - `rank_score` de la Pasada A;
   - Juez (3 dimensiones y reasoning), Jev (`rank_score`, las tres notas 0–4 y la confianza);
   - flags de calidad, `w2_score`, elegido o descartado y el motivo;
   - `PROMPT_VERSION`, modelos, Formato/Categoría y fecha.
2. Las filas **no se pisan** entre jobs. Tabla nueva `candidate_evals` por migración con Supabase CLI (ADR 0006), con índice por `video_id` y `job_id`, más RLS/grants iguales a los de `job_usage_events`.
3. La escritura es no fatal (si falla, log y el job sigue) y **no ocurre en dry-run** (`EVAL_DRY_RUN`): las mediciones no escriben en producción (§4.1).
4. Exportador `worker/eval/exportar_candidatos.py` → JSONL versionado: candidatos + etiquetas disponibles (Referencias por solape de núcleo, `clip_feedback` por `content_result`), listo para el script de `worker/eval/experimentos/ml_factibilidad.py`.

## Criterios de aceptación

- [ ] Migración nueva en `supabase/migrations/` y `PROYECTO.md` §7 actualizado (es un contrato de esquema: decilo en el PR).
- [ ] Tests con mocks: se escribe una fila por Candidato con todos los campos; falla de Supabase no rompe el job; en dry-run no se escribe; Jev ausente queda en null.
- [ ] El exportador corre contra la base en solo lectura y genera el JSONL; test con datos sintéticos.
- [ ] `CONTEXT.md`: término **Registro de candidatos** (o el que mejor encaje).
- [ ] Suite del worker en verde.

## Fuera de alcance

Entrenar modelos · cambiar el ranking o la entrega · la UI.

## Entrega

Commit y push incremental. PR contra `integracion/mejora-ola-1` con la plantilla de `PLAN_MEJORA.md` §8.4. **No apliques la migración a la base de la beta**: la aplica Agustín al mergear.
