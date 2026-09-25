# Estudio: ¿un evaluador aprendido para elegir los momentos?

**Fecha:** 25 de septiembre de 2026 · **Pedido por:** Agustín ("exhaustivo, que analice todo, con posibilidad real, costos, opciones, pros y contras") · **Base:** `integracion/mejora-ola-0` (`60d8d79`) · **Plan vigente:** [`PLAN_MEJORA.md`](PLAN_MEJORA.md) · **Vocabulario:** [`../CONTEXT.md`](../CONTEXT.md) · **Experimento:** [`../worker/eval/experimentos/ml_factibilidad.py`](../worker/eval/experimentos/ml_factibilidad.py) → [`../worker/eval/runs/2026-09-25-ml-factibilidad.json`](../worker/eval/runs/2026-09-25-ml-factibilidad.json) · **Gasto en APIs de este estudio:** US$0

Convención: **DATO** = medido o leído de una fuente primaria, con la consulta o el link. **ESTIMACIÓN** = cálculo o juicio mío, con el supuesto a la vista.

---

## 0. Resumen ejecutivo (una página)

**La pregunta.** ¿Conviene entrenar un modelo propio (machine learning con aprendizaje automatizado) que aprenda de nuestras etiquetas a evaluar y elegir momentos, para acercarnos al "1000 %": capturar lo mejor del video entero?

**La respuesta corta: todavía no. Hoy no hay datos para entrenar nada.** Sí conviene, desde ya, empezar a **juntar los datos correctos** y usar la parte barata del aprendizaje (calibrar y combinar lo que ya existe) dentro de W24. Un modelo entrenado entra al plan recién cuando se cumpla un gate de datos que hoy está a meses de distancia.

**Por qué no (DATO, consultas en §2):**
- Hay **22 clips etiquetados** Posteable, de **1 anotador** (Agustín) y **2 videos** (20 del mismo). Hay **17 Referencias validadas**, todas de **1 video** (B60). Hay **0 ediciones** y los **13 jobs** de la base son todos de Agustín: no existe feedback de usuarios reales.
- Con 22 etiquetas, el intervalo de un AUC de 0,70 va de ~0,46 a ~0,94 (±0,24). **No se puede distinguir un buen evaluador de uno al azar**, ni hablar de entrenar uno.
- Para que un rankeador nuevo demuestre que le gana por +0,10 de AUC al actual hacen falta **~255 clips etiquetados**; para entrenar una combinación de ~10 señales, **≥ 100 positivos** (≈ 300 etiquetas) repartidos en **≥ 15 videos**. Al ritmo comprometido (A4: ~1 h/semana, ~15–20 clips) eso es **4–5 meses de Agustín solo** (ESTIMACIÓN, §2.3).

**Lo que encontró el experimento (US$0, §7):**
1. **Posteable no es predecible con lo que hay:** ninguna señal (Juez, sus tres dimensiones, nota de la Pasada A, duración, posición, densidad) separa los posteables de los no posteables; todas con IC que cruzan 0,5 (Juez: AUC 0,61 [0,37–0,84]).
2. **"De lo mejor del episodio" sí tiene señal, y viene de donde no esperábamos:** en B60, el `rank_score` que la Pasada A se pone a sí misma separa los candidatos que tocan una Referencia validada (AUC **0,82** [0,68–0,95]; 0,78 controlando duración). Hoy esa señal **se tira**: el ranking final usa solo Juez/Jev. Es un video: es una hipótesis para W24, no una decisión.
3. **Que el candidato contenga la historia completa no lo predice ningún puntaje** (rank_score 0,51), lo predice **la duración del corte** (0,96). La completitud es un problema de **corte** (W21/W22), no de ranking. Un modelo aprendido no arregla eso.
4. **Aprender con poco empeora:** contra borradores de 4 videos, una regresión logística validada dejando un video afuera (0,59) **pierde** contra el Juez solo (0,68). La señal cambia de video a video (el `rank_score` va de 0,43 a 0,77 según el video).
5. **Posteable ≠ lo mejor** (lo dijo Agustín el 25-sep y los datos lo confirman): son dos preguntas distintas y necesitan dos etiquetas distintas. La literatura lo llama *criteria drift* (Shankar et al., UIST 2024): el criterio se define mirando ejemplos.

**Recomendación:**
- **Ahora (Olas 1–2, sin costo de modelo):** agregar **W30 · Registro para aprender** (S): persistir todas las señales de cada Candidato (Jev incluido, que hoy se pierde) y la etiqueta nueva "de lo mejor" por comparación de pares. En **W24**, sumar el `rank_score` como señal y probar un **Juez comparativo por lista** con la rúbrica de Agustín y ejemplos validados (opción a, ~US$0,005 por job).
- **Gate L1 (primer modelo aprendido, opción b):** ≥ 300 Candidatos etiquetados, ≥ 100 positivos, ≥ 15 videos, ≥ 2 anotadores con acuerdo κ ≥ 0,4; y una combinación de señales tiene que ganarle al mejor rankeador solo por ≥ 0,05 de AUC fuera de video, con IC que no cruce 0. Fecha realista: **no antes de enero de 2027**, y solo si entran canarios o usuarios.
- **No hacer:** fine-tuning de LLM, LoRA con GPU alquilada, un bucle de reentrenamiento automático, ni prometer "modelo propio" como ventaja competitiva antes de tener usuarios. Con 1 anotador y 2 videos, cualquier modelo aprende el gusto de Agustín sobre un video de hantavirus.
- **Presupuesto:** lo recomendado para 2026 cuesta **< US$15 de API** y **~25–35 h de agente**; lo que cuesta de verdad es **etiquetado**: ~1 h/semana de Agustín más 1–2 canarios.

---

## 1. Objetivo: ¿qué aprendería exactamente el modelo?

### 1.1 Las cuatro cosas que se podrían aprender

| Qué aprende | Pregunta | Etiqueta que necesita | Métrica que mueve | Veredicto |
|---|---|---|---|---|
| **R · Ranking "de lo mejor"** | Entre los Candidatos de este video, ¿cuáles están entre lo mejor del episodio? | Referencias validadas o comparaciones de pares | `recall@entregados` → **captura de lo mejor** (métrica norte) | **Es el objetivo correcto.** La métrica norte es exactamente esto. |
| **P · Posteable** | ¿Lo publicaría tal cual? | `clip_feedback` | Precisión (guardarraíl ≥ 80 %) y el **Piso de calidad** de W25 | Útil como **filtro**, no como ranking: mezcla calidad del momento con corte, subtítulos, encuadre y copy (4 de los 9 rechazos de hoy no son del momento). |
| **C · Límites del corte** | ¿Dónde arranca y termina la historia? | Núcleos de Referencia, recortes del editor | `historias_partidas`, motivos `arranca_mal`/`termina_mal` | **No es un problema de aprendizaje hoy.** E2 muestra que la completitud depende de la duración propuesta; W21/W22 lo atacan con reglas ("historia completa, 30–120 s"). |
| **Combinación** | Un score que ordene (R) con un piso que filtre (P) | Ambas | Norte + precisión | Es la forma final si algún día hay datos: W25 ya la pide ("piso que predice posteable") y W24 elige el orden. |

**El objetivo de un evaluador aprendido, si se hace, es R**: ordenar dentro de un video. Dos razones:

1. **La métrica norte es de ranking, no de clasificación.** Captura de lo mejor = qué fracción de las Referencias A llega entregada completa y posteable. Con la entrega por calidad de W25, lo que importa es que lo excelente quede arriba del piso; el número absoluto importa solo para el piso.
2. **Posteable es una etiqueta de clip terminado**, no de momento. Los 9 rechazos de hoy: 3 `momento_flojo`, 2 `termina_mal`, 1 `arranca_mal`, 1 `subtitulos_mal`, 2 `otro` (los dos comentarios hablan del título y la descripción). Solo 3 de 9 son "el momento no vale". Entrenar un ranking de momentos sobre Posteable le enseña a predecir subtítulos y copy.

### 1.2 Cómo se conecta con el plan

- **W24 (rankeador por formato, Ola 2)** ya es "aprendizaje" en su forma mínima: elegir entre rankeadores (Juez, Jev) **con etiquetas**, por formato. Un evaluador aprendido es la continuación natural de W24: en vez de elegir uno, **combinarlos**. No es una línea aparte que compite con el plan: es W24 con más datos.
- **W25 (Piso de calidad calibrado)** es un modelo de una variable: "la nota a partir de la cual la precisión es ≥ 80 %". Es el primer lugar donde hace falta calibrar con etiquetas. Con 22 etiquetas, ese umbral tiene un error enorme (§2.3): W25 va a necesitar las ≥ 40 etiquetas de G2 y conviene declararlo provisional.
- **G2** pide n ≥ 40 etiquetas (≥ 15 de charla). Ese es el primer punto en que se puede **medir** un rankeador; **no** alcanza para entrenar uno (§2.3).
- **W23 (evaluar en texto primero)** baja el costo de puntuar cada Candidato. Cualquier evaluador aprendido tiene que funcionar sobre texto de Líneas para no destruir el ahorro de W23.

---

## 2. Datos: qué existe, qué falta y cuánto hace falta

### 2.1 Inventario (DATO, 25-sep-2026, consultas de solo lectura a la base de la beta)

Todas las consultas son `SELECT` con la service key del `.env` de la raíz (proyecto `…aftk`). El script de §7 las reproduce.

| Fuente | Consulta | Qué hay | Lectura |
|---|---|---|---|
| `clip_feedback` | `select id, content_result_id, user_id, posteable, motivo, comentario, created_at from clip_feedback` | **24 filas → 22 clips** (última etiqueta por clip), **1 usuario**, entre el 21-sep 17:15 y el 23-sep 20:00 | 13 posteables (59 %, IC 95 % Wilson 39–77 %). Motivos de rechazo: `momento_flojo` 3, `termina_mal` 2, `otro` 2, `subtitulos_mal` 1, `arranca_mal` 1. |
| … por job | join con `content_results(job_id, moment_index)` | `9e739c7b` (XxoVRjTySsM, Juez): 12 clips, 6 posteables · `c7ea4108` (XxoVRjTySsM, Jev): 8, 6 · `fb287cba` (B60): 2, 1 | **2 videos**, y 20 de 22 son del **mismo** (una entrevista sobre hantavirus). Los dos jobs de XxoVRjTySsM comparten momentos: no son 20 observaciones independientes. |
| `jobs` | `select id, user_id, video_url, status, created_at from jobs` | **13 jobs** (10 completados, 3 fallidos), **todos del mismo usuario**; 9 videos distintos | La base tiene 4 usuarios; ningún usuario externo corrió un job. |
| `content_results` | `select id, job_id, moment_index, score_judge … from content_results` | **210 filas = 70 Momentos entregados** en 11 jobs, todos con `score_judge` | 3 filas por Momento (`CONTEXT.md`). 48 de los 70 no tienen etiqueta. |
| `clip_edits` | `select * from clip_edits` | **0 filas** | No hay recortes ni ediciones: la señal implícita más valiosa para los cortes no existe todavía. |
| `analysis_cache.candidates_all` | `select video_id, prompt_version, result from analysis_cache` | **27 filas, 412 Candidatos**; **261** con `judge_scores`, `w2_score` y `w2_selected` (evaluados de verdad) en 6 videos | La fila se pisa en cada job del mismo video (upsert): los candidatos de `9e739c7b` ya no están, quedaron los de `c7ea4108`. **El score de Jev no se persiste** (limitación conocida de `eval/README.md`). |
| Referencias | `worker/eval/referencias/*.json` | **B60: 17 validadas por Agustín (12 A, 5 B), 8 descartadas, 12 borradores sin validar.** Otros 6 videos: 153 momentos en borrador (Sonnet 5), 0 validados | Todo lo validado es **un solo video de charla/humor**. |
| Transcripts `whisper_full` | `select video_id, duration_seconds from transcription_cache` (claves `:whisper_full:`) + `worker/downloads/eval_transcripts/` (local, del agente W19) | Supabase: **5 videos, 5,1 h** (XxoVRjTySsM, Lqq78q17jDY, KXKzgeHOr7A, MaXgAEI4Vm8, qgK3GfY5ZC4). Local: 7 videos, 9,1 h. **Unión: 8 videos, 9,4 h** | Suficiente para rasgos de texto y para candidatos nuevos sin pagar Whisper; no para entrenar un modelo de lenguaje. |
| `job_usage_events` | `select count(*)` | 621 eventos | Costos y latencias: sirven para el presupuesto, no como etiqueta. |
| `worker/eval/runs/*.json` | archivos | 14 corridas: 7 e2e/juez (18–21 sep), calibración (20 clips), Jev contra Juez (59 clips, sin etiqueta humana), 2 baselines de `seleccion` (**7 videos × 3 reps × ~30 = 616 candidatos** con `rank_score`) | Los candidatos de `seleccion` son el mejor material para medir ranking contra Referencias, pero **no tienen nota del Juez** (el tier no la corre). |

**Resumen del inventario:** para la pregunta P hay 22 etiquetas; para la pregunta R hay 17 positivos validados en 1 video; para C, nada. Etiquetas de terceros, feedback implícito y usuarios reales: **cero**.

### 2.2 Ruido y sesgo

- **Un solo anotador.** Todo lo validado es el criterio de Agustín. Es la vara correcta para el producto hoy (el plan lo dice: "tu criterio de posteable es la vara"), pero no se puede separar su gusto del gusto de un podcaster que publica para su audiencia. No hay forma de medir el ruido de la etiqueta sin un segundo anotador.
- **La etiqueta se mueve con la experiencia (DATO):** Agustín etiquetó R16 ("Romay, colifa divino") como posteable el 23-sep y la **descartó** como Referencia el 25-sep. Descartó 8 de 25 momentos de la semilla (32 %), entre ellos 3 de 15 que Claude había marcado A. Es el *criteria drift* de Shankar et al. ([arXiv 2404.12272](https://arxiv.org/abs/2404.12272), UIST 2024): "grading outputs helps users define criteria". Cualquier modelo entrenado sobre etiquetas viejas aprende un criterio que su dueño ya cambió.
- **Acuerdo LLM ↔ Agustín (DATO):** de los 25 momentos de la semilla, el borrador independiente de Sonnet 5 propuso 16; Agustín conservó 12 y descartó 4 (**precisión 75 %**), y Sonnet encontró 12 de las 17 que Agustín validó (**recall 71 %**). Un LLM fuerte es un anotador ruidoso útil para proponer, no para reemplazar la validación.
- **Sesgo de selección en Posteable:** solo se etiqueta lo **entregado**, y lo entregado ya pasó el filtro del Juez. Las señales se evalúan en un rango recortado (E1 lo sufre: explica en parte por qué nada predice Posteable). Para aprender a elegir hay que etiquetar también **descartados**: eso solo se logra con Referencias o con comparaciones sobre Candidatos, no con la galería.
- **Sesgo de formato:** 20 de 22 etiquetas son de una entrevista informativa; todas las Referencias validadas son de charla/humor. Son los dos extremos: lo que sirve en uno no transfiere al otro (H10 y E3 lo muestran).

### 2.3 Cuántas etiquetas hacen falta (cálculo en `E4_tamano_de_muestra` del JSON)

Supuestos: prevalencia de positivos 35 % (ESTIMACIÓN, entre el 30 % de E2 y el 59 % de Posteable), α = 0,05, poder 80 %, error estándar del AUC de Hanley–McNeil (1982). Son cálculos para **una sola señal**; entrenar varias exige más.

| Para… | Hace falta | Fundamento |
|---|---|---|
| Saber si una señal predice algo (AUC 0,70 contra 0,5) | **71** etiquetas | Hanley–McNeil |
| … si el efecto es chico (AUC 0,60) | **288** | ídem |
| Estimar un AUC de 0,70 con ±0,11 | **100** (±0,24 con las 22 de hoy) | ídem |
| Demostrar que un rankeador nuevo le gana al actual por +0,10 (0,65 → 0,75), mismos clips, r = 0,5 | **255** | diferencia de AUC correlacionados |
| … por +0,05 | **~1 050** | ídem |
| Estimar la precisión de un Piso de calidad (0,8) con ±0,10 | **62** clips por encima del piso | binomial |
| Mostrar que la precisión sube de 0,65 a 0,80 | **138 por brazo** | dos proporciones |
| Entrenar una logística con ~10 señales | **≥ 100–200 positivos** (≈ 300–600 etiquetas) | 10–20 eventos por variable ([Peduzzi et al. 1996](https://doi.org/10.1016/S0895-4356(96)00236-3)); [Riley et al., BMJ 2020](https://doi.org/10.1136/bmj.m441) da cálculos más finos, en general del mismo orden o mayores |
| Medir el recall medio del sistema con ±10 pp | **18 videos**; ±15 pp: **8** | desvío entre videos del `recall_completo` = 0,216 (baseline `seleccion`) |
| Clasificador sobre embeddings con cabeza lineal | tareas de tema: 8–64 por clase ([SetFit, Tunstall et al. 2022](https://arxiv.org/abs/2209.11055)); gusto subjetivo como "gracia": **cientos** | ESTIMACIÓN: SetFit se midió en tareas de tema y sentimiento, no en humor |
| Optimizar un prompt con DSPy | ~10 ejemplos (`BootstrapFewShot`); **≥ 200 para MIPROv2** "to prevent overfitting" | [documentación de DSPy](https://github.com/stanfordnlp/dspy/blob/main/docs/docs/learn/optimization/optimizers.md), consultada el 25-sep-2026 |

**El cuello de botella no son las etiquetas, son los videos.** La señal cambia de video a video (E3), así que la unidad de validación es el video: 300 etiquetas de 3 videos valen menos que 150 de 15. Cualquier gate tiene que exigir **videos distintos**, no solo clips.

**Tiempo de etiquetado (ESTIMACIÓN):** validar las 25 Referencias de B60 le llevó a Agustín ~3–4 h (plan, Ola 0), y 20 clips de galería ~1–2 h. A ~1 h/semana (A4) son ~15–20 etiquetas por semana: **300 etiquetas ≈ 15–20 semanas** con Agustín solo; con 2 canarios a 1 h/semana, **6–7 semanas**.

### 2.4 Cómo conseguir más datos (de más barato a más caro)

| Vía | Qué da | Costo | Rinde | Cuándo |
|---|---|---|---|---|
| **Persistir lo que ya se calcula** (Jev, `rank_score`, flags, rasgos, texto de Líneas por Candidato, `job_id`) sin pisar la fila anterior | Rasgos de **todos** los Candidatos, no solo de los entregados | Horas de agente, US$0 | Sin esto, ninguna etiqueta futura sirve para aprender | **Ya** (W30, §8) |
| **Referencias validadas** de los 6 videos en borrador (G0 las pide) | Positivos y descartados por video, incluidos momentos que el pipeline no propuso | 3–4 h de Agustín por video (DATO de B60) | ~15–20 positivos por video | Ola 0 (ya en el plan) |
| **Comparaciones por pares** "¿cuál es mejor momento del episodio?" sobre Candidatos del mismo video, con el texto de las Líneas y el link con `&t=` | Orden relativo; resuelve "posteable ≠ lo mejor"; sirve para Bradley–Terry | ESTIMACIÓN: 20–40 s por par leyendo texto; ~100 pares/h | Más consistentes que notas absolutas y no sufren la escala del anotador (práctica estándar de RLHF: los modelos de recompensa se entrenan con pares) | Ola 2, en la galería o en un markdown como el de validación |
| **Etiquetado activo** | Elegir qué pares o Candidatos etiquetar: los que el rankeador actual duda o en que Juez y Jev discrepan | US$0 | 2–3× menos etiquetas para el mismo error (ESTIMACIÓN, orden de magnitud habitual en la literatura de *active learning*) | Junto con los pares |
| **Canarios** (A4) | Segundo anotador → se puede medir el ruido (κ) | Conseguirlos | Sin segundo anotador no hay forma de saber si el modelo aprende calidad o a Agustín | Ola 1–2 |
| **Feedback implícito del producto**: HD pedido, descarga, edición, recorte, compartir | Miles de señales débiles | Instrumentación (S) | **Cero hoy**: 0 ediciones, 0 usuarios externos. Requiere la beta abierta | Beta abierta |
| **Mapa de calor "lo más repetido"** de YouTube (W28) | Comportamiento real de la audiencia del video, 100 tramos por video | US$0 si se puede leer desde el VPS; zona gris de los términos (§6) | Es la única **etiqueta externa** gratis: si sus picos coinciden con las Referencias, es un rasgo **y** una verdad débil para entrenar | W28, Ola 4 (propongo adelantar la medición, §8) |
| **Engagement de clips publicados** (vistas, retención en TikTok/Shorts) | La verdad de negocio | Requiere que los usuarios publiquen y conecten sus cuentas | La mejor etiqueta posible, meses de latencia | Post-beta |
| **LLM fuerte como anotador** (destilación: Sonnet/Gemini Pro etiqueta, un modelo barato aprende) | Etiquetas en volumen | ~US$0,15–0,33 por video (DATO: borradores de W19) | Acuerdo con Agustín ~70–75 % (§2.2): enseña el gusto del LLM, no el del usuario | Solo para proponer, siempre validado |

---

## 3. Opciones técnicas

Supuestos comunes (ESTIMACIÓN salvo que diga DATO): video de 60 min, 30 Candidatos de ~60 s (~170 palabras, ~300 tokens de texto), VPS de 6 vCPU sin GPU (DATO, `PROYECTO.md` §10), presupuesto de costo IA ≤ US$0,003 por minuto de video (DATO, A3) — o sea ≤ US$0,18 por job de 60 min, del que ya se gastan US$0,10–0,15 (DATO: 0,0016–0,0025 por minuto). **El margen para un evaluador nuevo es de US$0,03–0,08 por job de 60 min**; G4 pide ≤ +US$0,0005 por minuto (US$0,03).

### (a) Calibrar el Juez LLM con ejemplos etiquetados y optimizar el prompt

**Cómo funciona.** Tres variantes, de menor a mayor esfuerzo:
- **a1 · Rúbrica de Agustín + ejemplos:** la rúbrica de charla escribe su criterio observado ("anécdota completa con remate, opinión fuerte, cruce con remate claro; no chistes cortos sueltos, chicanas ni confusiones sin remate") y el prompt lleva 6–10 ejemplos validados (conservados y descartados de B60). Es lo que W24 ya planea, con ejemplos.
- **a2 · Juez comparativo por lista:** una sola llamada recibe los textos de todos los Candidatos del video y devuelve un orden (o un torneo por grupos de 8–10). Compara en vez de puntuar en abstracto: ataca el problema de fondo del Juez (notas 4–6 amontonadas, 13,9 % de pares empatados, DATO de `ranker_jev.py`) y encaja con "lo mejor **del episodio**", que es relativo por definición.
- **a3 · Optimización automática (DSPy `BootstrapFewShot` / MIPROv2 / GEPA):** se busca automáticamente qué instrucciones y ejemplos maximizan el acuerdo con las etiquetas.

| | a1 | a2 | a3 |
|---|---|---|---|
| Datos mínimos | 10–20 ejemplos + ≥ 40 para medir (G2) | ídem | 10 para empezar; **≥ 200 para MIPROv2** (DSPy) |
| Desarrollo | 6–10 h agente, 1 h Agustín (rúbrica) | 12–20 h agente | 15–25 h agente + integrar DSPy |
| Entrenamiento | US$0 | US$0 | ESTIMACIÓN: 200–1 000 llamadas de optimización × ~US$0,0005 (nano) ≈ US$0,1–0,5 por corrida; con un modelo mayor, US$2–10 |
| Inferencia por job | nano con ~6 k tokens de ejemplos por llamada × 30: US$0,04 sin caché; ~US$0,015 con caché de prefijo (ESTIMACIÓN con precios de §4) | 30 × 300 + ~4 k de rúbrica y ejemplos ≈ 13 k tokens de entrada, ~1 k de salida: **~US$0,004** con nano, ~US$0,03 con Gemini 3.5 Flash | igual que a1/a2 |
| Tiempo en el VPS | red, en paralelo: 5–15 s | 1 llamada: 10–30 s | igual |
| Riesgo | sobreajuste a los ejemplos de B60; sesgo de posición en la lista (a2) | ídem; hay que barajar y repetir | sobreajuste fuerte con < 200; la métrica de optimización es la que decide (lección del 0,06) |

**Pros:** se hace con los datos que ya hay; no agrega infraestructura; reversible con un flag; el costo entra en el margen. a2 es la única opción que usa la naturaleza **relativa** del problema. **Contras:** sigue siendo solo texto (techo en humor, H10); con 40 etiquetas la mejora no va a ser estadísticamente demostrable (§2.3) y hay que decidir con el agregado de videos y con el criterio de "no empeora".

### (b) Ranking liviano sobre rasgos (learning-to-rank con regresión logística o gradient boosting)

**Cómo funciona.** Cada Candidato es un vector de rasgos y un modelo chico aprende los pesos: nota del Juez (tres dimensiones), Jev, `rank_score` de la Pasada A, posición, duración, completitud (¿el candidato empieza en inicio de Línea y termina en fin de Línea?), densidad, flags de corte, y más adelante rasgos de audio (energía, risas de W29) y el mapa de calor (W28). Con pares o listas se entrena directamente para ordenar (pérdida por pares tipo RankNet, o LambdaMART en gradient boosting).

- **Datos mínimos:** ≥ 100 positivos en ≥ 15 videos para una logística de ~10 rasgos (§2.3). Gradient boosting pide más (ESTIMACIÓN: ≥ 500 etiquetas antes de que le gane a la logística).
- **Desarrollo:** 15–25 h de agente (rasgos + entrenamiento + validación por video + flag), 1 h de Agustín para revisar. Requiere W30 antes.
- **Entrenamiento:** US$0; segundos de CPU. Un archivo de pesos versionado.
- **Inferencia:** < 10 ms por job en CPU; US$0 más lo que cueste cada rasgo (Jev y audio no son gratis).
- **Pros:** interpretable (se ve cuánto pesa cada señal); barato; combina señales que hoy se tiran (E2: `rank_score` informa y no se usa); es el formato natural del Piso de calidad de W25. **Contras:** con los datos de hoy **pierde** contra la mejor señal sola (E3: 0,59 contra 0,68); las señales cambian por formato, así que hace falta un modelo por formato o rasgos de formato, lo que multiplica los datos necesarios.
- **Riesgo:** medio; se controla con validación por video y un gate.

### (c) Clasificador sobre embeddings

**Cómo funciona.** Se convierte el texto de cada Candidato en un vector (embedding) y una cabeza lineal aprende "de lo mejor sí/no". Por API (OpenAI `text-embedding-3-small`, DATO US$0,02 por millón de tokens; Gemini Embedding 2, DATO US$0,20) o local en CPU (un modelo multilingüe tipo e5-small/base; ESTIMACIÓN: 1–5 s para 30 textos en 6 vCPU).
- **Datos mínimos:** para tareas de tema, SetFit logra buena precisión con 8 por clase; para "gracia" o "remate", cientos (ESTIMACIÓN).
- **Desarrollo:** 10–15 h agente. **Entrenamiento:** US$0 (segundos). **Inferencia:** 30 × 300 tokens = 9 k tokens → **US$0,0002 por job** por API; US$0 local.
- **Pros:** barato; aporta un rasgo de "de qué habla" a (b). **Contras:** los embeddings capturan **tema**, no timing ni remate. Con pocos videos el modelo aprende "fútbol = bueno" o "hantavirus = bueno": es **fuga por video disfrazada de señal**. Solo tiene sentido como un rasgo más dentro de (b), validado por video.
- **Riesgo:** alto de métricas engañosas si se valida mezclando videos.

### (d) Fine-tuning de un LLM chico como Juez

**Cómo funciona.** Se entrena un modelo con pares (texto del Candidato → etiqueta o preferencia).
- **Hospedado (OpenAI):** `gpt-4.1-nano` se puede ajustar a **US$1,50 por millón de tokens de entrenamiento** e inferir a **US$0,20/0,80** (DATO, §4). ESTIMACIÓN: 500 ejemplos × 1,2 k tokens × 3 épocas ≈ 1,8 M tokens ≈ **US$2,7 por entrenamiento**; inferencia ~30 k tokens por job ≈ **US$0,007**. Obliga a llamar a OpenAI directo (hoy todo va por OpenRouter).
- **Abierto con LoRA:** Together cobra **US$0,34 por millón** para modelos de hasta 9B (mínimo US$4; DPO US$0,84) (DATO). El entrenamiento es barato; **servirlo no**: en el VPS sin GPU un modelo de 8–9B tarda minutos por job (ESTIMACIÓN: el prellenado de 30 k tokens en 6 vCPU es de varios minutos), y una GPU L4 en RunPod cuesta **US$0,44/h** (DATO), o sea ~US$320/mes encendida, o arranques en frío por job.
- **Datos mínimos:** ESTIMACIÓN ≥ 500–1 000 etiquetas de calidad; con menos, un fine-tune aprende el estilo de la salida, no el criterio.
- **Desarrollo:** 25–40 h agente + pipeline de datos + evaluación. **Pros:** a volumen, es el evaluador más barato por llamada y puede internalizar un gusto complejo. **Contras:** hoy es imposible (no hay datos); crea dependencia de un modelo que el proveedor puede retirar (ya pasó con Gemini 2.0, DATO `PROYECTO.md` §6); cada cambio de criterio exige reentrenar. **Riesgo:** alto.

### (e) Preferencias por pares (Bradley–Terry / Elo)

**Cómo funciona.** Dos usos distintos:
- **Como forma de etiquetar (recomendado):** Agustín o un canario comparan pares de Candidatos del mismo video; Bradley–Terry convierte los pares en un orden por video. Es la etiqueta que falta para R y se integra con el etiquetado activo.
- **Como rankeador en producción:** un LLM compara pares y un torneo arma el orden. 30 Candidatos → ~100–150 comparaciones (ordenamiento por comparación) × ~US$0,0004 (nano, ~1 k tokens) ≈ **US$0,05 por job** (ESTIMACIÓN); a2 (listas) da algo parecido por una décima parte.
- **Datos mínimos:** para ordenar 30 candidatos de un video con BT, ESTIMACIÓN ~60–100 pares bien elegidos. **Desarrollo:** 8–12 h (UI o markdown de pares + ajuste BT). **Inferencia:** US$0 si solo sirve para etiquetar.
- **Pros:** resuelve el problema de escala del anotador; las comparaciones son más rápidas y consistentes que una nota; es la etiqueta natural de "lo mejor del episodio". **Contras:** el orden es por video (no da un piso absoluto: para W25 sigue haciendo falta Posteable). **Riesgo:** bajo.

### (f) Juez multimodal que escucha el audio

**Cómo funciona.** Gemini recibe el audio del Candidato además del texto. **DATO nuevo para el spike de W24:** OpenRouter **sí acepta audio** en `google/gemini-3.5-flash`, `gemini-3-flash-preview` y `gemini-2.5-flash-lite` (API pública de modelos, 25-sep). Gemini cuenta **32 tokens por segundo de audio** (DATO, [docs de audio](https://ai.google.dev/gemini-api/docs/audio)).
- **Costo:** 60 s = 1 920 tokens de audio. Con `gemini-3-flash-preview` (US$1 por millón de audio): **US$0,0019 por Candidato**, US$0,06 los 30, US$0,02 si solo se escuchan los 10 mejores por texto. Con `gemini-3.5-flash` (US$3/M): US$0,17 los 30, **fuera del presupuesto**. Con `flash-lite` (US$0,30/M): US$0,017 los 30.
- **Tiempo:** hay que cortar el audio de cada Candidato; con W23 el audio del episodio ya está bajado: ffmpeg recorta en < 1 s por candidato; la llamada, 5–15 s en paralelo (ESTIMACIÓN).
- **Pros:** ataca el techo estructural del texto en humor (el chiste vive en la voz y la reacción, §2 del plan). **Contras:** caro si se aplica a todos; no está medido que mejore. **No es "aprendido"**: es un rasgo más; si se adopta, entra como rasgo de (b).
- **Riesgo:** medio. Ya está en el plan como spike de W24 con criterio de corte (+0,1 de precisión en charla).

### (g) Aprendizaje continuo automatizado

**Cómo funciona.** Un bucle programado junta las etiquetas nuevas, reentrena (b) o (d), evalúa contra el golden set y las Referencias, y si pasa los gates promueve el modelo detrás de un flag, con rollback automático si caen los guardarraíles.
- **Datos mínimos:** los de (b) **más un flujo continuo** de etiquetas (decenas por semana de más de un anotador). **Desarrollo:** 30–50 h de agente: almacenamiento de datasets versionados, entrenamiento, evaluación por video, registro de modelos, promoción y rollback, alertas. **Costo recurrente:** bajo en cómputo; alto en mantenimiento.
- **Pros:** es lo que convierte datos en ventaja con el tiempo. **Contras:** sin volumen de etiquetas el bucle reentrena sobre ruido; automatiza el error del 0,06 (optimizar contra una métrica que no mide lo que importa). **Riesgo:** muy alto hoy.
- **Lo que sí conviene hoy de (g):** la **disciplina**, no la automatización: dataset versionado (W30), evaluación por video (script de §7), y un "modelo" que hoy son pesos a mano o un rankeador elegido, detrás de un flag.

### (h) Otras opciones serias

| Opción | Qué es | Veredicto |
|---|---|---|
| **Jev (TypeSafe)** — ya en el pipeline | Un evaluador de terceros con score continuo y confianza | Ya es "comprar un evaluador". Ganó en entrevista y pierde en humor (H10); en E2b, AUC 0,50 contra la validación de Agustín (n = 10). Se sigue midiendo en W24. |
| **Mapa de calor de YouTube** (W28) | Réplicas reales de la audiencia por tramo | La señal externa más valiosa y la única gratis; propongo adelantar su **medición** (§8). |
| **Detector de risas** (W29) | Eventos de audio en CPU | Rasgo para (b) y para la Pasada A de charla; ya en el plan. |
| **Servicios de "puntaje viral" de terceros** (p. ej., el Virality Score de Opus) | Cajas negras | Opus documenta 4 dimensiones (Hook, Flow, Value, Trend) y **no dice** cómo lo entrena ([help.opus.pro](https://help.opus.pro/docs/article/virality-score), consultado el 25-sep). Nuestro análisis mostró que su juez casi no discrimina (32–37 sobre 40, DATO `ANALISIS_OPUS_CLIP.md`). No hay nada que comprar que resuelva el gusto en español rioplatense. |
| **Modelos de *highlight detection*** de video (académicos) | Aprenden de datos de video con consultas | Entrenados sobre video en inglés y con otra tarea; exigen GPU. Descartado para este tamaño. |
| **Destilar un LLM fuerte** (Sonnet 5 / Gemini Pro etiquetan, (b)/(c) aprenden) | Etiquetas en volumen a ~US$0,2–0,3 por video | Útil para **proponer** candidatos a validar (ya lo hace W19); como verdad, enseña el gusto del LLM (acuerdo ~70–75 %). |

### 3.1 Tabla de opciones

Horas y costos por job son ESTIMACIÓN con los precios DATO de §4; video de 60 min y 30 Candidatos.

| Opción | Datos mínimos | Desarrollo (agente / Agustín) | Entrenamiento | Inferencia por job | Tiempo en el VPS | Pros | Contras | Riesgo |
|---|---|---|---|---|---|---|---|---|
| a1 Rúbrica + ejemplos | 10–20 ej. + 40 para medir | 6–10 h / 1 h | US$0 | ~US$0,015 (caché) | 5–15 s (red) | ya factible; reversible | solo texto; no demostrable con n=40 | bajo |
| **a2 Juez por lista** | ídem | 12–20 h / 1 h | US$0 | **~US$0,004** | 10–30 s | relativo como la métrica norte; barato | sesgo de posición en la lista | bajo-medio |
| a3 DSPy | ≥ 200 (MIPROv2) | 15–25 h / 1 h | US$0,1–10 por corrida | igual que a1/a2 | igual | automatiza la búsqueda | sobreajuste con pocos datos | medio |
| **b Ranking sobre rasgos** | ≥ 100 positivos, ≥ 15 videos | 15–25 h / 1 h (+W30) | US$0, segundos | US$0 + rasgos | < 10 ms | combina señales que hoy se tiran; interpretable; es el Piso de W25 | hoy pierde contra una señal sola | medio |
| c Embeddings + cabeza | cientos por formato | 10–15 h / 0 h | US$0 | US$0,0002 (API) o US$0 local | 1–5 s CPU | barato | aprende tema, no gracia; fuga por video | alto |
| d Fine-tune hospedado | ≥ 500–1 000 | 25–40 h / 2 h | ~US$3 por corrida | ~US$0,007 | 5–15 s | barato a volumen | sin datos; dependencia del proveedor | alto |
| d' LoRA abierto | ídem | 30–50 h / 2 h | US$4+ (Together) | GPU: US$0,44/h (L4) o minutos de CPU | minutos en CPU | control total | infraestructura GPU | muy alto |
| **e Pares (etiquetar)** | 60–100 pares por video | 8–12 h / 1 h/sem | US$0 | US$0 | — | la etiqueta que falta para R | no da piso absoluto | bajo |
| e' Pares (rankear con LLM) | — | 10–15 h | US$0 | ~US$0,05 | 30–60 s | robusto a la escala | 10× a2 | medio |
| f Juez con audio | 40 para medir | 10–15 h / 1 h | US$0 | US$0,02 (top 10, 3-flash) – US$0,06 (30) | +10–20 s | ataca el techo del texto | caro para todos; sin medir | medio |
| g Bucle continuo | flujo de decenas/semana, > 1 anotador | 30–50 h / 2 h + mantenimiento | bajo | igual que el modelo | — | convierte datos en ventaja | automatiza ruido sin volumen | muy alto |

---

## 4. Costos reales (fuentes primarias, consultadas el 25-sep-2026)

| Proveedor · producto | Precio | Fuente |
|---|---|---|
| OpenAI · fine-tuning `gpt-4.1-nano` | entrenamiento US$1,50 por M tokens; inferencia US$0,20 entrada / US$0,80 salida | [developers.openai.com/api/docs/pricing](https://developers.openai.com/api/docs/pricing) |
| OpenAI · fine-tuning `gpt-4.1-mini` | US$5,00 por M; inferencia US$0,80 / US$3,20 | ídem |
| OpenAI · RFT `o4-mini` | US$100 por hora de entrenamiento; inferencia US$4 / US$16 | ídem |
| OpenAI · `gpt-5.4-nano` (Juez actual) | US$0,20 / US$1,25 por M | ídem y [OpenRouter API de modelos](https://openrouter.ai/api/v1/models) (mismo precio) |
| OpenAI · `gpt-5.4-mini` | US$0,75 / US$4,50 por M | ídem |
| OpenAI · Batch API | 50 % de descuento | ídem |
| OpenAI · `text-embedding-3-small` / `-large` | US$0,02 / US$0,13 por M | ídem |
| Google · Gemini 3.5 Flash | US$1,50 entrada / US$9,00 salida por M (batch: 0,75 / 4,50) | [ai.google.dev/gemini-api/docs/pricing](https://ai.google.dev/gemini-api/docs/pricing) |
| Google · Gemini 3 Flash Preview | texto US$0,50, **audio US$1,00**, salida US$3,00 por M | ídem |
| Google · Gemini 2.5 Flash-Lite | texto US$0,10, audio US$0,30, salida US$0,40 por M | ídem |
| Google · Gemini Embedding 2 | texto US$0,20 por M (batch 0,10); audio US$6,50 por M | ídem |
| Google · audio | 32 tokens por segundo; hasta 9,5 h por pedido | [ai.google.dev/gemini-api/docs/audio](https://ai.google.dev/gemini-api/docs/audio) |
| OpenRouter · Gemini 3.5 Flash | US$1,50 / US$9,00; **audio US$3,00 por M; acepta audio** | [openrouter.ai/api/v1/models](https://openrouter.ai/api/v1/models) |
| OpenRouter · Gemini 3 Flash Preview | US$0,50 / US$3,00; audio US$1,00; acepta audio | ídem |
| OpenRouter · Claude Sonnet 5 (borradores W19) | US$2 / US$10 por M | ídem |
| Groq · `whisper-large-v3-turbo` | US$0,04 por hora de audio | [console.groq.com/docs/models](https://console.groq.com/docs/models) |
| Groq · `gpt-oss-20b` | US$0,075 / US$0,30 por M | ídem |
| Together · LoRA SFT (Qwen3.5 0,8–9B, Llama 3.1 8B) | US$0,34 por M tokens de entrenamiento, mínimo US$4; DPO US$0,84 | [together.ai/pricing](https://www.together.ai/pricing) |
| RunPod · GPU a demanda | RTX 4090 US$0,34/h (community) – 0,74 (secure); L4 US$0,44–0,49; A100 80 GB US$1,19–1,59; H100 US$1,99–2,89 | [runpod.io/pricing](https://www.runpod.io/pricing) |
| Google Cloud · tuning de Gemini (Vertex) | no pude leer el precio en la página oficial; queda sin verificar | [cloud.google.com/vertex-ai/generative-ai/pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing) |

### 4.1 Costo por opción (ESTIMACIÓN con esos precios)

| Opción | Setup (única vez) | Mensual | Por job (60 min, 30 Candidatos) | ¿Entra en el margen de US$0,03–0,08? |
|---|---|---|---|---|
| a1 Rúbrica + ejemplos (nano) | US$0 + medición ~US$2 | US$0 | ~US$0,015 | sí |
| a2 Juez por lista (nano) | US$0 + medición ~US$2 | US$0 | ~US$0,004 | sí, sobrado |
| a2 con Gemini 3.5 Flash | ídem | US$0 | ~US$0,03 | justo |
| a3 DSPy | US$1–10 por optimización | — | como a1/a2 | sí |
| b Ranking sobre rasgos | US$0 | US$0 | US$0 (+ rasgos) | sí |
| c Embeddings (OpenAI small) | US$0 | US$0 | ~US$0,0002 | sí |
| d Fine-tune `gpt-4.1-nano` | ~US$3 por entrenamiento | reentrenos: ~US$3 c/u | ~US$0,007 | sí, pero sin datos |
| d' LoRA 8–9B + GPU | US$4+ entrenamiento | L4 24/7 ≈ **US$320**; o arranque en frío por job | — | **no** para la beta |
| e Etiquetar por pares | US$0 | horas humanas | US$0 | — |
| e' Torneo de pares con nano | US$0 | US$0 | ~US$0,05 | justo |
| f Audio, 10 mejores (3-flash) | US$0 | US$0 | ~US$0,02 | sí |
| f Audio, 30 (3.5-flash) | US$0 | US$0 | ~US$0,17 | **no** |
| Mapa de calor (W28) | US$0 | US$0 | US$0 (+ proxy) | sí |

**Lectura:** la plata de API **no es la restricción** de ninguna opción razonable salvo LoRA con GPU y audio con el modelo caro. La restricción es **etiquetas y horas de Agustín**.

---

## 5. Realidad del negocio

**Restricciones (DATO, `PLAN_MEJORA.md`, `PROYECTO.md`):**
- **Un dev con ~10 h/semana** más agentes; la revisión de Agustín es el cuello de botella (D12). Un modelo propio agrega algo que ningún agente puede hacer por él: **etiquetar**.
- **Beta cerrada, métrica "< 15 min en ≥ 90 % de los jobs".** Hoy los jobs de 77 min tardan 23–26 min (H12). Todo lo que sume tiempo por Candidato va en contra; por eso W23 va primero.
- **Economía unitaria:** crédito US$0,225, costo IA US$0,18–0,21 por job (H16); pasa a presupuesto por minuto (≤ US$0,003/min, A3). El margen para un evaluador nuevo es chico (§3) pero alcanza para las opciones baratas.
- **ICP:** podcasters y coaches en español; el caso difícil es charla/humor (A2). Es justo donde el texto solo tiene techo y donde los rankeadores fallan (H10).
- **Usuarios hoy: cero externos** (13 jobs, todos de Agustín). No hay flywheel de datos que proteger ni que aprovechar.

**Competencia.** Opus Clip documenta un Virality Score de 4 dimensiones y dice que su modelo reconoce patrones de contenido de alto rendimiento, pero no publica cómo lo entrena. Lo que sí vimos en sus datos (DATO, `ANALISIS_OPUS_CLIP.md`): un juez que casi no discrimina (32–37 sobre 40, todo A), **varias arquitecturas en paralelo** (`RAW`, `HPv2`, `TPv3`) con cambio automático de modelo, duración hasta 180 s, deduplicación por solape y un ranking relativo con curva. **Su ventaja visible está en el corte, la cantidad y la presentación, no en un evaluador superior.** Ninguno de sus 20 mejores coincidía con nuestros 5, pero eso se explica por cobertura y duración (lo que atacan W21/W22 y el tope de 120 s), no por un modelo aprendido.

**¿Cuándo tiene sentido un modelo propio?**
- **Sí**, cuando se cumplan las tres: (1) **volumen de etiquetas con más de un anotador** y de muchos videos (§2.3); (2) una **etiqueta que refleje valor real** (Referencias por pares o engagement real, no solo Posteable de Agustín); (3) la **alternativa comprada o de prompt dejó de mejorar** en el agregado de videos (curva plana en W24 y siguientes).
- **No**, mientras el error dominante sea de cobertura y corte (hoy: `recall_completo` 14 % en B60, `min_cuarto` 7 %; DATO), porque un rankeador perfecto no puede elegir un momento que la Pasada A no propuso ni completar una historia que el corte partió. **El techo del ranking hoy es la cobertura**: con 14 % de recall completo, aun un evaluador perfecto captura poco más de ~14 % de las Referencias A (el corte de W1 puede completar alguna parcial con el margen del segmento ancho, no mucho más).

**¿Qué ventaja competitiva daría?** A escala, un evaluador entrenado con el gusto de **creadores hispanohablantes** sobre **su propio contenido** (y, mejor, sobre el engagement de lo que publicaron) es difícil de copiar: Opus no tiene esas etiquetas en español rioplatense. Pero esa ventaja es **de datos, no de modelo**: los modelos son baratos y se reentrenan en horas; los datos llevan meses y usuarios. Hoy, la ventaja defendible está en otra parte: cortar historias completas en español y cubrir el episodio entero. **Lo que conviene construir ahora es la tubería de datos que haga posible la ventaja después (W30), no el modelo.**

---

## 6. Riesgos

| Riesgo | Qué pasaría | Evidencia hoy | Mitigación |
|---|---|---|---|
| **Sobreajuste a un anotador** | El modelo aprende el gusto de Agustín del 23-sep, que el 25-sep ya cambió | R16: posteable el 23, descartada el 25; 8 de 25 de la semilla descartadas | ≥ 2 anotadores con κ ≥ 0,4 antes de entrenar; etiquetas con fecha y reetiquetado de una muestra |
| **Sobreajuste a un video** | "Fútbol = bueno", "hantavirus = bueno" | 20 de 22 etiquetas en un video; 17 de 17 Referencias en otro | **Validación dejando videos afuera**, siempre; gates por número de videos, no de clips |
| **Fuga entre entrenamiento y prueba** | Un AUC que parece 0,9 y en producción es 0,6 | Los 3 reps de `seleccion` proponen el mismo momento hasta 3 veces (90 candidatos → 48 grupos en B60); dos jobs de XxoVRjTySsM comparten clips | Agrupar por video y por momento (como hace el script de §7); nunca partir al azar por clip |
| **Métricas engañosas** | Optimizar lo que no importa, como pasó con el Juez (0,06) | `judge_avg` subió en corridas que no mejoraron nada; `mayúscula inicial` pasó de 44,8 % a 93,1 % sin cambiar el comportamiento, solo la métrica | La vara es **captura de lo mejor** con Referencias validadas; el AUC de un modelo es una métrica intermedia |
| **Artefactos de la etiqueta** | Un candidato largo "toca" más Referencias por construcción | E2: duración AUC 0,75 para "tocar", 0,96 para "contener" | Controlar por duración (el script lo hace: residuo y terciles) |
| **Rango recortado** | Se etiqueta solo lo entregado; las señales parecen inútiles porque ya filtraron | E1: nada predice Posteable entre clips que el Juez ya eligió | Etiquetar Candidatos descartados (pares, Referencias) |
| **Deriva y mantenimiento** | Cambia la Pasada A (W21 ventanas, W22 formatos) y cambian las distribuciones de rasgos: el modelo queda viejo | El `rank_score` depende del prompt; `PROMPT_VERSION` cambia en cada línea | Reentrenar y remedir en cada cambio de Pasada A; pesos detrás de un flag; rollback = apagar el flag |
| **Dependencia del proveedor** | Un modelo ajustado desaparece | Gemini 2.0 apagado el 1-jun-2026 | Preferir (b) (pesos propios, CPU) sobre (d) |
| **Términos y privacidad** | Usar contenido de terceros para entrenar | Los términos de YouTube prohíben el acceso automatizado ("robots, botnets o scrapers") y descargar salvo autorización ([términos](https://www.youtube.com/static?template=terms)); ya operamos en esa zona gris para descargar. Entrenar sobre transcripts de videos de usuarios es tratamiento de datos | Entrenar solo con rasgos derivados y etiquetas, no con el contenido crudo; consentimiento explícito en los términos del producto antes de entrenar con jobs de usuarios; el mapa de calor por scraping es la misma zona gris que la descarga |
| **Costo de oportunidad** | Horas de agente y de revisión que no van a W21–W25 | El techo del ranking es la cobertura (14 % recall completo) | Nada de modelo propio antes de G2; W30 es chico y alimenta a W24/W25 |

---

## 7. Experimento mínimo (corrido, US$0)

**Script:** [`worker/eval/experimentos/ml_factibilidad.py`](../worker/eval/experimentos/ml_factibilidad.py). **Salida:** [`worker/eval/runs/2026-09-25-ml-factibilidad.json`](../worker/eval/runs/2026-09-25-ml-factibilidad.json). Solo numpy; lee la base de la beta en solo lectura; bootstrap de 4 000 remuestras con semilla fija. Reproducir desde `worker/`: `python eval/experimentos/ml_factibilidad.py --json`.

**Diseño contra la fuga:** no se entrena nada sobre el mismo video en que se evalúa. E1, E2 y E2b miden **señales que ya existen** (sin ajustar parámetros), así que no hay fuga posible; el IC de E2 remuestrea **grupos** de candidatos que se solapan > 50 % (el mismo momento propuesto en varias repeticiones cuenta una vez). E3, el único que entrena, deja un video entero afuera en cada vuelta.

### E1 · ¿Qué predice Posteable? (22 clips, 13 posteables, 1 anotador, 2 videos)

| Señal | AUC | IC 95 % |
|---|---|---|
| Juez, suma | 0,61 | 0,37–0,84 |
| Juez, hook | 0,50 | 0,27–0,75 |
| Juez, retention | 0,65 | 0,44–0,84 |
| Juez, shareability | 0,53 | 0,34–0,73 |
| Pasada A, suma de su auto-score | 0,51 | 0,25–0,78 |
| Duración | 0,57 | 0,31–0,82 |
| Posición en el video | 0,45 | 0,21–0,71 |
| Palabras por segundo | 0,63 | 0,35–0,89 |
| Cantidad de flags | 0,59 | 0,36–0,81 |

**Lectura:** ninguna señal se distingue del azar; todos los intervalos cruzan 0,5 y miden ~0,45 de ancho. Confirma W12 (correlación 0,06) con una métrica de ranking. No se puede entrenar nada con esto, y tampoco **descartar** que el Juez sirva algo: con n = 22 no se sabe.

### E2 · ¿Qué separa "de lo mejor" en B60? (Referencias validadas por Agustín)

Datos: los 90 Candidatos de las 3 repeticiones del baseline `seleccion` de B60 (48 grupos únicos). Etiqueta: **mejor** = cubre ≥ 50 % del Núcleo de una Referencia validada (25); **no** = no toca ninguna (43) o solo una descartada por Agustín (14); se excluyen 8 que solo tocan borradores sin validar. Tasa base: 30,5 %.

| Señal | AUC | IC 95 % (por grupos) |
|---|---|---|
| **`rank_score` de la Pasada A** | **0,82** | **0,68–0,95** |
| `rank_score` sin el efecto de la duración (residuo) | 0,78 | 0,62–0,93 |
| Duración | 0,75 | 0,55–0,91 |
| Posición | 0,47 | 0,27–0,65 |
| Palabras por segundo | 0,54 | 0,36–0,71 |
| Preguntas por minuto | 0,44 | 0,26–0,62 |
| Exclamaciones por minuto | 0,52 | 0,41–0,65 |
| Líneas por minuto (cambios de turno, aprox.) | 0,47 | 0,25–0,69 |

- Correlación `rank_score`–duración: 0,10. Por terciles de duración, el AUC del `rank_score` es 0,93 / 0,86 / 0,79 (n = 25 / 28 / 29): la señal no es un efecto de la duración.
- Precisión del top-k por `rank_score` en cada repetición: top-5 = 4, 3 y 3 de 5 (**67 %** contra 30 % de base); top-10 = 5, 5 y 6 de 10 (53 %).
- **Etiqueta estricta** ("contiene completo el Núcleo de una A", solo 5 positivos): `rank_score` 0,51 [0,32–0,90]; **duración 0,96** [0,90–0,99]. **Qué candidatos contienen la historia completa lo decide cuánto duran, no la nota.**

**Lectura:** la Pasada A sabe algo sobre qué momentos son buenos en este video, y hoy **esa información no llega al ranking final** (`score_candidate` usa solo Juez/Jev; el auto-score "nunca gana"). Esa regla se decidió en W2 (causa C4) cuando la Pasada A trabajaba con captions y se autoevaluaba sobre el tramo que proponía; no se volvió a medir con Líneas de Whisper. **Es un solo video de charla**: es una hipótesis para W24, que tiene que medirse con los 6 videos que valide G0. La etiqueta estricta confirma el diagnóstico del plan: completar historias es trabajo de corte (W21/W22).

### E2b · Juez y Jev contra la validación de Agustín (Anexo A.2)

Las 10 Referencias de la tabla A.2 de `PLAN_MEJORA.md` tienen nota del Juez y de Jev; Agustín conservó 8 y descartó 2 (Bilardistas, Ledley King). AUC del Juez **0,66**, de Jev **0,50**, sobre **16 pares** comparables. Con 2 negativos no se puede concluir nada; lo muestro porque es literalmente todo lo que hay de Juez y Jev contra la vara validada.

### E3 · ¿Aprender combinaciones sirve con los datos de hoy? (4 videos, etiquetas de borrador)

Datos: 230 Candidatos con nota del Juez en `analysis_cache` (v8 y v9, `whisper_full`) de 4 videos; etiqueta: toca un momento del **borrador** de Referencias de Sonnet 5 (**sin validar**; 86 positivos).

| Señal (agrupada por video) | AUC | IC 95 % |
|---|---|---|
| Juez, suma | **0,68** | 0,61–0,74 |
| `w2_score` (Juez − penalizaciones) | 0,61 | 0,49–0,67 |
| `rank_score` | 0,58 | 0,39–0,73 |
| Posición | 0,59 | 0,45–0,68 |
| Duración | 0,57 | 0,46–0,65 |
| **Logística con las 5, dejando un video afuera** | **0,59** | 0,55–0,66 |

Por video, el AUC del `rank_score` va de 0,43 (MaXgAEI4Vm8) a 0,77 (XxoVRjTySsM), y el del Juez de 0,59 a 0,74. **La combinación aprendida pierde contra la mejor señal sola**: los pesos que sirven en tres videos no sirven en el cuarto. Es lo que se espera con 4 grupos, y es la evidencia más directa de que **hoy entrenar empeora**. (Con 4 videos, el IC por bootstrap de videos es grueso; la dirección es lo que importa.)

### E4 · Cuánto haría falta

Ver §2.3. Resumen: **~70 etiquetas para saber si una señal sirve; ~255 para demostrar que un rankeador nuevo mejora +0,10; ≥ 100 positivos en ≥ 15 videos para entrenar una combinación**. Hoy hay 22 etiquetas en 2 videos y 17 positivos validados en 1.

### Experimento que sí conviene pagar después (diseñado, **no corrido**)

**Objetivo:** decidir en W24 entre Juez actual, Jev, `rank_score`, a1 y a2, contra Referencias validadas.
- **Datos:** los 7 videos del golden set con Referencias validadas (G0), los ~30 Candidatos por video de 3 repeticiones de `seleccion` (ya pagadas).
- **Llamadas:** Juez sobre el texto de las Líneas de cada Candidato (7 × 90 × ~1 k tokens con nano ≈ **US$0,25**); a2 por lista (7 × 3 × ~13 k tokens ≈ **US$0,06** con nano, **~US$0,50** con Gemini 3.5 Flash); Jev (costo de TypeSafe, a confirmar).
- **Total estimado: < US$2.** Necesita recargar OpenRouter (hoy ~US$1,37 compartido con producción: **no se corrió**).
- **Métrica:** `precision_ref@5`, `recall_completo@top-k` y AUC contra Referencias, por video y macro, con IC por grupos; decide la mayoría de videos, como pide §11 del plan.

---

## 8. Conclusión y recomendación

### 8.1 La decisión

**Todavía no.** No hay que construir un evaluador aprendido en 2026-Q4: no hay datos para entrenarlo ni para validarlo, el techo del ranking hoy lo pone la cobertura, y la ventaja competitiva que daría es de datos que todavía no existen. **Sí** hay que:

1. **Empezar ya a registrar los datos** que harían posible un modelo después (W30). Es barato, y cada job que corre sin registrar es una etiqueta futura perdida.
2. **Usar el aprendizaje barato dentro de W24:** combinar y calibrar lo que existe (`rank_score` como señal; Juez por lista con la rúbrica y ejemplos de Agustín), medido contra Referencias validadas.
3. **Cambiar cómo se etiqueta:** separar "de lo mejor" (por pares, sobre Candidatos) de Posteable (galería), porque son preguntas distintas.

### 8.2 Cambios propuestos al plan

| Qué | Ola | Tipo | Detalle |
|---|---|---|---|
| **W30 · Registro para aprender** (nueva, esfuerzo S, agente *eval* o *ranking*) | 1 (en paralelo con W21) | Instrumentación, sin cambio de comportamiento | (1) persistir por Candidato: Juez (3 dimensiones), **Jev (score y confianza)**, `rank_score`, posición, duración, inicio/fin en Línea, flags, `w2_score`, elegido/descartado, `job_id`, `PROMPT_VERSION` y modelos, **sin pisar** la fila anterior (tabla nueva por migración de Supabase, o un JSONL por job en R2); (2) exportador del dataset versionado con el script de §7 como evaluación por video. Toca el contrato de esquema (`PROYECTO.md` §7) si va por tabla. |
| **W24, ampliado** | 2 | Medición + flag | Agregar a las variantes: `rank_score` como señal (combinación con pesos fijos elegidos contra Referencias, no entrenados), **a1** (rúbrica de Agustín + 6–10 ejemplos validados) y **a2** (Juez por lista). Decide la mayoría de videos con Referencias validadas; el experimento de §7 (< US$2) es su medición previa. |
| **Etiquetado por pares de "lo mejor"** | 2 (junto a W24) | Proceso | Markdown o vista de galería con pares de Candidatos del mismo video, elegidos por desacuerdo entre rankeadores (etiquetado activo). Meta: 60–100 pares por video en 6 videos. Suma a la hora semanal de A4. |
| **W25, aclaración** | 2 | Doc | El Piso de calidad calibrado con n ≈ 40 es **provisional** (IC de ±0,15 en la precisión); se recalibra al llegar a ≥ 60 clips sobre el piso. |
| **W28 (mapa de calor), medición adelantada** | 1–2 (solo medir) | Spike | Leer el mapa de calor de B60 y de los videos con Referencias y medir sus picos contra ellas cuesta US$0 y minutos. Si coincide, es la única etiqueta externa gratis y un rasgo para (b). |
| **W31 · Evaluador aprendido v1 (opción b)** (nueva, **condicionada al Gate L1**) | post-beta (≥ 2027-Q1) | Modelo | Logística por formato sobre los rasgos de W30, detrás de `RANKER=aprendido`, con evaluación dejando videos afuera. |
| **Término nuevo para `CONTEXT.md`** (cuando entre W30) | 1 | Doc | **Evaluador aprendido:** Rankeador cuyos pesos salen de etiquetas y no de un prompt. |

**Fuera del plan (qué NO hacer):** fine-tuning de un LLM (d, d'), GPU alquilada, bucle de reentrenamiento automático (g), embeddings como clasificador principal (c), y cualquier promesa comercial de "IA entrenada con tu contenido" antes del Gate L2.

### 8.3 Gates de decisión

| Gate | Condición (todas) | Si pasa | Si no |
|---|---|---|---|
| **L0** (fin de la Ola 2, ~14-oct) | W30 registrando en producción; Referencias validadas en ≥ 6 videos; ≥ 40 etiquetas Posteable (G2) | W24 decide rankeador con a1/a2/`rank_score` medidos | Seguir juntando; W24 decide con lo que haya, como dice el plan |
| **L1** (primer modelo aprendido) | ≥ 300 Candidatos etiquetados (pares o Referencias), ≥ 100 positivos, **≥ 15 videos**, ≥ 3 formatos; **≥ 2 anotadores** con κ ≥ 0,4 en una muestra común; y una logística dejando videos afuera le gana al mejor rankeador solo por **≥ 0,05 de AUC** con IC de la diferencia que no cruza 0 | W31 entra detrás de un flag; se adopta si mueve la captura de lo mejor ≥ 10 pp en el agregado sin bajar la precisión | No se entrena; se sigue con el rankeador elegido en W24 |
| **L2** (modelo más grande: embeddings o fine-tune) | L1 superado; ≥ 1 000 etiquetas o una señal de engagement real de clips publicados; la curva de aprendizaje de (b) sigue subiendo al duplicar datos; el costo por job del rankeador vigente es el cuello de botella | Evaluar (c) como rasgo y (d) hospedado | Quedarse con (b) |

### 8.4 Presupuesto y cronograma realistas

| Periodo | Qué | Agente | Agustín | API |
|---|---|---|---|---|
| Ola 1 (1–7 oct) | W30 (registro) + medición del mapa de calor | 8–12 h | 0,5 h de revisión | US$0 |
| Ola 2 (8–14 oct) | W24 ampliado (a1, a2, `rank_score`) + experimento de §7 | 12–20 h | 1 h (rúbrica y ejemplos) + etiquetado de G2 | < US$2 medición; +US$0,004–0,015 por job si se adopta |
| Oct–dic | Etiquetado por pares (60–100 por video) + canarios | 3–5 h (vista o markdown de pares) | 1 h/semana (A4) | US$0 |
| ≥ enero 2027 | Gate L1 → W31 si pasa | 15–25 h | 2 h | US$0 de entrenamiento |
| **Total 2026** | | **~25–35 h de agente** | **~1 h/semana ya comprometida + ~2 h** | **< US$15** |

**Qué tendría que pasar para que la respuesta sea "sí, entrenemos":** usuarios reales que generen etiquetas o señales (o 1–2 canarios constantes), una etiqueta de "lo mejor" por pares en ≥ 15 videos, y un W24 que muestre que ningún prompt ni combinación fija sube más. Con eso, el primer modelo es chico, barato y corre en el VPS en milisegundos. Sin eso, cualquier modelo es el gusto de una persona sobre dos videos.
