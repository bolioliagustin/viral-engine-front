# W18 — Caché íntegra y red de seguridad

**Rama:** `fix/worker-cache-integridad` · **Agente:** fiabilidad · **Ola:** 0 · **Base:** `integracion/mejora-ola-0` · **Depende de:** nada · **Categoría:** bug

## Contexto

Leé [`../PLAN_MEJORA.md`](../PLAN_MEJORA.md) §1 (hallazgos H1–H5) y el Anexo A.3 (cronología del job `fb287cba`). Leé también [`../PROYECTO.md`](../PROYECTO.md) §5.5 (sub-pipeline por momento) y §5.8 (caches), y los términos Transcript, Candidato, Verificación y Posteable de [`../../CONTEXT.md`](../../CONTEXT.md).

En resumen: un job corrido después del fix de la pista de audio (W17) reutilizó el transcript y el análisis de una corrida anterior hecha sobre el doblaje en inglés. Ningún candidato se ancló al audio real, y el pipeline igual entregó 5 clips rotos.

## Comportamiento actual

- `get_cached_analysis` / `get_cached_analysis_row` devuelven un análisis por (video_id, modelo, tono, `effective_prompt_version`), aunque el transcript actual sea otro.
- El tono forma parte de esa clave, pero la Pasada A (`select_moments`) no lo usa: el mismo análisis se paga una vez por tono.
- `get_cached_transcript(video_id, source="whisper_full", model=…)` usa la copia local `downloads/<id>_transcript_whisper_full_<modelo>.json` cuando Supabase responde sin filas. En el VPS `downloads/` es el volumen `worker_downloads`, que sobrevive a los deploys.
- `_process_job_inner` guarda cualquier transcript, incluso los de `whisper_full`, bajo la clave pelada `video_id`, que corresponde a los captions (paso "S3").
- Un transcript cacheado en otro idioma que `TRANSCRIPT_LANGUAGE` se usa igual.
- Si todos los candidatos terminan con `hook_not_found`/`payoff_not_found`, el job evalúa los 30 hasta el final y entrega.
- `select_finalists` completa el piso (`max(target, DELIVERY_MIN_CLIPS)`) con candidatos de `deferred` aunque tengan señales de nivel FUERTE.
- Cuando el anclaje W1 falla, `_refine_bounds_legacy` aplica "Head filler trim": si las 3 primeras palabras no comparten palabras con `moment.hook`, recorta hasta el primer fin de oración dentro del primer 35 % del clip.

## Comportamiento deseado

1. **Huella del transcript.** Todo transcript lleva una huella estable que resume fuente, modelo, idioma, pista de audio (si se conoce) y hash del texto de sus Líneas o segmentos. El análisis guardado registra la huella del transcript con el que se calculó. Un análisis se reutiliza **solo si su huella coincide** con la del transcript actual; si falta o difiere, se recalcula y se pisa. Las filas viejas, que no tienen huella, cuentan como distintas.
   La versión efectiva de la caché (`effective_prompt_version`) acepta además los **flags que cambian la Pasada A**, igual que hoy acepta la fuente del transcript. Hoy no hay ninguno; W21 y W22 los van a sumar. Dejá el mecanismo listo y con test (`PLAN_MEJORA.md` §4.1).
2. **Tono fuera de la clave de la Pasada A.** Cuando el análisis sale de la Pasada A (dos pasadas, el default), se reutiliza entre tonos. El camino legacy (mega-prompt), que sí depende del tono, conserva el tono en la clave.
3. **Copia local solo ante una caída.** `get_cached_transcript` usa la copia local únicamente si la consulta a Supabase **lanza un error**. Si Supabase responde "no hay fila", la copia local se ignora (y se puede borrar). La copia local guarda la misma huella.
4. **Cada transcript bajo su clave.** El transcript `whisper_full` se guarda solo bajo su clave compuesta; la clave pelada queda para los captions.
5. **Guardia de idioma.** Con `TRANSCRIPT_LANGUAGE` definido, un transcript cacheado cuyo idioma dominante (por mayoría de palabras) es otro se descarta y se recalcula. Se loguea con el porcentaje medido.
6. **Purga por video.** Un script `worker/scripts/purge-video-cache.py <video_id> [--dry-run]` borra `analysis_cache`, `category_cache`, `transcription_cache` (clave pelada y compuestas) y los archivos locales `downloads/<video_id>*` (transcripts y audio). Imprime qué borró. Corre igual en la Mac y dentro del contenedor.
7. **Cortacircuitos.** Durante la evaluación de candidatos: si **4 de los primeros 5** candidatos evaluados, o **≥ 50 %** del total con un mínimo de 4, quedan con `hook_not_found` o `payoff_not_found`, el transcript se considera desalineado del audio. El job entonces:
   - invalida la caché de ese video (la misma lógica que la purga),
   - rehace el transcript (descarga nueva del audio) y la Pasada A **una sola vez**,
   - reinicia la evaluación.

   Si se vuelve a disparar, el job termina `failed` con un error legible ("no pudimos alinear el audio del video con su transcript") y el crédito se devuelve por el camino existente (F1, `_finalize_job_outcome`). Cada disparo queda en el log con nivel ERROR y prefijo `CORTACIRCUITOS`. El fallo final usa la alerta de jobs fallidos que ya existe (F1).
8. **El piso no entrega rotos.** El relleno del piso en `select_finalists` excluye los candidatos con `hook_not_found`, `payoff_not_found` o `bad_segment`. Si quedan menos entregables que el piso, se entregan menos. Con 0, el job falla y devuelve el crédito, como hoy.
9. **Sin recorte por palabras del hook.** Cuando el anclaje W1 falla y el momento trae frases de Verificación, el refinamiento conserva los límites alineados a Líneas (o los originales) y marca el candidato como roto. El "Head filler trim" queda solo para momentos sin frases de Verificación (jobs legacy), o se elimina si ningún camino vivo lo usa.

## Interfaces clave

- `get_cached_analysis(video_id, model, tone, prompt_version)` y `save_analysis(…)`: el resultado guardado lleva la huella; la lectura la compara. Evitá cambiar el esquema (la huella viaja dentro de `result` o en `prompt_version`). Si hace falta una columna, usá una migración con Supabase CLI (ADR 0006).
- `get_cached_transcript` / `save_transcript`: la semántica de "Supabase no respondió" y "respondió vacío" pasa a ser distinta.
- `select_finalists(candidates, target)`: mismo contrato de retorno.
- `CandidateEval`: ya trae `hook_not_found`, `payoff_not_found` y `bad_segment`.

## Criterios de aceptación

- [ ] Test de regresión de `fb287cba`: con un análisis cacheado de huella distinta, la Pasada A se recalcula.
- [ ] Test: un análisis cacheado sin huella (fila vieja) se recalcula.
- [ ] Test: el mismo transcript con dos tonos distintos da un solo cálculo de Pasada A (segundo acceso = cache hit).
- [ ] Test: Supabase responde vacío y existe la copia local → no se usa la copia local.
- [ ] Test: Supabase lanza un error y existe la copia local → se usa la copia local.
- [ ] Test: con `TRANSCRIPT_LANGUAGE=es` y un transcript cacheado mayoritariamente en inglés → se descarta.
- [ ] Test: el transcript `whisper_full` ya no se escribe bajo la clave pelada.
- [ ] Test del cortacircuitos: con 4 de 5 candidatos rotos se dispara, rehace una vez, y un segundo disparo termina `failed` con el mensaje y la devolución de crédito (mocks de Supabase y Whisper).
- [ ] Test: `select_finalists` con 0 candidatos sanos y 10 rotos entrega 0.
- [ ] Test: `select_finalists` con 2 sanos y 10 rotos entrega 2, no 5.
- [ ] Test: con anclaje fallido y frases de Verificación presentes, `snap_trim_start` = 0 (no hay recorte por palabras del hook).
- [ ] `purge-video-cache.py --dry-run B60BHDNFNxM` lista lo que borraría, sin borrar (probado con mocks; en el VPS lo corre Agustín).
- [ ] La suite del worker está en verde con el comando de `AGENTS.md`.
- [ ] Docs actualizados: `PROYECTO.md` §5.8 (caches y huella) y §5.5 (cortacircuitos, piso); `CONTEXT.md` con **Huella del transcript** y **Cortacircuitos**.

## Cómo se mide

- Golden set tier `e2e` (ver `worker/eval/README.md`) antes y después, sobre los 4 videos habilitados: 0 disparos del cortacircuitos, los mismos clips o más, costo de Pasada A igual o menor (menos recálculos por tono).
- Guardá el run en `worker/eval/runs/<fecha>-w18.json` y agregá una línea en `worker/eval/runs/README.md`.

## Fuera de alcance

Cambios de prompt o de modelo · la selección por ventanas (W21) · el umbral y el cupo de entrega (W25): acá solo se toca el piso · el tope de duración (lo hace W20, en otra rama).

## Entrega

Commit y `git push -u origin fix/worker-cache-integridad` después de cada paso en verde. PR contra `integracion/mejora-ola-0` con la plantilla de `PLAN_MEJORA.md` §8.4. En "Contrato tocado", indicá si agregaste una migración o una variable de entorno.
