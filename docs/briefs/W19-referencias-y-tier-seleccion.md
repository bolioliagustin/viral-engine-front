# W19 — Referencias y tier `seleccion`

**Rama:** `feat/eval-referencias` · **Agente:** eval · **Ola:** 0 · **Base:** `integracion/mejora-ola-0` · **Depende de:** nada (para validar las Referencias necesitás a Agustín) · **Categoría:** mejora de medición

## Contexto

Leé [`../PLAN_MEJORA.md`](../PLAN_MEJORA.md) §3 (métricas por etapa), §5 (D2, D9) y el Anexo B (semilla de Referencias). Leé también [`../../worker/eval/README.md`](../../worker/eval/README.md) (tiers y comandos) y el término Posteable de [`../../CONTEXT.md`](../../CONTEXT.md).

Hoy la selección de momentos (Pasada A) solo se puede juzgar corriendo jobs completos y etiquetando clips. Esta línea crea una verdad humana consultable offline, las **Referencias**, y un tier que mide la Pasada A contra ellas en minutos.

## Comportamiento actual

- El golden set (`worker/eval/golden_set.json`) tiene 4 videos habilitados (clase/negocios, entrevista, corto, podcast largo) y ninguno de charla o humor.
- Ningún tier mide cobertura: `analysis` valida duraciones y anclas; `e2e` mide juez, flags, costo y Posteable.
- `run_golden_set.py` lee y escribe `analysis_cache`, así que repetir una corrida devuelve el mismo análisis.

## Comportamiento deseado

1. **Referencias.** Un JSON por video en `worker/eval/referencias/<video_id>.json`. Cada momento tiene:
   - `id`, `inicio`, `fin`,
   - `nucleo_inicio`, `nucleo_fin` (lo mínimo que un clip tiene que contener: del planteo al remate),
   - `tipo` (anécdota / opinión / frase citable / cruce con el público / imitación / dato / explicación),
   - `calidad` (`A` = lo publicaría seguro; `B` = probablemente),
   - `por_que`, `autor`, `validado_por`, `fecha`.

   Además, una lista de tramos a **excluir** (publicidad).
2. **Borrador asistido.** `worker/eval/borrador_referencias.py <video_id>` toma el transcript `whisper_full` cacheado (o lo genera una vez y lo guarda localmente) y le pide a un modelo de **otra familia** que la Pasada A, vía OpenRouter, que proponga 20–30 momentos con núcleo y porqué. La rúbrica tiene que ser propia, no el prompt de la Pasada A: si no, el recall sale inflado. La salida queda con `validado_por: null`, lista para que Agustín la revise. Guarda costo y modelo usados.
3. **Validación humana.** Un formato cómodo para que Agustín revise cada lista (markdown generado desde el JSON, con links `youtu.be/<id>?t=<seg>` por momento) y un comando que vuelve a aplicar sus cambios al JSON (mantener, borrar, ajustar tiempos, agregar). Los tiempos son segundos absolutos.
4. **Métricas** en `worker/eval/eval_metrics.py`, con definiciones en el README:
   - `recall_completo@candidatos`: fracción de Referencias A cuyo núcleo queda contenido en algún candidato (inicio ≤ núcleo_inicio + 2 s y fin ≥ núcleo_fin − 2 s). También sobre A+B.
   - `recall_parcial@candidatos`: algún candidato cubre ≥ 50 % del núcleo.
   - `historias_partidas`: Referencias sin candidato que las contenga, pero cuyo núcleo queda cubierto ≥ 80 % por la unión de dos o más candidatos.
   - `min_cuarto` y `candidatos_por_cuarto`, sobre la duración del video.
   - `candidatos_en_exclusion`: candidatos que se solapan > 50 % con un tramo excluido.
   - `precision_ref@k`: de los k mejores candidatos según el ranking del pipeline, cuántos coinciden (parcial) con una Referencia.
   - Las mismas métricas sobre entregados (`recall@entregados`) en el tier `e2e`.
   - **`captura_de_lo_mejor`, la métrica norte del plan** (`PLAN_MEJORA.md` §3): Referencias A cuyo núcleo está contenido en un clip **entregado** y etiquetado posteable en `clip_feedback`. Se calcula para jobs reales con etiquetas; sin etiquetas, reportá la versión "contenido en un entregado" y marcala como tal.
5. **Tier `seleccion`** en `run_golden_set.py`:
   - Corre clasificador + Pasada A (con el código de la rama) sobre el transcript cacheado de cada video con Referencias validadas.
   - `--reps N` hace N llamadas independientes y reporta media y desvío.
   - Con `--sin-cache` no lee ni escribe `analysis_cache` ni `category_cache`. Es el default del tier.
   - Emite JSON (`--json`) con métricas por video y agregadas, costo y tiempo.
6. **Tiers que no contaminan producción** (`PLAN_MEJORA.md` §4.1). El `.env` de la raíz apunta a la base de la beta, así que en los tiers `seleccion` y `e2e`:
   - los transcripts se leen de la caché (solo lectura);
   - la caché de análisis **no se lee ni se escribe**;
   - no se escribe ninguna caché de producción (`transcription_cache`, `analysis_cache`, `category_cache`).

   Si un transcript no está cacheado, se genera y se guarda **solo localmente**. Documentá en el README qué variable controla esto y que es el default de ambos tiers.
7. **Golden set por formato.** `golden_set.json` gana el campo `formato` (entrevista / charla / monólogo / clase) y nuevos videos:
   - `charla_humor_01` = `B60BHDNFNxM`, que se siembra con el Anexo B del plan;
   - `charla_humor_02` y `monologo_coach_01`: pedíselos a Agustín (videos de potenciales usuarios, 30–90 min);
   - los existentes, con su formato asignado.
8. **Baseline.** Tier `seleccion` con `--reps 3` sobre `main` para todos los videos con Referencias validadas, commiteado en `worker/eval/runs/<fecha>-seleccion-baseline.json`.

## Interfaces clave

- Esquema JSON de Referencia: documentalo en el README; es contrato para W21–W25.
- `run_golden_set.py --tier seleccion [--reps N] [--video ID] [--json]`.
- Funciones puras de métricas en `eval_metrics.py`, testeables sin red.

## Criterios de aceptación

- [ ] Tests de métricas con casos sintéticos: contenido, parcial, historia partida, fuera de cuarto, en exclusión.
- [ ] `borrador_referencias.py` produce el JSON para `B60BHDNFNxM` y se fusiona con la semilla del Anexo B sin duplicar.
- [ ] Referencias con `validado_por: "agustin"` para ≥ 6 videos, ≥ 2 de charla, ≥ 12 momentos cada una. Si Agustín todavía no validó, el PR queda abierto con el resto listo y lo marca en la descripción.
- [ ] `run_golden_set.py --tier seleccion --reps 3 --json` corre en < 15 min y < US$4 para 8 videos, sin tocar `analysis_cache`. Probalo con `SUPABASE_URL` apuntado a una URL muerta: la corrida no debe fallar.
- [ ] Test: en los tiers `seleccion` y `e2e`, ninguna función de guardado de caché (`save_analysis`, `save_category`, `save_transcript` hacia Supabase) llega a escribir, y la caché de análisis no se consulta (mocks).
- [ ] Baseline commiteado. `worker/eval/README.md` explica el tier, las métricas y el esquema de Referencia.
- [ ] `CONTEXT.md` gana **Referencia** y **Núcleo**.
- [ ] La suite del worker está en verde.

## Cómo se mide

El baseline es la medición. Guardá la salida y una línea por corrida en `worker/eval/runs/README.md`.

## Fuera de alcance

Cambiar la Pasada A, el ranking o la entrega · UI de etiquetado de Referencias (alcanza con markdown y JSON) · etiquetas de Posteable (siguen en `clip_feedback`).

## Entrega

Commit y `git push -u origin feat/eval-referencias` después de cada paso en verde. PR contra `integracion/mejora-ola-0` con la plantilla de `PLAN_MEJORA.md` §8.4. Avisale al coordinador en cuanto el borrador de las listas esté listo, para que Agustín empiece a validar mientras terminás el tier.
