# W23 — Evaluar en texto primero

**Rama:** `feat/worker-evaluar-en-texto` · **Agente:** evaluación · **Olas:** parte A (medición) en la 1, parte B (código) en la 2 · **Base:** `integracion/mejora-ola-1` (A) y `integracion/mejora-ola-2` (B) · **Depende de:** W18 (loop con cortacircuitos) y, para B, del resultado de A · **Categoría:** mejora de rendimiento

## Contexto

Leé [`../PLAN_MEJORA.md`](../PLAN_MEJORA.md) §1 (H12), §4 (principio 4) y §5 (D6). Leé también [`../PROYECTO.md`](../PROYECTO.md) §5.5 (sub-pipeline por momento) y los términos Transcripción del clip, Rankeador y Línea de [`../../CONTEXT.md`](../../CONTEXT.md).

Hoy cada candidato se descarga, se transcribe (con margen extendido si hace falta), pasa por el juez y por Jev, uno por uno: ~55 s por candidato, 27–29 min para 30. La meta de la beta es < 15 min. Con `TRANSCRIPT_SOURCE=whisper_full` ya tenemos el texto puntuado de todo el video. La hipótesis es que alcanza para **ordenar**, y que la evaluación completa solo hace falta para los finalistas.

## Parte A — Medir antes de cambiar (Ola 1)

1. Tomá los candidatos que ya tienen nota sobre la Transcripción del clip: `candidates_all` de `analysis_cache` para los videos del golden set, y las corridas `e2e` de `worker/eval/runs/`. Necesitás ≥ 60, de ≥ 3 videos. Esa base es la de la beta: **solo lectura**, sin escribir nada en Supabase (`PLAN_MEJORA.md` §4.1).
2. Para cada uno, armá el texto desde las Líneas del transcript `whisper_full` cacheado dentro de sus límites, y puntualo con el mismo Rankeador (juez y Jev, por separado) y el mismo hook y overlay.
3. Reportá:
   - Spearman ρ texto ↔ clip, por Rankeador y por video;
   - superposición del top-12 por video;
   - casos donde más difieren, con el porqué (¿corte? ¿Whisper? ¿el anclaje movió los límites?).
4. Guardá todo en `worker/eval/runs/<fecha>-texto-vs-clip.json`, con una línea en su README.

**Decisión (la toma el coordinador con el reporte):**
- **ρ ≥ 0,8 y top-12 ≥ 80 %** → se hace la parte B completa.
- Si no → la parte B se reduce al pool paralelo (punto 4 de abajo), sin pre-ranking.

## Parte B — Pre-ranking textual (Ola 2)

Detrás de `EVALUACION=texto_primero|completa` (default `completa` hasta pasar G2):

1. **Pre-ranking textual:** todos los candidatos se puntúan sobre el texto de sus Líneas, en paralelo (≤ 8 concurrentes, con reintentos ante 429).
2. **Selección para evaluación completa:** los mejores `N_eval = ceil(1,5 × cupo de entrega)` por nota textual, con la misma regla de diversidad que `select_finalists` (solape y hook). El cupo es `DELIVERY_MAX_CLIPS` hoy, y el de W25 cuando entre.
3. **Evaluación completa** (lo que hace hoy `_prepare_moment_clip` más juez y Jev) solo para esos `N_eval`. Los demás quedan en `candidates_all` con la nota textual y el motivo "no evaluado: fuera del top textual".
4. **Pool paralelo** para la evaluación completa: ≤ 3 candidatos a la vez. Cada uno conserva su proxy sticky; la URL de googlevideo va atada a la IP que la resolvió (ver `AGENTS.md`). El cortacircuitos de W18 sigue funcionando con los primeros resultados que terminan.
5. Con el pre-ranking activo, `candidate_count` puede subir a ~1 cada 2,5 min (tope 50). Coordinalo con W21 (Ventanas): el cupo total pasa a leer ese valor.
6. `candidates_all` guarda las dos notas (textual y de clip) para seguir midiendo la correlación en producción.

## Interfaces clave

- El loop de evaluación de candidatos en `_process_job_inner` (hoy secuencial) y `_prepare_moment_clip`.
- `judge_moment_scores(clip_text, hook, viral_overlay, category, clip_duration_sec)` y `jev_rank_scores(…)`: mismas funciones, con texto de Líneas.
- `CandidateEval` gana un campo para la nota textual.

## Criterios de aceptación

- [ ] **A:** reporte commiteado con ρ, superposición y casos, con ≥ 60 candidatos de ≥ 3 videos.
- [ ] **B:** tests del orden por nota textual, de `N_eval`, de la diversidad, del pool (mock: 3 concurrentes, errores aislados) y del cortacircuitos con el pool.
- [ ] **B:** test del caso sin Líneas (transcript de captions): se evalúa todo completo, como hoy.
- [ ] **B:** tier `e2e` con `EVALUACION=completa` contra `texto_primero` en el golden set:
  - tiempo p90 −40 % o más;
  - p90 ≤ 15 min con video de 60 min y ≤ 25 min con 120 min;
  - costo por minuto menor;
  - `recall@entregados` y Posteable (si hay etiquetas) sin caída mayor al ruido (misma dirección en la mayoría de los videos).
- [ ] ADR `0011-evaluar-en-texto-primero.md`, con el reporte A como evidencia. `CONTEXT.md` gana **Pre-ranking textual**. `PROYECTO.md` §5.5 y §11 actualizados.
- [ ] La suite del worker está en verde.

## Fuera de alcance

Rúbricas por Formato (W24) · reglas de entrega (W25) · la Pasada A (W21, W22).

## Entrega

Parte A: commit y push del reporte y el script de medición; PR chico contra `integracion/mejora-ola-1`. Parte B: commits con push incremental en la misma rama rebasada sobre `integracion/mejora-ola-2`, y PR contra esa rama con la plantilla de `PLAN_MEJORA.md` §8.4.
