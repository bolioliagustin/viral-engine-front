# Plan de calidad de clips — de 4.7 a "posteable"

**Fecha:** 17 de septiembre de 2026 · **Estado de partida:** pipeline estable (3/3 corridas reales con 15/15 clips) pero con clips que el propio juez puntúa **4.7 de 10** en promedio y que el dueño del producto no publicaría · **Meta:** que un podcaster reciba clips que publica sin editar, y que cada mejora se mida antes y después.

Complementa a [`PROYECTO.md`](PROYECTO.md) (§5 pipeline, §6 IA, §15 plan de etapa). Vocabulario en [`../CONTEXT.md`](../CONTEXT.md).

---

## 0. Resumen ejecutivo

Analicé los **20 clips de los 4 jobs completados** (julio + los 3 de esta semana) con todos los datos que deja el pipeline: scores del juez y su razonamiento, scores de la Pasada A, flags de verificación, palabras Whisper de cada clip (inicio y final real), candidatos guardados en `analysis_cache`, transcripts de Supadata y logs del VPS.

**El problema no es el modelo ni el prompt: es que los cortes están mal.** 18 de 20 clips empiezan a mitad de oración, y en la mayoría el remate que la IA eligió queda **fuera** del clip. El juez lo dice en 19 de 20 razonamientos ("tarda en aterrizar", "se corta a medias", "remate incompleto"). Hay tres causas de fondo, todas en el pipeline y no en la IA:

1. **Resolución:** la Pasada A elige momentos sobre bloques de captions de 3 a 30 segundos. Las oraciones duran 2 a 8. El `start_time`/`end_time` numérico cae donde termina un bloque de captions, no donde termina la idea.
2. **El refinamiento post-Whisper solo recorta, nunca extiende.** Cuando el remate quedó fuera del segmento descargado, no hay forma de recuperarlo; cuando los timestamps de Whisper vienen mal (pasó con `whisper-1`), el recorte destruye el clip (33 s → 9 s con 72 palabras apretadas).
3. **El ranking de candidatos es ciego:** la Pasada A se autopuntúa 8-9 en todo (media 8.5 en las tres métricas, sin varianza), así que "elegir los 5 mejores de 11" es azar. El juez, que sí discrimina (2-7), corre **después** de descargar y no influye en qué se elige.

La buena noticia: todo esto es corregible con cambios acotados en el worker, y el propio pipeline ya deja los datos para medir el antes y el después. El plan tiene **7 líneas de trabajo paralelizables**, una **rueda de mejora continua** (medir → cambiar → medir) y **objetivos numéricos**.

---

## 1. Evidencia

### 1.1 Métricas sobre los 20 clips

| Métrica | Valor | Lectura |
|---|---|---|
| Juez promedio (hook / retención / compartibilidad) | **4.85 / 4.45 / 4.90** → 4.73 | Muy por debajo de "posteable" (≥7) |
| Pasada A autoevaluación | 8.5 / 8.5 / 8.55 | Inflada e inútil para rankear: sin varianza |
| Clips con juez ≥7 en las tres métricas | **0 / 20** | Ningún clip "bueno" según la rúbrica |
| `verification_failed` (frase o hook no coincide con el audio) | **15 / 20** | El usuario ve "⚠ Verificar corte" en el 75 % |
| `late_hook` (el gancho aparece >3 s después del inicio) | 10 / 20 | El clip arranca antes de la idea |
| `whisper_mismatch_last` (la última frase elegida no está en el clip) | 5 / 20 | El remate quedó fuera del segmento |
| Clips sin ningún flag | 5 / 20 | |
| Duración elegida → final | 34 s → 29 s de media | El refinamiento recorta un 15 % |
| Densidad < 1.2 palabras/s (segmento sin habla o desincronizado) | **3 / 20** | Clips basura no detectados: "O R m Y TleK E", frases repetidas |
| Densidad > 5 palabras/s (timestamps Whisper rotos) | 1 / 20 | Clip de 9 s con 72 palabras; subtítulos ilegibles |
| Costo IA por job (Groq activo) | US$0.04 | Hay margen para gastar más en calidad |
| Tiempo por job | 4–5 min | Idem |

### 1.2 Lo que dice el juez (razonamientos, resumidos)

Los 20 razonamientos repiten cuatro quejas:

- **"Tarda en aterrizar" / "empieza con relleno"** (13 de 20): el clip arranca antes del gancho.
- **"Se corta a medias" / "remate incompleto" / "queda colgado"** (11 de 20): el clip termina antes del cierre de la idea. Ejemplos reales de finales: *"de nuestro cliente le va a hacer a"*, *"Entonces, esto"*, *"Tercero,"*, *"Una vez que está"*.
- **"El overlay/hook promete algo que el transcript no entrega"** (8 de 20): el hook lo escribe la Pasada B a partir del texto real, pero la promesa (la "frase exacta", el "cómo") está en la parte del audio que no entró en el clip.
- **"Repeticiones / muletillas / genérico"** (6 de 20): momentos de bajo valor elegidos porque el ranking no discrimina.

Inicios reales de clips (primeras palabras Whisper): *"el raro, cuándo hay tendrías de encontrar"*, *"y los seres humanos no vamos a pintar nada en"*, *"principalmente por esta frase de aquí que"*, *"mesionado durante hace un año El tema es que"*, *"en los de América, incluso hay uno"*, *"potencialidad de y ahí vamos a la potencialidad"*. Ninguno es el inicio de una idea.

### 1.3 Cadena causal, con la prueba de cada eslabón

| # | Causa | Prueba |
|---|---|---|
| C1 | **La Pasada A no ve timestamps a nivel de oración.** Supadata devuelve captions de ~3-4 s que cortan oraciones por la mitad (*"…subir tu \| código propio de frontend. Todo esto de \| una forma…"*), y `COMPACT_TRANSCRIPT` los agrupa en bloques de hasta 30 s / 500 caracteres. El prompt exige "usá EXACTAMENTE los timestamps de la transcripción". | Muestra del transcript en `transcription_cache`; `format_transcript_for_prompt_compact` en `transcriber.py`; en `analysis_cache` los `end_time` caen antes de la `last_phrase_in_audio` que el mismo modelo eligió (ej. Wild Project m1: eligió terminar en *"la vía de contagio más habitual"*, el clip terminó en *"ese polvito que estás barriendo"*). |
| C2 | **El pipeline descarta la intención del modelo y se queda con el número.** El modelo entrega `first_phrase_in_audio` / `last_phrase_in_audio` (correctas: son texto real del transcript), pero el corte usa `start_time`/`end_time`. El ancla de primera frase solo mueve el inicio hacia adelante dentro del segmento ya descargado; nada busca la última frase para extender el final. | `_resolve_moment_video_source` + `refine_bounds_to_sentences` en `main.py`/`clip_generator.py`: solo recortan (`trim`), nunca extienden; el segmento se descarga con ±8 s de margen (`CLIP_KEYFRAME_MARGIN_SEC`) pero el pre-corte lo tira. |
| C3 | **Sin guardas de plausibilidad sobre Whisper.** Si los timestamps vienen corridos (`whisper-1` con `prompt` largo), el snap cree que hay 24 s de silencio y recorta habla real; el filtro post-shift no descarta palabras y quedan 72 palabras en 9 s. Si el segmento no tiene habla (audio desincronizado), densidad 0.2 palabras/s y el clip pasa igual. | Log del VPS job `1b1007c4 m=1`: `Whisper: 73/73 words (density=2.21)` → `Snap trim: 33.0s → 9.0s (start=23.98)` → `Snap words: 73 → 72` → `densidad: 7.98 w/s`. Clip m5 del job de julio: texto *"O R m Y TleK E"*, densidad 0.23, sin flag. |
| C4 | **Ranking ciego.** Sobre-generar 11 candidatos y quedarse con 5 por score propio no filtra nada porque todos los scores son 8-9. Los 6 descartados no se guardan (`analysis_cache` solo conserva los 5 finales), así que ni siquiera se puede evaluar si eran mejores. | `analysis_cache`: `hook=[9,8,8,8,8] retention=[8,9,8,8,9] share=[9,8,8,8,8]` en todos los videos. `rank_and_prune_candidates` en `moment_selector.py`. |
| C5 | **El juez llega tarde.** Corre después de descargar, transcribir y escribir el copy; sus scores (los únicos calibrados) se guardan pero no cambian ninguna decisión. Un clip con juez 2/2/2 se renderiza, se sube y se cobra igual. | Orden en `main.py` §5.5: Whisper → Pasada B → Juez → render. Job Fazt m4: juez 2/2/2, entregado. |
| C6 | **Groq roto durante meses** (clave inválida hasta el 17-sep): Whisper caía a `whisper-1`, que además de costar 9× tiene timestamps por palabra menos fiables (C3). | Logs `Groq falló (401)` en todos los clips hasta el fix; corregido en el VPS el 17-sep-2026. |

**No es el modelo.** Con `gemini-3.5-flash` los momentos elegidos son razonables (los temas son buenos: "el error del Ferrari", "contagio entre humanos", "3 días sin programar"); lo que falla es dónde empieza y termina el clip, y cuáles de los candidatos se descartan. Cambiar de modelo antes de arreglar C1–C5 no movería el promedio.

---

## 2. Qué es calidad para el usuario

El ICP (podcaster / coach hispanohablante) no mira scores: mira el clip y decide en 5 segundos si lo publica. Lo que decide, en orden:

1. **Arranca con la idea** (primera frase = gancho, sin "entonces, eh, bueno") y **termina con el cierre** (última frase completa, con remate). Es el 80 % de la queja actual.
2. **Es un momento que vale la pena** del episodio, no un tramo cualquiera; y los 5 clips son distintos entre sí.
3. **Subtítulos correctos y sincronizados** (nombres propios, términos del nicho) y legibles.
4. **Se ve profesional en vertical**: hoy el clip es el 16:9 completo, chico, sobre fondo desenfocado. Los productos de referencia (Opus Clip, Vizard, Klap) recortan al rostro del que habla. Para un podcast de dos personas esto es la diferencia entre "hecho con IA" y "hecho por un editor".
5. **El overlay y el copy** dicen lo que el clip realmente muestra.
6. Velocidad y que no falle (ya resuelto).

Definición operativa que vamos a medir (nuevo término en `CONTEXT.md`): un clip es **posteable** cuando el usuario responde "sí" a "¿lo publicarías tal cual, sin editar?". Esa etiqueta humana es la fuente de verdad; el juez es el proxy automático que se calibra contra ella.

---

## 3. Objetivos de la etapa de calidad

Medidos sobre el golden set (4 videos, 20 clips) y sobre los jobs reales de la beta:

| Métrica | Hoy | Objetivo | Cómo se mide |
|---|---|---|---|
| Juez promedio (3 métricas) | 4.7 | **≥ 7.0** | `score_judge` en `content_results` |
| Clips con juez ≥ 7 en las tres métricas | 0 % | **≥ 60 %** | idem |
| Clips "posteables" según humano | sin medir | **≥ 70 %** | feedback en UI (W7) / planilla del golden set |
| Clips que arrancan al inicio de una oración | ~10 % | **≥ 90 %** | primera palabra Whisper capitalizada y sin `late_hook` |
| Clips cuya `last_phrase` está dentro del clip | 75 % | **≥ 95 %** | `whisper_mismatch_last` = 0 |
| `verification_failed` | 75 % | **≤ 15 %** | flag |
| Clips basura (densidad < 1.2 o > 5) | 20 % | **0 %** | guarda automática los reemplaza |
| Delta juez vs Pasada A | 3.8 | ≤ 2.0 | señal de que el ranking mejoró |
| Costo IA por job | US$0.04 | ≤ US$0.15 | `usage_summary` |
| Tiempo por job (≤ 90 min de video) | 4–5 min | ≤ 10 min | `created_at` → `updated_at` |

---

## 4. Líneas de trabajo (paralelizables)

Cada línea es un brief listo para un agente. **Contratos compartidos** (no se cambian sin PR que actualice `PROYECTO.md` §7/§8): esquema `ViralMoment` (`worker/models/schemas.py`), columnas de `content_results`, flags de `clip_quality_issues`, formato JSON del golden set (`worker/eval/`). Reglas de trabajo: [`../AGENTS.md`](../AGENTS.md).

### W0 — Métricas primero: la rueda (agente "eval")

*Sin esto, ninguna otra línea puede demostrar que mejoró algo.* Es la primera en arrancar y corre en paralelo con todas.

- **Qué:** tier `e2e` del golden set que ejecute el pipeline real por clip (descarga + Whisper + Pasada B + juez, sin subir a R2) sobre los 4 videos y emita un JSON con: juez por clip y promedio, % con juez ≥7, flags, densidad, duración elegida vs final, primera/última frase real, costo y tiempo. Un comando: `python eval/run_golden_set.py --tier e2e --json > runs/<fecha>-<PROMPT_VERSION>.json`.
- **Además:** guardar en `analysis_cache` **todos** los candidatos (no solo los 5 finales) con sus scores, para poder comparar rankings; script `eval/compare_runs.py a.json b.json` que imprima el delta por métrica; carpeta `worker/eval/runs/` versionada con el baseline de hoy.
- **Baseline:** correr el tier `e2e` **antes** de tocar cualquier otra línea y commitear el JSON. Es la foto del "antes".
- **Archivos:** `worker/eval/run_golden_set.py`, `worker/eval/eval_metrics.py`, `worker/services/analysis_cache.py` (guardar candidatos), nuevo `worker/eval/compare_runs.py`.
- **Aceptación:** baseline commiteado; `compare_runs` funciona; el tier `e2e` corre en < 20 min para los 4 videos; documentado en `worker/eval/README.md`.

### W1 — Cortes anclados a frases (agente "cortes") — **mayor impacto**

- **Qué:** el corte deja de usar `start_time`/`end_time` como verdad y pasa a usar las frases que eligió el modelo. Por momento: (1) descargar el segmento con margen generoso (`start_time − 15 s`, `end_time + 20 s`); (2) Whisper (Groq, palabra por palabra) sobre **todo** el segmento con margen; (3) localizar `first_phrase_in_audio` y `last_phrase_in_audio` en las palabras (matching fuzzy, ya existe `find_phrase_start_in_words` / `phrase_anchor_in_clip`); (4) inicio = comienzo de la oración que contiene la primera frase (retroceder hasta la puntuación anterior o gap > 0.6 s), fin = final de la oración que contiene la última frase (avanzar hasta puntuación); (5) si la última frase no aparece en el margen, extender el margen una vez (+20 s) y reintentar; si sigue sin aparecer, marcar `payoff_not_found` y usar el mejor fin de oración disponible; (6) recién entonces pre-cortar y seguir con Pasada B, juez y render.
- **Reglas duras:** duración final entre 15 y 60 s (si queda < 15 s, extender al siguiente fin de oración; si > 60 s, cortar en el fin de oración más cercano ≤ 60 s); nunca arrancar en minúscula si hay un inicio de oración ≤ 2 s antes.
- **Archivos:** `worker/main.py` (sub-pipeline por momento, §5.5), `worker/services/validation.py`, `worker/services/clip_generator.py` (`refine_bounds_to_sentences` pasa a trabajar sobre el segmento con margen), `worker/services/downloader.py` (`CLIP_KEYFRAME_MARGIN_SEC` asimétrico).
- **Aceptación:** en el tier `e2e`, `whisper_mismatch_last` ≤ 5 %, clips que arrancan en inicio de oración ≥ 90 %, `late_hook` ≤ 10 %; tests unitarios con palabras sintéticas (frase al principio, frase fuera del margen, frase repetida).
- **Depende de:** W3 (guardas) conviene que entre antes o junto; W0 para medir.

### W2 — El juez elige (agente "selección")

- **Qué:** mover el juez **antes** de la decisión de qué renderizar. Flujo: Pasada A genera N candidatos (hoy `min(12, minutos)`); para los **N** (o al menos target + 3) se hace descarga con margen + Whisper + ancla de frases (W1) + juez sobre el texto real (**sin** Pasada B todavía, que es lo caro: ~US$0.007 por momento); se ordenan por juez (suma, con penalización si `verification_failed` o densidad anómala) y se renderizan solo los `target` mejores; Pasada B corre solo para los finalistas. Diversidad: descartar candidatos con > 30 % de solapamiento temporal o mismo `hook` semántico (comparación simple por palabras).
- **Costo extra estimado:** +3 descargas per-clip (~2–4 MB, 5–10 s cada una), +3 Whisper Groq (US$0.0005 c/u), +3 juez (US$0.0003 c/u): **< US$0.01 y ~1 min por job**.
- **Además:** guardar los scores del juez de todos los candidatos en `analysis_cache` (o tabla nueva `moment_candidates`) para que W0 pueda evaluar el ranking.
- **Archivos:** `worker/main.py` (reordenar el loop por momento en dos fases: evaluar → renderizar), `worker/services/moment_selector.py` (`rank_and_prune_candidates` deja de podar por score propio), `worker/services/scorer.py`.
- **Aceptación:** en el tier `e2e`, juez promedio de los 5 entregados ≥ el promedio del baseline + 1.5; ningún clip entregado con juez < 4 si existía un candidato mejor; costo por job ≤ US$0.15.
- **Depende de:** W1 (ancla) para que el texto que juzga sea el del clip final; W0.
- **Estado (18-sep-2026, rama `feat/juez-elige`):** implementado y con suite verde (211/211, 10 tests nuevos en `tests/test_seleccion.py`); **medición e2e pendiente** (frenada por pedido explícito de Agustín mientras corría la remedición de W6 — no se lanzó ninguna corrida que consuma APIs). `rank_and_prune_candidates` conserva `target + EVAL_POOL_EXTRA` (3) candidatos en vez de podar a `target`; `main.py` evalúa cada uno (`_prepare_moment_clip`: fuente + Whisper + ancla W1 + juez sin Pasada B) y `moment_selector.select_finalists` rankea por el juez con penalizaciones con nombre (`PENALTY_VERIFICATION_FAILED/DENSITY_OUT_OF_RANGE/BAD_SEGMENT/PAYOFF_NOT_FOUND/INSUFFICIENT_SOURCE`) y diversidad (solapamiento >30 % u hook casi idéntico); solo los finalistas pasan por `_deliver_moment` (Pasada B + render). Notas de todos los candidatos anotadas en `candidates_all`. `scorer.py` sin cambios — `judge_moment_scores` ya servía para juzgar sin copy.
  - **`PROMPT_VERSION` v4→v5:** el prompt de la Pasada A (texto que ve el LLM, `get_selection_prompt`) **no cambió una letra**; el bump es porque el *shape* de lo que queda cacheado sí cambió (`rank_and_prune_candidates` ahora guarda `target+3` candidatos en vez de `target` en `viral_moments`). Sin el bump, `analyze_with_openrouter` pega en el cache viejo (ya podado a `target` bajo la lógica anterior) y W2 nunca se ejercita — es necesario para medir, no opcional. Efecto secundario: la corrida paga Pasada A real en los 4 videos (antes cacheada a costo ~0), así que el costo total **no es comparable 1:1** contra `2026-09-18-w1-cortes.json`; reportar el costo con y sin Pasada A por separado.
  - **Medición pendiente — comando exacto para retomar** (desde `worker/`, con `.venv` y `.env` ya listos en este worktree):
    ```bash
    EVAL_DRY_RUN=1 ENVIRONMENT=development .venv/bin/python eval/run_golden_set.py --tier e2e --json \
      2>eval/runs/2026-09-18-w2-juez-elige.log >eval/runs/2026-09-18-w2-juez-elige.json
    .venv/bin/python eval/compare_runs.py eval/runs/2026-09-18-w1-cortes.json eval/runs/2026-09-18-w2-juez-elige.json
    ```
    Después: commitear `eval/runs/2026-09-18-w2-juez-elige.json` + línea en `eval/runs/README.md`, y calcular "cuántos finalistas no coinciden con el ranking viejo" (ya impreso en el log de la corrida, línea `📐 N/target finalistas no coinciden con el ranking viejo`) — es la medida directa de si W2 cambia algo. Recordar el umbral de ruido: un judge_avg que se mueve <0.3 no es señal (ver medición W1, 5.44→5.15 con el mismo código).

### W3 — Guardas de sanidad (agente "guardas") — **rápido, entra primero**

- **Qué:** (1) **plausibilidad de timestamps Whisper**: si la densidad de la región post-snap supera 5 palabras/s o el snap recorta > 40 % del clip, descartar el snap y usar límites por segmento (o re-transcribir con el otro proveedor); (2) **segmento sin habla**: densidad < 1.2 palabras/s o texto con < 8 palabras únicas o patrón repetido → marcar `bad_segment`, re-descargar con otro proxy/estrategia una vez y, si persiste, descartar el candidato (W2 elige otro); (3) **shift + filtro**: al desplazar la línea de tiempo tras un snap, descartar palabras con `end < 0` (hoy quedan y se aprietan); (4) **duración mínima post-refinamiento** 15 s; (5) hook anchor: nunca mover el inicio más del 40 % del clip (hoy el límite existe pero el snap por silencio no lo respeta).
- **Archivos:** `worker/services/clip_generator.py` (`snap_trim_bounds`, `shift_words_timeline`, `filter_whisper_words`), `worker/main.py` (loop por momento), `worker/services/validation.py` (`build_clip_quality_issues` con los flags nuevos `bad_segment`, `whisper_timestamps_suspect`, `payoff_not_found`).
- **Aceptación:** tests que reproduzcan el caso `1b1007c4 m=1` (73 palabras, timestamps corridos) y el caso "O R m Y TleK E"; en el tier `e2e`, 0 clips con densidad fuera de [1.2, 5].
- **Depende de:** nada. Es el primer PR.

### W4 — Transcript de alta resolución (agente "transcript") — fase 2, habilita la subida directa

- **Qué:** reemplazar los captions de Supadata como insumo de la Pasada A por **Whisper del audio completo** (Groq, US$0.04/hora → US$0.05 por un podcast de 77 min; 216× tiempo real). Con palabras + puntuación reales, la Pasada A recibe un transcript con frases enteras y timestamps exactos, y C1 desaparece de raíz. Es además lo que necesita la subida directa (ADR 0007): un archivo del creador no tiene captions de YouTube.
- **Qué hace falta:** descargar solo el audio (stream de audio por RapidAPI + proxy sticky, ~70 MB por hora a 128 kbps; ya existe `get_stream_urls` + `_download_bytes_sequential`); `_transcribe_chunked` debe usar Groq (hoy está clavado a OpenAI); trozos de 10 min con solape de 15 s (límite de tamaño de Groq); nuevo formato compacto para el prompt con oraciones completas `[mm:ss.s] Oración.`; `PROMPT_VERSION` v5; Supadata queda como fallback si la descarga de audio falla.
- **Riesgo:** vuelve a poner una descarga de YouTube (audio) en el camino crítico antes de la selección; mitigado por el fallback a Supadata y porque el audio es 10× más chico que el video.
- **Aceptación:** en el tier `e2e`, con Whisper full el `phrase_anchor_pass_rate` sube y la Pasada A entrega `start_time`/`end_time` que coinciden ± 1 s con sus propias frases; tiempo extra ≤ 90 s por hora de video.
- **Depende de:** W0 para comparar; independiente de W1–W3 (se puede desarrollar en paralelo y activar con un flag `TRANSCRIPT_SOURCE=whisper|supadata`).

### W5 — Reencuadre vertical (agente "visual") — producto, no scoring

- **Qué:** detectar rostro/hablante y recortar el 16:9 a 9:16 centrado en él, en vez del 16:9 completo sobre fondo desenfocado. Primera versión: detección de rostros por muestreo (1 frame/s) con un detector liviano (OpenCV DNN o MediaPipe, sin GPU), suavizado de la posición, `crop` dinámico en FFmpeg; si hay 2 rostros estables (podcast), alternar por el que habla usando la energía de audio por canal o, más simple, encuadre que incluya a ambos. Fallback al fondo desenfocado actual si no se detecta rostro (pantallas, tutoriales).
- **Archivos:** `worker/services/clip_generator.py` (`to_vertical_9_16` → nueva función `reframe_to_speaker`), nuevo `worker/services/reframe.py`, `requirements.txt`, Dockerfile (dependencias del detector).
- **Aceptación:** en los 2 videos de podcast del golden set, ≥ 80 % de los clips con rostro centrado y estable (revisión humana); tiempo de render ≤ 2× el actual; opción por job `layout=speaker|blur`.
- **Depende de:** nada técnico. Decisión de producto (§7).

### W6 — Hook, overlay y copy fieles al clip (agente "copy")

- **Qué:** (1) la Pasada B recibe además la primera y la última oración del clip final y el prompt exige que el hook sea una afirmación que **aparece** en el clip (no una promesa del tema); (2) el overlay (≤ 4 palabras) se valida contra el texto: al menos una palabra clave del overlay debe estar en las primeras 8 s del clip, si no se regenera; (3) el juez recibe el overlay y el hook finales (ya lo hace) y su `reasoning` se guarda para mostrarlo al usuario como "por qué este score".
- **Archivos:** `worker/services/processor.py` (`generate_moment_copy_full`), `worker/services/content_validators.py`, `worker/services/scorer.py`.
- **Aceptación:** en el tier `e2e`, quejas del juez del tipo "el overlay promete algo que el transcript no entrega" ≤ 10 % (se cuenta por palabras clave en el `reasoning`); `copy_clean_rate` ≥ 90 %.
- **Depende de:** W1 (el clip final tiene que estar bien cortado para que el hook sea fiel).

### W7 — Feedback humano en la interfaz (agentes "frontend" + "backend")

- **Qué:** en cada `ViralMomentCard`, dos botones: **"Lo publicaría"** / **"No"** y, si es "No", un motivo de una lista (arranca mal · termina mal · momento flojo · subtítulos mal · se ve mal · copy malo). Se guarda en una tabla nueva `clip_feedback` (`content_result_id`, `user_id`, `posteable bool`, `motivo`, `created_at`) vía `POST /api/clips/:id/feedback`. Panel `/admin/usage` muestra % posteable por semana y por motivo. También mostrar el `reasoning` del juez en la card (hoy está oculto en un tooltip).
- **Archivos:** `frontend/src/components/ViralMomentCard.tsx`, `backend/src/routes/clip-edits.js` (o nuevo `feedback.js`), migración Supabase CLI, `frontend/src/app/admin/usage/page.tsx`.
- **Aceptación:** etiqueta guardada en < 1 s; en el admin, correlación juez vs humano visible; migración aplicada por CLI (ADR 0006).
- **Depende de:** nada. Es lo que convierte a los 2 canarios y a la beta en fuente de datos.

### W8 — Modelos y prompts (agente "IA") — **recién después de W1–W3**

- **Qué:** con la rueda funcionando y los cortes arreglados, probar una variable por vez: `MODEL_ANALYSIS` (`gemini-3.5-flash` vs `gemini-3.5-pro` vs `gpt-5.4`), prompt de Pasada A con pedido explícito de "setup → remate" y ejemplos de buenos/malos momentos del golden set, `reasoning=medium`, y calibrar el juez contra las etiquetas humanas de W7 (si el juez y el humano no correlacionan, cambiar la rúbrica o el modelo juez).
- **Aceptación:** cada experimento es un JSON en `eval/runs/` comparado con `compare_runs.py`; se adopta solo lo que sube juez promedio **y** % posteable sin subir el costo por encima del objetivo.

---

## 5. La rueda de mejora continua

```
      ┌──────────────── jobs reales de la beta ────────────────┐
      │  content_results (juez, flags, densidad, duraciones)   │
      │  clip_feedback (posteable sí/no + motivo)  ← W7        │
      └──────────────┬─────────────────────────────────────────┘
                     ▼
   ┌─── golden set (4 → 8 videos, con etiquetas humanas por clip) ───┐
   │   tier e2e: baseline.json  ← W0                                  │
   └──────────────┬───────────────────────────────────────────────────┘
                  ▼
   cambio (una variable: corte, guarda, prompt, modelo)  →  PROMPT_VERSION++
                  ▼
   run e2e  →  compare_runs(baseline, nuevo)  →  ¿sube juez y posteable, no sube costo?
                  ▼ sí                                   ▼ no
   PR + deploy al VPS                              descartar, anotar en eval/runs/README
                  ▼
   una semana de jobs reales  →  panel admin (% posteable, juez, flags)  →  nuevo baseline
```

Cadencia propuesta: **una iteración por semana**. Lunes: baseline y elección de la variable; miércoles: run e2e y decisión; viernes: deploy y lectura de jobs reales. Cada run queda en `worker/eval/runs/` con fecha, `PROMPT_VERSION`, modelos y resultado, y un `README.md` con una línea por experimento (qué se cambió, qué pasó). El golden set crece con los clips que los usuarios etiquetan: cada clip con feedback humano es un caso de prueba nuevo.

Regla de oro de la rueda: **no se cambia nada del pipeline de IA sin un run del tier e2e antes y después.** Lo que no se mide no se toca.

---

## 6. Secuencia y paralelización

| Semana | En paralelo | Entrega |
|---|---|---|
| 1 | **W0** (eval e2e + baseline) · **W3** (guardas) · **W7** (feedback UI) | Baseline commiteado; 0 clips basura; los canarios pueden etiquetar |
| 2–3 | **W1** (cortes anclados) · **W5** (reencuadre, en rama larga) · W7 sigue | Clips que empiezan y terminan en oración completa; primer run e2e comparado |
| 3–4 | **W2** (juez elige) · **W6** (hook/copy fieles) | Juez promedio ≥ 6.5 en e2e; overlays que el clip cumple |
| 5–6 | **W4** (Whisper full, con flag) · **W8** (experimentos de modelo) · W5 llega a beta | Juez ≥ 7; ≥ 60 % de clips ≥ 7; % posteable ≥ 70 % en los canarios |

Reparto para agentes (un componente por agente, ramas `feat/<linea>-<tema>`, PR con CI verde):

| Agente | Líneas | Archivos que toca | Con quién coordina |
|---|---|---|---|
| eval | W0, W8 | `worker/eval/*`, `analysis_cache.py` | todos leen su JSON |
| guardas | W3 | `clip_generator.py` (snap/shift/filter), `validation.py` (flags), `main.py` (loop, mínimo) | cortes (mismos archivos: W3 entra primero y chico) |
| cortes | W1 | `main.py` §5.5, `validation.py` (anclas), `downloader.py` (margen) | guardas, selección |
| selección | W2 | `main.py` (dos fases), `moment_selector.py`, `scorer.py` | cortes (necesita el texto final) |
| transcript | W4 | `yt_transcript.py`, `transcriber.py` (chunked Groq), `processor.py` (formato), flag `TRANSCRIPT_SOURCE` | eval |
| visual | W5 | `clip_generator.py` (`to_vertical_9_16`), nuevo `reframe.py`, Dockerfile | nadie (función aislada) |
| copy | W6 | `processor.py` (Pasada B), `content_validators.py` | cortes |
| producto | W7 | frontend + backend + migración | eval (panel) |

Conflictos previsibles: `main.py` es tocado por guardas, cortes y selección → PRs chicos, en ese orden, rebase frecuente. `clip_generator.py` es tocado por guardas (funciones de snap) y visual (función de render): funciones distintas, sin solapamiento real.

---

## 7. Decisiones pendientes (para Agustín)

1. **Juez como métrica guía hasta tener etiquetas humanas.** El juez es un proxy: hoy coincide con tu impresión ("no son los mejores"), pero nadie lo calibró. Propuesta: usarlo como norte durante 4 semanas y calibrarlo con W7; si no correlaciona con lo que vos y los canarios etiquetan, se cambia la rúbrica, no las metas.
2. **Presupuesto por job.** Las mejoras W1+W2 suben el costo de US$0.04 a ~US$0.08–0.15 y el tiempo de 4 a ~6–8 min. Propuesta: aceptar hasta US$0.15 (queda margen sobre los US$0.225 del crédito).
3. **Reencuadre (W5): ¿entra en esta etapa?** Es lo que más cambia la percepción de "producto pro" para podcasts, pero es la línea más larga y no mueve el score del juez. Propuesta: sí, como rama larga de un agente aparte, con flag por job, para que llegue a la beta sin bloquear W1–W3.
4. **Whisper del audio completo (W4): ¿reemplaza a Supadata?** Arregla la causa raíz C1 y es necesario para la subida directa, pero vuelve a depender de descargar audio de YouTube. Propuesta: desarrollarlo detrás de un flag y decidir con el run e2e.
5. **Etiquetado del golden set.** Para que la rueda gire hace falta que alguien (vos, en la primera vuelta) etiquete los 20 clips actuales como posteable sí/no con motivo. Son 15 minutos y es el dato más valioso que tenemos hoy.

---

## 8. Lo que aprendimos de Opus Clip (18-sep-2026)

Agustín corrió el mismo video de `podcast_general_01` por Opus Clip y guardó la respuesta completa; el análisis está en `docs/ANALISIS_OPUS_CLIP.md`. Lo que cambia para este plan:

- **Confirma el núcleo:** los clips de Opus terminan al 100 % en fin de oración sobre un transcript puntuado por palabra, y su juez tampoco discrimina (32–37 sobre 40). W1 (cortes anclados) y W2 (el juez elige) son el camino correcto; **W4 (transcript puntuado con silencios) sube de prioridad** porque es la base de los cortes, los subtítulos y el "quitar silencios".
- **Tope de duración:** 7 de sus 10 mejores clips superan nuestros 60 s (su #1 dura 96 s). Propuesta: tope 120 s con objetivo por género (podcast 30–90 s). Decisión de Agustín (§7, punto 6).
- **Cantidad y presentación:** 42 clips con score curvado 83–99 y letras A–D contra nuestros 5 con 4–6 sobre 10. Nueva línea **W9 — muchos clips, ranking relativo, preview liviano y HD a pedido** (ver §6 del análisis). Decisión de Agustín (§7, punto 7).
- **Encuadre y subtítulos:** layout de dos caras apiladas y subtítulos de 1–3 palabras con palabra clave resaltada. W5 se acota (paneles/caras por escena, sin seguimiento cuadro a cuadro) y se agrega un estilo de subtítulos v2 en `clip_generator`.
- **Copy:** título + descripción + hashtags por clip, además de las tres piezas actuales.
- **No copiar:** el juez "A para todos" como métrica interna; solo como capa de presentación separada del ranking.

Decisiones nuevas para §7:

6. **Tope de duración 60 → 120 s.** Cambia `validate_durations`, la Pasada A y los límites de W1; sube ~2× el tamaño de descarga y el render por clip. Propuesta: sí, ahora, porque sin esto el ranking de W2 nunca va a poder elegir la idea completa.
7. **¿Cuántos clips entregamos y cómo?** Hoy 1/3/5 renderizados en HD. Propuesta: todos los candidatos viables como preview a baja resolución con score relativo, y HD + copy completo al descargar (lazy). Cambia el modelo de créditos: hay que decidir si el crédito se cobra por job o por clip descargado.

---

## 9. Plan de acción "ir por Opus" (decidido el 18-sep-2026)

Agustín aprobó el análisis completo (`ANALISIS_OPUS_CLIP.md`) y las dos decisiones de §8: **tope de duración 120 s** y **entrega tipo "todos los clips viables como preview, HD y copy al descargar"**. Este plan reordena las líneas W0–W8 y agrega W9–W11. La regla no cambia: cada cambio se mide en el tier `e2e` contra la corrida anterior, una medición a la vez.

### Estado real de cada línea (19-sep-2026, fuente única para el PR de `integracion/fase-0`)

Todas las líneas de código de la Fase 0 (W0–W11, más las dos que nacieron después: W9-A y F1) están mergeadas en `integracion/fase-0` (INT-1 + INT-2). Lo que falta en cada una es **medición** (correr el tier `e2e` y comparar) o **trabajo de producto** todavía no encarado — nunca código sin mergear.

| Línea | Qué era | Estado | Fecha | Nota |
|---|---|---|---|---|
| **W0** Eval e2e + baseline | Tier `e2e`, `compare_runs.py`, baseline | **Hecho y medido** | 17-sep | Es la infraestructura que mide a todas las demás |
| **W1** Cortes anclados a frases | Corte por oración, no por `start_time`/`end_time` numérico | **Hecho y medido** | 18-sep | Run vs baseline v4 en `eval/runs/2026-09-18-w1-cortes.json` |
| **W2 + W2-B + W2-C** El juez elige, tope 120 s, penalizaciones con nombre | Juez antes del render, ranking por juez con 3 niveles de penalización, tope 120 s | **Hecho, medición pendiente** | 18-sep (código) | Suite verde (211+/211+) desde entonces; la corrida e2e post-merge quedó frenada por pedido explícito de Agustín (§4 W2) y sigue **PENDIENTE DE CRÉDITO** — mismo bloqueo que la medición final de abajo |
| **W3** Guardas de sanidad | Plausibilidad de timestamps Whisper, segmento sin habla, duración mínima | **Hecho** | 18-sep | Entró junto con W1 (mismos archivos); tests de los casos reales (`1b1007c4 m=1`, "O R m Y TleK E") en verde |
| **W4** Transcript puntuado (Whisper full) | `TRANSCRIPT_SOURCE=supadata\|whisper_full\|hybrid` | **Hecho** | 18-sep (merge INT-1) | Default `supadata` (sin cambio de comportamiento); activar `whisper_full`/`hybrid` en producción es una decisión de Agustín, no medida todavía con ese flag activo |
| **W5** Reencuadre vertical (Split/Fill/Fit) | Encuadre por escena en vez de fondo desenfocado fijo | **Hecho** | 19-sep (merge INT-1) | Verificado por el coordinador (278 tests, 2 renders reales) antes del merge; `REFRAME_MODE=off` default, activar `auto` en el VPS es decisión pendiente de Agustín (§11) |
| **W6** Hook/overlay/copy fieles al clip | Hook y overlay validados contra el texto real del clip | **Hecho** | 18-sep (merge INT-1) | Remedición con el fix de `hook_is_faithful` mencionada en Fase 0 no se volvió a correr aparte: queda incluida en la medición final pendiente |
| **W7** Feedback humano | Botones "lo publicaría/no", motivo, panel admin | **Hecho** | 18-sep (merge INT-1) | Es la fuente de verdad para calibrar el juez (§7 punto 1); sin datos reales todavía (la beta no está corriendo) |
| **W8** Modelos y prompts | Experimentos de modelo/prompt calibrados contra W7 | **Pendiente** | — | Fase 2, explícitamente "recién después de W1–W3"; no arrancó |
| **W9-A + W9-B** Galería + HD a pedido (producto + worker) | `/results/[jobId]` en galería, `POST /api/clips/:id/hd`, ADR 0008; mitad worker: más candidatos evaluados, entrega por umbral (no target fijo), preview real, HD real | **Hecho, medición pendiente** | 19-sep (W9-A) + 20-sep (W9-B) | Código completo — ver §10. `candidate_count()` sube a 30 candidatos y `PROMPT_VERSION` v8: la medición de "≥8 clips por video de 60 min" queda incluida en la medición final, **PENDIENTE DE CRÉDITO** |
| **W10** Copy por clip + score visible | Título/descripción/hashtags, score curvado con letras A-D | **Hecho** | 18-sep (merge INT-1) | Sin medición de juez (no cambia el ranking interno, es capa de presentación); revisión visual hecha en preview de Vercel antes del merge |
| **W11** Subtítulos v2 | 1-3 palabras por bloque, palabra clave resaltada | **Hecho** | 18-sep (merge INT-1) | Gap de integración (b)/(c) cerrado con test (INT-1); pendiente de producto: `EditClipDrawer.tsx` no ofrece `tiktok_viral_v2` como opción manual todavía (§9 Frontend de `PROYECTO.md`) |
| **F1** Fiabilidad de la beta | ADR 0005 (créditos reservados), tope 90 min, Telegram, Sentry | **Hecho** | 19-sep (merge INT-2) | Suite backend 98/98 en verde con Node 20 puro; nada pendiente de código |
| **Medición final de la integración** | Tier `e2e` sobre `integracion/fase-0` completa vs `2026-09-18-w2c-verificacion.json` | **Pendiente de crédito** | — | OpenRouter en saldo negativo (instrucción explícita de Agustín en INT-2); comando exacto en `worker/eval/README.md` ("Tier `e2e`: cómo funciona") y en el `worker_done` de INT-1/INT-2 |

### Fase 0 — esta semana, en paralelo (tres agentes)

| Línea | Qué | Agente / rama | Mide contra |
|---|---|---|---|
| **W2-B** Tope 120 s + medir W2 | `validate_durations` 10–60 → 15–120; prompt de Pasada A ("una idea completa: planteo, desarrollo y remate; 20–120 s; en podcast lo normal es 40–90 s"); `compute_clip_bounds(max_s=120)`; márgenes de descarga acordes; `PROMPT_VERSION` v6. Luego la medición e2e de W2 que quedó pendiente. | selección / `feat/juez-elige` | `2026-09-18-w1-cortes.json` |
| **W6** remedir | La corrida con el fix de `hook_is_faithful` (bolsa de palabras). | copy / `feat/copy-fiel-al-clip` | `2026-09-18-w1-cortes.json` |
| **W4** Transcript puntuado con silencios | Whisper del audio completo (Groq `whisper-large-v3-turbo`, `verbose_json` con palabras), puntuación y mayúsculas, tokens de silencio ≥ 0,3 s con duración, `wordsPerMinute`; detrás de `TRANSCRIPT_SOURCE=whisper_full|supadata|hybrid`; la Pasada A recibe líneas (oraciones) en vez de bloques de captions. | transcript / `feat/transcript-whisper-full` | corrida W2-B (misma rama base) |
| **W10** Copy por clip + presentación del score | Título (≤ 60 chars), descripción (2 oraciones) y 10 hashtags por momento (además de tweet/post/caption); `reasoning` del juez en español y constructivo; **capa de presentación**: percentil curvado 60–99 sobre el ranking del juez dentro del job + letra A–D por dimensión; la card muestra título, score curvado, letras y "por qué"; el juez interno no cambia. Migración CLI para las columnas nuevas. | producto / `feat/copy-por-clip` | no mide juez: revisión visual en preview de Vercel |

### Fase 1 — próximas dos semanas

| Línea | Qué | Depende de |
|---|---|---|
| **W11** Subtítulos v2 | 1–3 palabras por bloque (el modelo decide cortes por énfasis), mayúsculas, borde 12–16 px, palabra clave resaltada en color (1–2 por bloque, elegidas por el modelo en la Pasada B), sin texto en silencios; estilo `tiktok_viral_v2` por defecto, el actual queda como opción. | W4 (silencios) — puede arrancar con las palabras de Whisper actuales |
| **W9** Muchos clips, ranking relativo, preview + HD a pedido — **hecho (W9-A+W9-B, 20-sep), medición pendiente, ver §10** | El worker evalúa todos los candidatos viables (objetivo ≥ 1 cada 2–3 min de video), renderiza **preview 480×854 `veryfast`** de todos y guarda el ranking; el HD 720p (y luego 1080p) y la Pasada B completa se generan **al descargar** (job de re-render, mecanismo ya existente de `clip_edits`); la card muestra la galería completa ordenada por score curvado; **crédito por job** (se cobra al encolar, ADR 0005) y HD ilimitado dentro del job. Requiere ADR 0008 (modelo de entrega y créditos). | W2 (ranking), W2-B (tope), W10 (presentación) |
| **W5** Encuadre por paneles/caras (acotado) | Por escena (PySceneDetect): detectar paneles (videollamada) y caras (YuNet/OpenCV, CPU, 2 fps); layouts `Split` (dos caras apiladas), `Fill` (una cara, recorte centrado con seguimiento suave por escena) y `Fit` (fondo desenfocado, el actual) como fallback; sin seguimiento cuadro a cuadro en v1. | nadie; rama larga |

### Fase 2 — después de la beta

- W8 modelos/prompts con la rueda ya girando; detección de patrocinio; "quitar silencios" en el editor; exportar XML; HD 1080p como upsell; email al terminar.

### Objetivos revisados (sobre el golden set, tier `e2e`)

- Juez ≥ 6,5 promedio y ≥ 40 % de clips con las tres ≥ 7 al cerrar la Fase 0 (hoy 5,15 y 0 %); ≥ 7,0 y ≥ 60 % al cerrar la Fase 1.
- 100 % de clips terminan en fin de oración y ≥ 90 % arrancan con mayúscula (W4).
- ≥ 8 clips entregados por video de 60 min (W9), 0 clips con `bad_segment` entregados.
- Posteable ≥ 70 % en la etiqueta humana (W7), que sigue siendo la verdad.

---

## 10. W9 — Muchos clips, ranking relativo, preview + HD a pedido (estado 20-sep-2026)

`docs/ANALISIS_OPUS_CLIP.md` §1 y §6 fila B: Opus entrega 42 clips de un video de 77 min como galería con preview liviano y HD a pedido; nosotros entregábamos 5 en HD directo. `feat/galeria-clips` (W9-A) hizo la mitad producto; `feat/galeria-worker` (W9-B) cerró la mitad worker. Código completo, entra en `integracion/fase-0` vía INT-2:

- **Producto (W9-A):** `docs/adr/0008-entrega-galeria-y-creditos.md` (propuesta, pendiente de confirmación de Agustín) — crédito por job sin cambios, galería y HD ilimitados dentro del job. Backend: `POST /api/clips/:contentResultId/hd` (HD a pedido, reusa `clip_edits` con `edit_type='hd_upgrade'`, sin columnas de estado nuevas); `GET /status/:jobId` agrega `preview_url`, `hd_url`, `hd_status`. Frontend: `/results/[jobId]` pasa a galería (grilla + filtro Todos/Mejores, detalle al abrir una tarjeta). Migración `galeria_hd` (columna `content_results.preview_url`, columna `clip_edits.edit_type`).
- **Worker (W9-B), docs/PROYECTO.md §5.5/§5.7/§11 tienen el detalle completo:**
  1. `moment_selector.candidate_count()` pide `min(30, max(6, minutos // 2))` candidatos (antes `min(12, minutos)`) y `rank_and_prune_candidates` ya no trunca — **todos** se evalúan de verdad (`PROMPT_VERSION` v8, cambia el shape cacheado).
  2. `select_finalists` entrega por umbral (`DELIVERY_JUDGE_MIN`, default 15/30) hasta `DELIVERY_MAX_CLIPS` (default **12**, no 30 — el costo de entregar 30 candidatos que pasan el umbral supera el tope de US$0.15/job, ver el cálculo en el commit de W9-B), con `target_moment_count()` (el 1/3/5 de siempre) y un piso absoluto de 3 como **piso mínimo garantizado**, no como cantidad exacta.
  3. `_deliver_moment` renderiza un **preview 480×854 crf 28** (antes 720×1280 crf 23) y lo guarda en `content_results.preview_url` (mismo archivo que `clip_url` — compatibilidad total con jobs viejos, que siguen sin `preview_url`).
  4. `clip_edit_processor.py`: `edit_type='hd_upgrade'` ya usaba 720×1280 (mismo pipeline que un edit de estilo) — con el preview de 480×854 de arriba, ese mismo render SÍ es ahora una mejora real de calidad, sin más cambios; se agregó una guarda de idempotencia (si la fila ya tiene `rendered_clip_url`, no vuelve a renderizar).
  5. Gap (5) de INT-1/F1: un job con 0 clips MP4 (sin excepción) marcaba `completed` y no devolvía el crédito reservado (F1 lo dejó como nota pendiente explícita en la migración `creditos_reservados`) — ahora `main.py::_finalize_job_outcome` lo marca `failed` con `error='sin clips viables'`, lo que dispara el trigger de F1.
  6. Sentry del worker: mismo fix que F1 ya había hecho en el backend, sin DSN hardcodeado de fallback.
- **Medición: PENDIENTE DE CRÉDITO** (OpenRouter en saldo negativo) — las métricas que hay que revisar cuando se corra: clips entregados por video (objetivo ≥ 8 en 60 min), juez promedio de los entregados (no debe bajar más de 0.3 al entregar más, riesgo real: con más candidatos evaluados el promedio de LOS ENTREGADOS puede bajar aunque cada uno individualmente sea bueno, porque antes solo se entregaban los 5 mejores), costo por job (objetivo ≤ US$0.15), tiempo por job.

---

## Apéndice — Datos crudos

Los datos de los 20 clips (scores, razonamientos del juez, palabras Whisper, flags) se extrajeron de Supabase el 17-sep-2026 con los scripts de esta sesión y quedan como baseline cualitativo. El baseline cuantitativo reproducible lo produce W0 (`worker/eval/runs/`).
