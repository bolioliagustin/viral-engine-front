# W21 — Pasada A por Ventanas

**Rama:** `feat/worker-pasada-a-ventanas` · **Agente:** selección · **Ola:** 1 · **Base:** `integracion/mejora-ola-1` · **Depende de:** W19 (tier `seleccion` y Referencias) y W18 (huella de caché) · **Categoría:** mejora

## Contexto

Leé [`../PLAN_MEJORA.md`](../PLAN_MEJORA.md) §1 (H6–H8), §5 (D4) y el Anexo A.1, que tiene el experimento de una pasada contra 4 ventanas. Leé también [`../PROYECTO.md`](../PROYECTO.md) §5.3 y §6 (Pasada A), y los términos Pasada A, Candidato y Línea de [`../../CONTEXT.md`](../../CONTEXT.md). La medición vive en el tier `seleccion` (`worker/eval/README.md`).

Con el transcript correcto de un video de 111 min, la Pasada A devolvió 30 candidatos en orden cronológico, todos en los primeros 51 min (cuartos [17, 13, 0, 0]). Con el mismo prompt por ventanas de ~28 min, la segunda hora pasó de 0 a 16 candidatos, pero una historia se perdió en el borde entre dos ventanas.

## Comportamiento actual

`select_moments` hace una sola llamada con el transcript completo y pide `candidate_count(duración)` candidatos (tope 30), "ordenados del mejor al peor". El modelo los devuelve cronológicos y agota el cupo antes del final. `rank_and_prune_candidates` ordena y anota `candidates_all`.

## Comportamiento deseado

Detrás de `SELECCION_POR_VENTANAS=on|off` (default `off` hasta pasar G1):

1. **Ventanas.** Las Líneas del transcript se parten en Ventanas de `VENTANA_MIN` minutos (default 20) con `VENTANA_SOLAPE_SEG` de solape (default 180). Una última Ventana de menos de 8 min se une a la anterior. Un video de hasta `VENTANA_MIN` + 5 min usa una sola Ventana (comportamiento de hoy).
2. **Cupo por Ventana** proporcional a sus minutos. El **total** respeta `candidate_count(duración)` (≤ 30) hasta que W23 levante el tope. Cada Ventana recibe al menos 3.
3. **Llamadas en paralelo** (≤ 4 concurrentes) con el mismo `get_selection_prompt`. Cada llamada ve solo las Líneas de su Ventana. Los timestamps son absolutos. El contexto indica la duración total del video y el rango de la Ventana.
4. **Unión:**
   - **Deduplicación:** dos candidatos que se solapan > 50 % de la duración del más corto se reducen a uno, el que contiene al otro o, si no, el mejor posicionado por su Ventana.
   - **Fusión de la misma historia:** dos candidatos contiguos (hueco ≤ 5 s) con duración combinada ≤ `CLIP_MAX_DURATION_SEC` se fusionan si vienen de la misma Ventana o del solape. Medí si la fusión ayuda (`historias_partidas`) y, si no ayuda, dejala apagada con un flag.
5. **Salida igual que hoy** (`viral_moments`, `candidates_all`), más el campo `ventana` por candidato. `rank_and_prune_candidates` sigue funcionando.
6. **`PROMPT_VERSION`** sube a un valor **nunca usado**: `analysis_cache` ya tiene filas `v9` de un experimento del 20-sep. La huella de W18 separa el resto. Además, `SELECCION_POR_VENTANAS` entra en la **versión efectiva de la caché** (mecanismo de W18, `PLAN_MEJORA.md` §4.1): un análisis por Ventanas nunca se sirve con el flag apagado, ni al revés.
7. **Costo y tiempo:** se loguea el costo de la Pasada A y la latencia total. Si una Ventana falla tras los reintentos, el job sigue con las demás y lo marca en el log. Si fallan todas, se cae a la pasada única de hoy.

## Interfaces clave

- `select_moments(transcript_text, video_info, duration, category, language, client, model, transcript)`: misma firma y mismo retorno. Las Ventanas se arman a partir de `transcript["lines"]` cuando existen; sin Líneas (captions), una sola Ventana.
- Nuevas funciones puras y testeables: armado de Ventanas, reparto de cupo, deduplicación y fusión.
- Variables de entorno nuevas en `.env.example` y en `PROYECTO.md` §11.

## Criterios de aceptación

- [ ] Tests de funciones puras: bordes de Ventana, solape, última Ventana corta, video corto de una sola Ventana, reparto de cupo con mínimo 3 y total ≤ 30, deduplicación, fusión (sí y no), timestamps absolutos.
- [ ] Test: una Ventana que falla no tira el job; todas fallando cae a la pasada única.
- [ ] Medición en el tier `seleccion` con `--reps 3`, flag `off` contra `on`, sobre todos los videos con Referencias validadas:
  - `recall_completo@candidatos` (Referencias A) ≥ 60 % en videos > 60 min;
  - `min_cuarto` ≥ 15 % en videos > 60 min;
  - `historias_partidas` ≤ 1 por video;
  - sin regresión > 5 pp en videos < 30 min;
  - costo de la Pasada A ≤ +25 % y latencia ≤ +20 s.
- [ ] Medición guardada en `worker/eval/runs/` y resumida en su README.
- [ ] ADR `0009-seleccion-por-ventanas.md`, con contexto, decisión, alternativas descartadas (modelo más caro, más candidatos en una pasada) y consecuencias.
- [ ] `CONTEXT.md` gana **Ventana**. `PROYECTO.md` §5.3 y §6 quedan actualizados.
- [ ] La suite del worker está en verde.

## Fuera de alcance

El foco o el texto del prompt por formato (W22) · evaluación de candidatos (W23) · entrega (W25) · cambio de modelo: si querés probar `gemini-3-flash-preview`, es una corrida aparte **después** de medir las Ventanas con el modelo actual, y va en el reporte como dato, no en el default.

## Entrega

Commit y `git push -u origin feat/worker-pasada-a-ventanas` después de cada paso en verde. PR contra `integracion/mejora-ola-1` con la plantilla de `PLAN_MEJORA.md` §8.4, incluyendo la tabla antes → después por video.
