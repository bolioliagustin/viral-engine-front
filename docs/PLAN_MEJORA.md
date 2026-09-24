# Plan de mejora total — que el worker vea el episodio entero

**Fecha:** 23 de septiembre de 2026 · **Estado:** aprobado por Agustín el 23-sep-2026 (sí a A1–A6); Ola 0 en curso · **Continúa:** [`PLAN_CALIDAD.md`](PLAN_CALIDAD.md) (líneas W0–W17) · **Vocabulario:** [`../CONTEXT.md`](../CONTEXT.md) · **Briefs para agentes:** [`briefs/`](briefs/)

---

## 0. Resumen ejecutivo

El job `fb287cba` (programa de Vorterix de 111 min, tono sarcástico) entregó **5 clips flojos en 31 min**. El diagnóstico de ese día encontró una causa puntual y cuatro estructurales:

1. **Caché envenenada (bug):** el job reutilizó el transcript y el análisis de una corrida anterior hecha sobre el doblaje en inglés, y ningún candidato pudo anclarse al audio real.
2. **La selección no ve el episodio entero:** con el transcript correcto, la Pasada A de `main` puso los 30 candidatos en los primeros 51 minutos. La segunda hora no tuvo ni uno.
3. **Parte y recorta las historias:** propone la mitad de una anécdota, o la corta antes del remate.
4. **El ranking mide "dato", no gracia, y el umbral es absoluto:** aun con todo sano, solo 5 de 30 candidatos superan el umbral. Lo más gracioso queda último.
5. **Encuadre y tiempo:** el 16:9 queda en una franja chica o partido mostrando al público, y un video largo tarda más del doble de la meta de la beta (< 15 min).

**La apuesta:** medir cada etapa del embudo por separado (cobertura → ranking → corte → presentación → entrega) contra una verdad humana barata de consultar (las **Referencias**), y atacar en cinco olas. Arrancamos por lo que decide qué momentos llegan al usuario; lo que decide cómo se ven va después.

**Qué significa "1000 %"** (definición de Agustín, 23-sep): ser **el mejor sistema de clips del mundo**. No se trata de entregar más clips, sino de que cada job **capture lo mejor del video entero**: que no quede afuera ningún momento excelente y que no entre ninguno flojo, bien cortado y bien presentado, aprovechando todo lo que el video ofrece. La cantidad sale del contenido, no de una cuota. Se mide con:
- **Captura de lo mejor:** qué parte de las Referencias A llega entregada como clip completo y posteable. Hoy es 1 de 15 en `fb287cba`.
- **Precisión:** qué parte de lo entregado es posteable.
- **Comparación a ciegas contra Opus Clip** sobre los mismos videos, el líder del mercado.

**Cómo:** 12 líneas (W18–W29) en 5 olas de ~1 semana, con un gate numérico al final de cada ola y a lo sumo 3 agentes en paralelo. Tu tiempo es el cuello de botella: ~10 h/semana entre revisar PRs, etiquetar clips y decidir en los gates.

---

## 1. Diagnóstico con evidencia

Datos del 23-sep-2026. Los experimentos corrieron con el código de `main` (`842c138`) contra una base desconectada; no se tocó producción. El detalle está en el Anexo A.

| # | Hallazgo | Evidencia | Línea |
|---|---|---|---|
| H1 | `analysis_cache` reutiliza un análisis aunque el transcript haya cambiado: la clave es (video, modelo, tono, versión de prompt), sin nada del transcript. | `fb287cba` usó el análisis de las 13:40, hecho sobre el doblaje: resumen, 30 hooks y frases de verificación en inglés. | W18 |
| H2 | `get_cached_transcript` usa la copia local de `/app/downloads` cuando Supabase no tiene la fila. Esa carpeta es el volumen `worker_downloads`, que sobrevive a los deploys. | No existe fila `B60BHDNFNxM:whisper_full:…` en Supabase, pero el job igual cargó un transcript en inglés (evento `transcript_full`, cache hit). | W18 |
| H3 | Nada frena un job en el que ningún candidato se ancla. | 30/30 candidatos sin anclar, 58 llamadas a Whisper (28 con margen extendido), US$0,178. Se entregó igual. | W18 |
| H4 | El piso mínimo de entrega rellena con candidatos rotos. | 0 de 30 pasaron el umbral (máximo 4,55 contra 15). Los 5 entregados salieron del piso y los 5 tienen `hook_not_found` y `payoff_not_found`. | W18, W25 |
| H5 | Cuando el anclaje falla, el refinamiento legacy recorta el inicio si las 3 primeras palabras no coinciden con el hook. | A los clips 4 y 5 les sacó 12,5 s y 17 s del planteo ("Maradona nos llama y dice: pongan el grabador"). Tu etiqueta: "termina cortado". | W18 |
| H6 | La Pasada A devuelve los candidatos en orden cronológico y se le acaba el cupo antes del final. | Transcript real de B60BHDNFNxM: 30 candidatos repartidos por cuartos [17, 13, 0, 0]. En los videos de 1,5–2 h cacheados: 57 % en el primer cuarto y 4 % en el último. | W21 |
| H7 | Mismo modelo y mismo prompt, pero por ventanas de ~28 min, la segunda hora pasa de 0 a 16 candidatos. | Aparecen 6 de 6 Referencias de esa hora. De las 15 Referencias A: completas pasan de 3 a 10, y ausentes de 7 a 4 (una se perdió en el borde entre ventanas porque no había solape). | W21 |
| H8 | Parte y recorta historias. | Jagger salió en dos candidatos; Peñarol sin el remate; Oso Yogi con 15 s de 80; Nápoles sin el planteo. Las duraciones propuestas se amontonan en 30–50 s. | W21, W22 |
| H9 | La Categoría (podcast/business) y su foco son de entrevista. El clasificador lee solo los primeros 1500 caracteres y el tono no llega a la selección. | Clasificó "PODCAST" un programa de humor con 5 panelistas y público. El foco pide "pregunta → respuesta sorprendente, revelación del invitado". | W22 |
| H10 | Juez y Jev anclan "compartir" en "dato útil / contraintuitivo / que da estatus". | Jev: Caniggia 8,8 (el juez le dio 18), Oso Yogi 8,2, Ledley King 10,5, "asociación ilícita" 11,4. Con `RANKER=jev`, manda Jev. | W24 |
| H11 | El umbral absoluto de 15 depende del formato y del rankeador. | Simulación con el pipeline sano: solo 5 de 30 lo superan (21,8 / 19,1 / 18,6 / 16,7 / 15,9), así que se entrega el piso. | W25 |
| H12 | La evaluación de candidatos es secuencial: se baja, transcribe, juzga y rankea uno por uno. | ~55 s por candidato → 27–29 min para 30. Jobs sanos de 77 min tardaron 23–26 min. La meta de la beta es < 15 min. | W23 |
| H13 | El encuadre en un estudio con varias cámaras falla. | Clips 1 y 5 en Fit: dos tercios de pantalla borrosos. Clips 2 y 3 en Split: a veces público o espaldas en lugar de quien habla. El zócalo de la fuente queda cortado. | W26 |
| H14 | La regla de fidelidad del overlay (W6) lo vuelve literal. | "DOMINGO EL MIÉRCOLES ARGENTINO", "ESTÁBAMOS RECORDANDO LA TELEVISIÓN". | W27 |
| H15 | El tope de 90 min no se aplica: el backend lo trata "fail open" si no puede leer la duración. | Entró un video de 111 min. | W20 |
| H16 | La economía por job no cierra en videos largos. | Costo IA US$0,18–0,21 por job contra US$0,225 de ingreso por crédito. | A3 |
| H17 | Las mediciones escriben en la caché de análisis de **producción** (el `.env` local apunta a la base de la beta). | `analysis_cache` tiene filas `v9` del 20-sep para 3 videos del golden set; `v9` no existe en `main`, así que las escribió código experimental. | §4.1, W18, W19 |

**Lo que sí funciona** (y no hay que romper): en los jobs sanos del 21-sep el anclaje W1 dejó 28/28 clips `line_aligned`, con 0 `hook_not_found`. Con el transcript correcto, la primera mitad de B60BHDNFNxM produce varios candidatos muy buenos (Cuti/Haaland, bilardistas, periodista). El W17 corrigió de verdad la pista de audio.

---

## 2. Suposiciones puestas a prueba

| Suposición | Qué muestran los datos | Postura desde hoy | Cómo se verifica |
|---|---|---|---|
| "El fallo de `fb287cba` fue del modelo." | Fue la caché (H1–H3), y debajo hay problemas de selección y ranking (H6–H11). | Primero bugs y medición, después modelos. | Gate G0. |
| "Con la caché arreglada, este video sale bien." | La simulación sana entrega igual 5 clips, ninguno de la segunda hora. | Hay que arreglar la selección y la entrega, no solo la caché. | Tier `seleccion` (W19). |
| "Pedir 30 candidatos da cobertura." | 30 en una pasada se concentran en la primera mitad. | La cobertura sale de las ventanas, no de la cantidad. | `min_cuarto` (W19, W21). |
| "Las ventanas son la solución." | Un solo experimento, un video, una corrida. | Es la mejor hipótesis, no un hecho. | 3 repeticiones × ≥ 6 videos (G1). |
| "Juez/Jev miden calidad." | El juez correlaciona 0,06 con Posteable. Jev ganó en una entrevista informativa. En humor, ninguno de los dos está medido. | El rankeador se elige **por formato**, con etiquetas. | `calibracion.py` por formato (W24). |
| "Un umbral de 15 sirve para todo video." | La distribución de notas cambia con el formato y el rankeador. | Entrega relativa a la duración, con un piso de calidad calibrado. | Simulación y etiquetas (W25). |
| "El tono elige el tipo de momento." | La Pasada A no recibe el tono. | Bien que sea así: el tono queda para el copy; el **Formato** decide la selección. | Documentado en `CONTEXT.md` (W22). |
| "Mis 15 Referencias de B60 son la verdad." | Son el criterio de un solo anotador (Claude). | Son un borrador: las valida Agustín y, si se puede, un canario. | Campo `validado_por` (W19). |
| "Evaluar sobre el texto del transcript equivale a evaluar el clip." | No medido. | Se adopta solo si la correlación texto↔clip es ρ ≥ 0,8. | Medición previa de W23. |
| "Los usuarios quieren más clips." | Opus entrega 42. Tu queja fue "solo 5". Más clips flojos erosionan la confianza. | Más clips, ordenados por score, con un piso de calidad y 0 rotos. | Posteables por job y precisión (G2). |
| "El ICP son podcasters y coaches, así que el humor no importa." | El caso que falló es un programa de humor/stream, y es el mercado de clips más grande del Río de la Plata. | El ICP no cambia, pero **Charla** entra como formato de primera clase en el golden set. | Decisión A2. |
| "US$0,15 por job es el techo de costo." | Ya estamos en 0,18–0,21. El precio por job no escala con la duración. | El presupuesto pasa a ser **por minuto de video** (≤ US$0,003/min). | Decisión A3. |
| "Solo texto alcanza." | En humor, el chiste vive en la voz, el timing y la reacción. | Techo estructural: las señales no textuales son apuestas medidas en la Ola 4. | Spikes W28 y W29. |

**Correcciones a lo que dije el 23-sep:** (1) el job `fb287cba` tenía 75 min de timeout dinámico (addendum W14), así que no estuvo "a segundos" de fallar. El problema real de tiempo es la meta de < 15 min de la beta. (2) Con el transcript correcto el modelo sí encuentra chistes en la primera mitad; lo que falla es la cobertura, el corte y el ranking.

---

## 3. Qué es "1000 %": métricas y metas

**Métrica norte: captura de lo mejor** = fracción de las Referencias A del video (validadas por un humano) que llegan **entregadas como clip que contiene su núcleo completo y que se etiqueta posteable**. Mide exactamente la definición de "1000 %": lo mejor del video entero, bien cortado. Entregar más clips no la mejora; encontrar y cortar bien lo excelente, sí.

**Guardarraíles:** precisión (posteable_rate de lo entregado) ≥ 80 % en la meta final, **0 clips rotos entregados**, tiempo p90 dentro de la meta y costo IA ≤ US$0,003 por minuto de video. "Posteables por job" queda como indicador, no como objetivo.

**Vara externa — "el mejor del mundo":** comparación **a ciegas** contra Opus Clip sobre 3–4 videos del golden set. Agustín ve pares de clips del mismo momento o tema sin saber de qué sistema viene cada uno y elige el que publicaría. Métrica: porcentaje de veces que se elige el nuestro (win rate). Requiere pasar esos videos por Opus una vez (Ola 2).

**Métricas por etapa** (cada línea mueve una; se miden así y no a ojo):

| Etapa | Métrica | Cómo | Dónde |
|---|---|---|---|
| **Norte** | `captura_de_lo_mejor`: Referencias A cuyo núcleo está contenido en un clip entregado y etiquetado posteable | Jobs reales etiquetados + Referencias | W19 |
| Cobertura | `recall_completo@candidatos`: Referencias A cuyo núcleo queda entero dentro de un candidato (±2 s) | Tier `seleccion` | W19 |
| Cobertura | `min_cuarto`: porcentaje de candidatos del cuarto con menos candidatos | Tier `seleccion` | W19 |
| Cobertura | `historias_partidas`: Referencias cuyo núcleo quedó repartido en dos candidatos sin que ninguno lo contenga | Tier `seleccion` | W19 |
| Ranking | `precision@k` contra Posteable; `recall@entregados` contra Referencias | `calibracion.py`, tier `e2e` | W19, W24 |
| Corte | Motivos "arranca_mal" y "termina_mal" / total de etiquetas | `clip_feedback` | W18, W22 |
| Presentación | Motivos "se_ve_mal", "copy_malo" y "subtitulos_mal" / total | `clip_feedback` | W26, W27 |
| Entrega | Clips entregados por minuto de video; tiempo p50/p90; US$ por minuto | `jobs`, `usage_summary` | W23, W25 |

| Métrica | Hoy | Meta al cerrar la Ola 2 | Meta final |
|---|---|---|---|
| **Captura de lo mejor** (Referencias A entregadas completas y posteables) | 1 de 15 (7 %) en `fb287cba`; 3 de 15 (20 %) en la simulación sana | ≥ 60 % | **≥ 80 %** |
| Precisión (posteable_rate) | 50 % en `fb287cba` (n=2); 50–75 % en XxoVRjTySsM | ≥ 75 % | ≥ 85 % |
| Win rate a ciegas contra Opus Clip | sin medir | ≥ 50 % (paridad) al cerrar la Ola 3 | **≥ 65 %** |
| `recall_completo@candidatos` (Referencias A) | 20 % (B60, una pasada) | ≥ 70 % | ≥ 90 % |
| `min_cuarto` (videos > 60 min) | 0 % (B60) | ≥ 15 % | ≥ 20 % |
| Clips rotos entregados | 5 de 5 (`fb287cba`) | 0 | 0 |
| Tiempo p90 con video de 60 min | 23–26 min (con videos de 77 min) | ≤ 15 min | ≤ 12 min |
| Tiempo p90 con video de 120 min | 31 min | ≤ 25 min | ≤ 20 min |
| Costo IA por minuto de video | US$0,0016–0,0025 | ≤ 0,003 | ≤ 0,003 |

---

## 4. Estrategia: cinco principios

1. **Medir por etapa, offline cuando se pueda.** Las Referencias permiten medir la selección en minutos y por centavos, sin renderizar ni etiquetar clips. Etiquetar clips queda para lo que sí lo necesita: el ranking y la presentación.
2. **Una variable por vez, detrás de un flag.** Todo cambio de comportamiento entra apagado. El gate decide si se prende en el VPS: si algo sale mal, se apaga el flag sin redeploy. Los bugs (W18) entran prendidos.
3. **Primero qué ve el usuario, después cómo se ve.** Un momento flojo bien encuadrado sigue siendo flojo. El orden es cobertura → ranking/entrega → corte → presentación.
4. **El tiempo y el costo son calidad.** La beta se mide en "< 15 min". Evaluar en texto primero (W23) libera el tiempo y el presupuesto que cuestan las demás mejoras.
5. **La etiqueta humana manda.** Los proxies (juez, Jev, Referencias) se calibran contra ella por formato. Tu hora de etiquetado por semana es el insumo más valioso del plan.

### 4.1 Reglas de medición (valen para todos los agentes)

El `.env` de la raíz apunta a la base de la **beta**. Una medición que escribe en sus cachés puede servirle un análisis experimental a un job real, que es la misma clase de bug que `fb287cba`. Por eso:

- Las mediciones **leen** transcripts cacheados (solo lectura). **Nunca** leen ni escriben la caché de análisis y nunca escriben ninguna caché de producción. El tier `seleccion` lo trae por default (W19); el tier `e2e` se adapta en W19.
- Todo flag que cambie la Pasada A (Ventanas, Formatos) forma parte de la **versión efectiva de la caché**, como hizo W4 con la fuente del transcript. Así, un análisis calculado con el flag prendido nunca se sirve con el flag apagado, ni al revés.
- Nadie corre `worker/main.py` (el loop de la cola) apuntado a la base de la beta: le roba jobs al VPS (`AGENTS.md`).

---

## 5. Decisiones

### 5.1 Tomadas (las ejecuto salvo que digas lo contrario)

| # | Decisión | Por qué |
|---|---|---|
| D1 | La métrica norte es la **captura de lo mejor**, con los guardarraíles de §3 y la vara externa contra Opus Clip. | Es tu definición de "1000 %": lo mejor del video entero, no más cantidad. `judge_avg` ya demostró no servir (0,06). |
| D2 | Se mide por etapa; la selección, offline contra Referencias. | Iterar la Pasada A con jobs reales cuesta 30 min y etiquetas por intento. |
| D3 | La caché se arregla primero y se purga hoy (W18, W20). | Sin eso, cualquier medición puede estar leyendo basura vieja. |
| D4 | La primera palanca de selección son las **ventanas con solape**, no un modelo más caro. | Es la evidencia más fuerte (H7), cuesta lo mismo, y un modelo más grande no arregla el sesgo de posición. |
| D5 | La **Categoría** se reemplaza por un **Formato** de cuatro valores: entrevista, charla, monólogo y clase. El tono queda solo para el copy. | H9. "Charla" cubre mesas, streams y humor. |
| D6 | Evaluar en texto primero **si y solo si** la correlación texto↔clip es ρ ≥ 0,8 y el top-12 se superpone ≥ 80 %. | Es el mayor ahorro de tiempo y costo, pero hay que medirlo antes. |
| D7 | Entrega **por calidad calibrada**: se entrega todo lo que supera un piso que predice "posteable" (y nada que no), **nunca rotos**, con un tope solo por costo. La cantidad la decide el contenido. | H4, H11 y tu definición de "1000 %". |
| D8 | `RANKER=jev` sigue hasta que W24 decida por formato con etiquetas. | Cambiarlo sin datos repite el error del juez. |
| D9 | El golden set crece a ≥ 8 videos en 4 formatos, cada uno con Referencias validadas. B60BHDNFNxM entra como `charla_humor_01`. | El formato que falló no estaba en el golden set. |
| D10 | Encuadre y copy (Ola 3) van después de la selección y el ranking. | Principio 3. |
| D11 | Mapa de calor, risas y diarización son apuestas medidas, con criterio de corte. | Pueden ser muy valiosas, pero su disponibilidad y su costo no están probados. |
| D12 | Una rama de integración por ola. Ninguna ola arranca sin pasar el gate anterior, salvo los spikes que solo miden. | Tenés ~10 h/semana: el cuello de botella es tu revisión, no los agentes. |
| D13 | "El mejor del mundo" se mide con una comparación **a ciegas** contra Opus Clip en 3–4 videos del golden set, desde la Ola 2. | Sin una vara externa, "el mejor" es una opinión. |

### 5.2 Para Agustín — respondidas el 23-sep-2026: sí a todas (A6: propone el coordinador)

| # | Pregunta | Recomendación | Si no respondés |
|---|---|---|---|
| A1 | ¿Purgamos ya las cachés envenenadas de producción? Yo borro las filas de caché de `B60BHDNFNxM` y `fVSgIKS_RSk` en Supabase (te muestro la lista antes) y vos corrés un comando en el VPS. | Sí, ahora. | No purgo: toca producción. |
| A2 | ¿Charla/humor entra como formato de primera clase en el golden set? El ICP no cambia. | Sí. | Sigo con sí. |
| A3 | ¿Pasamos a presupuesto y precio **por minuto de video** (ADR nuevo)? Referencia a verificar: Opus cobra por minuto procesado. | Sí, antes de abrir el cobro. Para ingeniería alcanza con el techo de US$0,003/min. | Trabajo con el techo de US$0,003/min. |
| A4 | ¿Te comprometés a ~1 h/semana de etiquetado y a sumar 1–2 canarios que etiqueten? | Sí: sin etiquetas no hay gate G2. | La Ola 2 se atrasa. |
| A5 | ¿Tope real de duración a 150 min, aplicado en el worker? | Sí. El ICP publica episodios de 1–2 h, y el < 15 min de la beta sigue midiéndose sobre videos ≤ 90 min. | 150 min en el worker, avisado en el PR. |
| A6 | Dos videos nuevos para el golden set: otro de **charla/humor** (stream de 60–90 min) y uno de **monólogo/coach** (30–60 min), idealmente de gente que podría usar el producto. | Elegilos vos: tu criterio de "posteable" es la vara. | Te propongo 3 opciones de cada uno y elegís. |

---

## 6. Líneas de trabajo

Cada línea tiene su brief en `docs/briefs/`, con criterios de aceptación verificables. Acá va el porqué y el diseño; el cómo está en el brief.

### Ola 0 — Cimientos: confiar en lo que medimos

**W18 · Caché íntegra y red de seguridad** · `fix/worker-cache-integridad` · agente *fiabilidad* · esfuerzo M · [brief](briefs/W18-cache-integra.md)
- **Problema:** H1–H5.
- **Qué cambia:**
  - Cada transcript lleva una **Huella** (fuente, modelo, idioma, pista de audio y hash del texto). Un análisis cacheado se reutiliza solo si su huella coincide.
  - El tono sale de la clave del análisis cuando la Pasada A no lo usa: más aciertos de caché y menos costo.
  - La copia local del transcript se usa solo si Supabase **no responde**, nunca si respondió "no hay fila".
  - Un transcript cacheado en otro idioma que `TRANSCRIPT_LANGUAGE` se descarta.
  - Nueva herramienta de purga por video que limpia los tres lugares (Supabase, clave pelada y compuesta, archivos locales).
  - **Cortacircuitos:** si 4 de los primeros 5 candidatos (o ≥ 50 % en total) no anclan, se invalida la caché del video, se rehace transcript y Pasada A una vez, y si persiste el job falla con devolución de crédito.
  - El piso de entrega nunca incluye rotos.
  - Con el anclaje fallido no hay recorte por palabras del hook.
- **Éxito:** los tests reproducen `fb287cba` y quedan en verde; 0 disparos del cortacircuitos en el golden set sano.
- **Riesgo:** falsos positivos del cortacircuitos en videos sin habla clara. Mitigación: umbral con mínimo de 4 candidatos; cada disparo queda en el log con prefijo `CORTACIRCUITOS`, y si persiste, salta la alerta de job fallido que ya existe (F1).

**W19 · Referencias y tier `seleccion`** · `feat/eval-referencias` · agente *eval* · esfuerzo M, más 3–4 h tuyas de validación · [brief](briefs/W19-referencias-y-tier-seleccion.md)
- **Problema:** hoy no hay forma barata de saber si la Pasada A encuentra los mejores momentos.
- **Qué cambia:**
  - Nuevo activo **Referencia**: momentos validados por un humano, con su **núcleo** (planteo → remate), tipo, calidad A/B y por qué.
  - Script de borrador asistido que usa un modelo de **otra familia** para no inflar el recall. Vos validás cada lista.
  - Métricas de §3.
  - Tier `seleccion`: solo clasificador + Pasada A sobre el transcript cacheado, con `--reps 3` y sin leer ni escribir `analysis_cache`.
  - Golden set por formato (D9).
  - Baseline commiteado.
- **Éxito:** Referencias validadas para ≥ 6 videos (≥ 2 de charla); el tier corre en < 15 min y < US$4.
- **Semilla:** las 25 Referencias de B60BHDNFNxM del Anexo B.

**W20 · Operación: purga, tope real y runbook** · coordinador y vos · la parte de código la toma el agente *fiabilidad* en `fix/worker-tope-duracion` · esfuerzo S · [brief](briefs/W20-operacion-purga-y-tope.md)
- Purga de B60BHDNFNxM y fVSgIKS_RSk (A1).
- Runbook "después de un fix de audio, transcript o idioma".
- El worker aplica `MAX_VIDEO_MINUTES` con la duración real, cierra si se pasa y devuelve el crédito (A5).
- Se documenta que **mergear a `main` despliega el worker** (`deploy.yml` escucha el push), cosa que hoy contradice `AGENTS.md`.

### Ola 1 — Selección que ve todo el video

**W21 · Pasada A por Ventanas** · `feat/worker-pasada-a-ventanas` · agente *selección* · esfuerzo M · [brief](briefs/W21-pasada-a-por-ventanas.md)
- **Problema:** H6–H8.
- **Diseño:**
  - Ventanas de 20 min con 3 min de solape (configurables), llamadas en paralelo (≤ 4).
  - Cupo de candidatos proporcional a la duración de cada ventana.
  - Hasta que entre W23 se mantiene el **total ≤ 30**: mejor cobertura sin más tiempo de evaluación.
  - Unión con deduplicación por solape y fusión de candidatos contiguos de la misma historia.
  - Mismo formato de salida. `PROMPT_VERSION` sube a un valor nunca usado: `analysis_cache` ya tiene filas `v9` de un experimento.
- **Éxito (G1):** `recall_completo` ≥ 60 % y `min_cuarto` ≥ 15 % en videos > 60 min; costo de la Pasada A ≤ +25 %.
- **Después, como variable aparte:** probar `gemini-3-flash-preview`, que cuesta un tercio con ventanas cortas.

**W22 · Formatos de contenido** · `feat/worker-formatos` · agente *selección* (después de W21) · esfuerzo M · [brief](briefs/W22-formatos-de-contenido.md)
- **Problema:** H8, H9, y los tramos de publicidad (el aviso de DiDi en 3100–3168 s).
- **Diseño:**
  - Clasificador de Formato sobre tres extractos (inicio, medio, final) más título y canal.
  - Foco de selección por formato. Charla: anécdota con remate, imitación, cruce con el público, frase citable, discusión.
  - Regla de "historia completa" (30–120 s, planteo → remate → reacción).
  - Tramos de publicidad excluidos.
  - `CONTEXT.md`: Formato reemplaza a Categoría.
- **Éxito:** clasificador ≥ 90 % en el golden set; en charla, +15 pp de `recall_completo` sobre W21; `historias_partidas` ≤ 1 por video; 0 candidatos en publicidad.

### Ola 2 — Evaluar mejor, más rápido y entregar lo que vale

**W23 · Evaluar en texto primero** · `feat/worker-evaluar-en-texto` · agente *evaluación* · esfuerzo L · [brief](briefs/W23-evaluar-en-texto-primero.md)
- **Parte A (Ola 1, solo mide):** correlación entre la nota sobre el texto de las Líneas y la nota sobre la Transcripción del clip, en ≥ 60 candidatos del golden set.
- **Parte B (Ola 2):** detrás de `EVALUACION=texto_primero`:
  - Pre-ranking textual de todos los candidatos en paralelo.
  - Evaluación completa (descarga, Transcripción del clip, anclaje y juez) solo para el top 1,5× del cupo de entrega, con un pool paralelo que respeta el proxy sticky.
  - Recién entonces se levanta el tope de 30 candidatos.
- **Éxito:** ρ ≥ 0,8 para adoptar; p90 ≤ 15 min con video de 60 min; costo por minuto baja; posteables no bajan.

**W24 · Rankeador por formato, calibrado** · `feat/worker-rankeador-por-formato` · agente *ranking* · esfuerzo M, depende de tus etiquetas · [brief](briefs/W24-rankeador-por-formato.md)
- **Problema:** H10.
- **Diseño:**
  - Rúbrica por Formato, en el juez y en las preguntas de Jev. En charla: "¿da risa o provoca una reacción que dan ganas de mandarle a alguien?", "¿llega al remate?".
  - `calibracion.py` por formato y por variante de rankeador; decisión en un ADR.
  - **Spike "juez que escucha":** Gemini recibe el audio del clip (≤ 90 s) además del texto. Se corta si OpenRouter no acepta audio o si no mejora la precisión en charla ≥ 0,1.
- **Éxito:** en charla, precision@5 ≥ 0,8 con el rankeador elegido; sin regresión en entrevista.

**W25 · Entrega por calidad, nunca rotos** · `feat/worker-entrega-relativa` · agente *ranking* · esfuerzo S · [brief](briefs/W25-entrega-relativa.md)
- **Problema:** H4, H11. El umbral absoluto no significa lo mismo con otro formato o rankeador, y el piso rellena para llegar a un número.
- **Diseño:**
  - **Piso de calidad calibrado por rankeador y Formato:** la nota a partir de la cual, según las etiquetas, la precisión es ≥ 80 %.
  - Se entregan **todos** los candidatos usables, no rotos y diversos que superan ese piso, ordenados por score. Si un video tiene 4 momentos excelentes, salen 4; si tiene 14, salen 14.
  - Tope solo por costo (`DELIVERY_MAX_CLIPS`, 20 para videos largos). Sin relleno para llegar a un número. Con 0, el job falla y devuelve el crédito.
- **Éxito:** en la simulación sobre el golden set, captura de lo mejor ≥ 60 %, precisión ≥ 75 % con etiquetas y 0 rotos.

### Ola 3 — Que se vea profesional

**W26 · Encuadre para estudio multicámara** · `feat/worker-encuadre-estudio` · agente *visual* · esfuerzo L, empieza con un spike M · [brief](briefs/W26-encuadre-estudio.md)
- **Problema:** H13.
- **Diseño:**
  - Primero se mide W5 sobre ≥ 30 escenas etiquetadas.
  - Luego: Fill sobre **quien habla** (movimiento de boca correlacionado con la energía del audio en la escena); Split solo con dos hablantes grandes, nunca con público; planos generales con Fill sobre la cara más grande o un Fit más cerrado; zócalos de la fuente detectados como regiones estáticas, con los subtítulos por encima.
- **Éxito:** layout correcto ≥ 85 % de las escenas; "se_ve_mal" ≤ 10 % de los rechazos; el render tarda ≤ +20 %.

**W27 · Overlay y copy por formato** · `feat/worker-overlay-promesa` · agente *copy* · esfuerzo S–M · [brief](briefs/W27-overlay-y-copy-por-formato.md)
- **Problema:** H14 y el copy "informativo" en humor ("El clip revela…", "Descubre…").
- **Diseño:**
  - El overlay es la **promesa** del clip en ≤ 4 palabras, con palabras de cualquier parte del clip, no solo del arranque.
  - Estilo de copy por Formato. En charla: títulos que citan la frase ("«Porteño, usted tiene 3 problemas»").
  - Se conservan las reglas de W13.
- **Éxito:** "copy_malo" ≤ 10 % de los rechazos; overlays iguales a las primeras palabras del clip → ~0.

### Ola 4 — Más allá del texto: apuestas medidas

**W28 · Spike: mapa de calor de YouTube** · `feat/eval-spike-mapa-de-calor` · agente *señales* · esfuerzo S · [brief](briefs/W28-spike-mapa-de-calor.md)
- ¿Se puede leer el "lo más repetido" desde el VPS? ¿Sus picos marcan Referencias?
- **Se corta si** está disponible en < 50 % de los videos o la precisión de los picos contra Referencias es < 0,4.

**W29 · Spike: risas y aplausos** · `feat/eval-spike-risas` · agente *señales* · esfuerzo M · [brief](briefs/W29-spike-risas-y-aplausos.md)
- Detector de eventos de audio en CPU (ONNX) sobre el audio completo. ¿Las risas marcan el remate de las Referencias de charla?
- **Se corta si** tarda > 3 min de CPU por hora de audio en el VPS, o si el recall es < 0,5.
- Si pasa: marcas "[risas]" en las Líneas para la Pasada A de charla y una señal de "reacción" para el ranking.

**Backlog, sin brief:** diarización ("quién habla"), que se evalúa después de W29 con el costo del proveedor a la vista; override del Formato en la UI; 1080p; B-roll; publicar en redes.

---

## 7. Olas, gates y calendario

| Ola | Semana (estimada) | Líneas | En paralelo | Gate de salida |
|---|---|---|---|---|
| 0 Cimientos | 24–30 sep | W18, W19, W20 | 2 agentes + coordinador | **G0:** 0 cachés envenenadas (detector de idioma); Referencias validadas en ≥ 6 videos (≥ 2 de charla, ≥ 12 momentos cada uno); baseline de `seleccion` y `e2e` commiteado; W18 en `main` |
| 1 Selección | 1–7 oct | W21 → W22; W23-A (medición) | 2 agentes | **G1:** en `seleccion` con 3 repeticiones: `recall_completo` ≥ 60 % y `min_cuarto` ≥ 15 % en videos > 60 min; `historias_partidas` ≤ 1; 0 candidatos en publicidad; Pasada A ≤ +25 % de costo; sin regresión > 5 pp en videos cortos |
| 2 Evaluar y entregar | 8–14 oct | W23-B, W24, W25 | 2–3 agentes | **G2:** con `e2e` y etiquetas (n ≥ 40, ≥ 15 de charla): captura de lo mejor ≥ 60 %; precisión ≥ 75 %; 0 rotos; p90 ≤ 15 min (60 min) y ≤ 25 min (120 min); ≤ US$0,003/min |
| 3 Presentación | 15–21 oct | W26, W27 | 2 agentes | **G3:** "se_ve_mal" y "copy_malo" ≤ 10 % de los rechazos cada uno; layout correcto ≥ 85 %; win rate a ciegas contra Opus ≥ 50 % |
| 4 Señales | 22–28 oct | W28, W29 | 1 agente | **G4 (por spike):** se adopta si mueve recall o precisión en charla ≥ 10 pp a ≤ +US$0,0005/min |

Hay una semana de colchón. Un gate que no pasa da una semana más de iteración dentro de la ola o se revierte el flag; nunca se "arrastra" a la ola siguiente.

**Los umbrales de G1 y G2 se confirman con el baseline de G0.** Si el baseline muestra que un umbral es trivial o imposible, se ajusta antes de arrancar la ola, nunca después de medir.

**Tu semana (~10 h):**
- Revisar la integración de la ola y mergear a `main`: 3–4 h.
- Etiquetar: en la Ola 0, validar Referencias (3–4 h esa semana); desde la Ola 1, clips (1–2 h).
- Gate y decisiones: 1 h.
- Deploy y flags en el VPS: 0,5–1 h.
- El resto queda de colchón.

**Etiquetas para G2:** a partir de la Ola 1 corrés los videos del golden set como jobs reales en tu cuenta y etiquetás en la galería (como el 21-sep). Si se suman canarios, mejor.

---

## 8. Plan de agentes

### 8.1 Roles

| Agente | Líneas | Ola | Toca (funciones/módulos) |
|---|---|---|---|
| **Coordinador** (Claude, repo principal) | integración, mediciones, gates, W20 ops | todas | ramas `integracion/mejora-ola-N`, `eval/runs/`, este plan |
| *fiabilidad* | W18 → W20 (código) | 0 | `analysis_cache`, `transcript_cache`, `yt_transcript`, loop de candidatos en `_process_job_inner`, `_refine_bounds_legacy`, `select_finalists` (piso), script de purga |
| *eval* | W19; apoyo de medición después | 0 → | `worker/eval/*`, `golden_set.json` |
| *selección* | W21 → W22 | 1 | `select_moments`, `get_selection_prompt`, `rank_and_prune_candidates`, `get_video_category` |
| *evaluación* | W23 (A en Ola 1, B en Ola 2) | 1–2 | loop de candidatos (`_prepare_moment_clip` y su orquestación), pool de descargas |
| *ranking* | W24 → W25 | 2 | `judge_moment_scores`, `QUESTIONS` de Jev, `calibracion.py`, `select_finalists`, `umbral_entrega.py` |
| *visual* | W26 | 3 | `services/reframe.py`, render de `clip_generator` |
| *copy* | W27 | 3 | `generate_moment_copy_full`, `content_validators` |
| *señales* | W28, W29 | 4 | `worker/eval/` (spikes) |

### 8.2 Mapa de conflictos y orden forzado

| Zona | Líneas | Orden |
|---|---|---|
| Loop de candidatos (`_process_job_inner`) | W18 (cortacircuitos), W23 (texto primero) | W18 en la Ola 0 → W23 en la Ola 2 |
| `moment_selector` | W18 (piso), W21, W22, W25 | W18 → W21 → W22 → W25, en olas distintas o secuenciales |
| `processor.py` | W22 (clasificador), W27 (Pasada B) | funciones distintas; W27 va en la Ola 3 |
| Juez y Jev | W24 | solo |
| `reframe` y render | W26 | solo |
| `worker/eval` | W19, luego métricas chicas de otras líneas | W19 primero; las demás agregan, no reescriben |

### 8.3 Protocolo con Orca

1. El coordinador crea `integracion/mejora-ola-N` desde `main` y despacha a cada agente con: *"Leé `AGENTS.md` y `docs/briefs/WNN-….md`, y trabajá en `<rama>` creada desde `integracion/mejora-ola-N`"*.
2. **A los 5 minutos** el coordinador verifica con `orca terminal read --screen` que el agente arrancó. Si no, reenvía con `orca terminal send --text … --enter` (lección del 20-sep).
3. El agente hace commit **y push** después de cada paso en verde (`git push -u origin <rama>`): si Orca cierra el worktree, lo pusheado sobrevive (lección del 18-sep).
4. El PR apunta a `integracion/mejora-ola-N`, nunca a `main`, y su descripción sigue la plantilla de §8.4.
5. El coordinador revisa (skill `code-review`), mergea a la integración, corre la medición del gate y escribe el reporte en `worker/eval/runs/README.md` y en §9 de este plan.
6. Vos revisás el reporte y el PR `integracion/mejora-ola-N → main`, y mergeás. Eso **despliega el worker** (`deploy.yml`). Después prendés los flags en `~/viralengine/.env` según el gate.

### 8.4 Plantilla de PR

**Qué cambió** · **Contrato tocado** (esquema / endpoint / variable de entorno: sección de `PROYECTO.md` actualizada) · **Flag y rollback** · **Cómo se probó** (comandos de tests de `AGENTS.md` + medición: comando y archivo en `eval/runs/`) · **Antes → después** (las métricas de la línea) · **Riesgos** · **Docs** (`PROYECTO.md`, `CONTEXT.md`, ADR).

### 8.5 Términos nuevos (van a `CONTEXT.md` en el PR que los introduce)

**Referencia** y **Núcleo** (W19) · **Huella del transcript** y **Cortacircuitos** (W18) · **Ventana** (W21) · **Formato**, que reemplaza a Categoría (W22) · **Pre-ranking textual** (W23) · **Piso de calidad** y **Cupo de entrega** (W25).

### 8.6 ADRs previstos

- **0009** Selección por ventanas (W21).
- **0010** Formato de contenido (W22).
- **0011** Evaluar en texto primero (W23).
- **0012** Rankeador por formato (W24).
- **0013** Entrega relativa a la duración (W25).
- **0014** Precio y presupuesto por minuto (A3, lo escribe el coordinador con tu decisión).

---

## 9. Estado (se actualiza en cada gate)

| Línea | Estado | Rama | Gate | Nota |
|---|---|---|---|---|
| W18 Caché íntegra | En curso (Ola 0) | `fix/worker-cache-integridad` | G0 | agente *fiabilidad* |
| W19 Referencias y tier `seleccion` | En curso (Ola 0) | `feat/eval-referencias` | G0 | agente *eval*; las Referencias de B60BHDNFNxM salen del Anexo B |
| W20 Purga, tope y runbook | Purga de Supabase: 23-sep; volumen del VPS: pendiente de Agustín; tope y runbook: después de W18 | `fix/worker-tope-duracion` | G0 | coordinador + *fiabilidad* |
| W21–W29 | Pendiente (Olas 1–4) | — | G1–G4 | — |

---

## 10. Presupuesto de API para las mediciones

| Ola | Qué se paga | Estimado |
|---|---|---|
| 0 | Borradores de Referencias (~US$0,3 por video × 8), baseline `seleccion` (3 repeticiones) y `e2e` | ~US$6 |
| 1 | `seleccion` × 3 repeticiones por variante (≈ US$1 por repetición para 8 videos) y medición de W23-A | ~US$8 |
| 2 | `e2e`, jobs reales para etiquetar (~US$0,2 cada uno × 10) y calibración | ~US$10 |
| 3–4 | Renders de prueba y spikes | ~US$6 |
| **Total** | | **~US$30–40** |

El costo por job en producción se vigila en `usage_summary`, con techo de US$0,003 por minuto de video.

---

## 11. Riesgos

| Riesgo | Mitigación |
|---|---|
| Tus etiquetas no alcanzan para G2. | Referencias en la Ola 0 (sirven sin etiquetar clips) y canarios desde la Ola 1. G2 exige n ≥ 40 y, si no se llega, se extiende. |
| La varianza del LLM engaña a la medición. | 3 repeticiones, media ± desvío, y un cambio cuenta solo si mejora en la mayoría de los videos. |
| Sobreajuste a B60BHDNFNxM. | Los gates miden el agregado de ≥ 6 videos y 4 formatos. |
| Las ventanas generan duplicados o suben el costo. | Deduplicación por solape, cupo total, y costo de la Pasada A dentro del gate. |
| El ranking sobre texto difiere del ranking sobre el clip. | Adopción condicionada a ρ ≥ 0,8, y el top 1,5× igual pasa por evaluación completa. |
| Conflictos en el loop y en `moment_selector`. | Orden forzado (§8.2) e integración por ola. |
| YouTube bloquea la IP de la Mac durante las mediciones. | `seleccion` usa transcripts cacheados, sin descargas; `e2e` con proxies (`WEBSHARE_PROXY_FILE`). |
| Orca borra un worktree. | Push incremental (§8.3). |
| Mergear a `main` despliega sin querer. | Todo cambio de comportamiento entra con flag apagado (principio 2). |
| Otra caché envenenada. | W18 más runbook de purga (W20). |
| Una medición con código experimental contamina la caché de producción (H17). | Reglas de §4.1: las mediciones no leen ni escriben la caché de análisis, y los flags entran en la versión efectiva. |

---

## 12. Fuera de alcance de este plan

Modelos nuevos para la Pasada A antes de que pasen G1 las ventanas y los formatos (una variable por vez) · 1080p · B-roll y animaciones · publicar en redes · inglés · rediseño de UI (salvo lo mínimo para etiquetar) · diarización (backlog) · cambios de precio en el código (A3 es una decisión, no una línea).

---

## Anexo A — Experimentos del 23-sep-2026

**Setup:** worktree de `main` (`842c138`) con Supabase apuntado a una URL muerta (lecturas y escrituras de caché fallan; nada toca producción), `TRANSCRIPT_SOURCE=whisper_full`, `TRANSCRIPT_LANGUAGE=es`, proxies de la raíz.

**Transcript:** audio original "Spanish (US) original" vía RapidAPI (102 MB en 55 s). Whisper Groq: 110,7 min, 2457 Líneas, 99 % puntuadas, US$0,074, 133 s. Costo total de los experimentos: ≈ US$0,25.

**A.1 — Pasada A en una pasada contra ventanas, sobre las 15 Referencias A de B60BHDNFNxM:**

| Ref. | Momento | Min. | Producción (1 pasada) | 4 ventanas × 8, sin solape |
|---|---|---|---|---|
| R01 | Caniggia y el auto en Italia 90 | 8:41 | parcial (sin planteo) | partida en dos |
| R02 | "Porteño, usted tiene 3 problemas" | 19:24 | parcial (sin remate) | completa |
| R03 | Voces del Oso Yogi y Melquíades | 25:28 | parcial (15 s de 80) | completa |
| R04 | "El periodista, el más puteado después del árbitro" | 27:31 | completa | ausente (borde entre ventanas) |
| R05 | "Desaparecés 10 s y gritás gol" | 30:43 | ausente | ausente |
| R06 | Cuti Romero y Haaland | 32:37 | completa | completa |
| R07 | Maradona en Nápoles, 1990 | 45:11 | parcial (sin planteo) | ausente |
| R08 | La foto perdida con Jagger | 46:31 | partida en dos | ausente |
| R09 | "Después vas y llorás en el baño" | 51:00 | completa | completa |
| R10 | Ledley King, el ídolo que nadie conoce | 1:02:17 | ausente | completa |
| R11 | "Al Arsenal lo viven robando" | 1:08:48 | ausente | completa |
| R12 | "Cierren el orto" a los hinchas de Boca | 1:13:57 | ausente | completa |
| R13 | La frase de Luis Enrique | 1:45:11 | ausente | completa |
| R14 | "Hasta los 22 años" | 1:46:26 | ausente | completa |
| R15 | "El Real Madrid es una asociación ilícita" | 1:48:37 | ausente | completa |
| | **Total** | | **3 completas, 5 parciales, 7 ausentes** | **10 completas, 1 partida, 4 ausentes** |

Candidatos por cuarto: una pasada [17, 13, 0, 0]; ventanas [8, 8, 8, 8], por construcción.

**A.2 — Rankeadores sobre el texto de las Líneas** (sin penalizaciones, escala 0–30):

| Candidato | Juez | Jev |
|---|---|---|
| Cuti/Haaland (1957–2012) | 20 | 21,8 |
| Peñarol (1156–1200) | 16 | 19,1 |
| Bilardistas (3060–3094) | 15 | 18,6 |
| Periodista (1651–1709) | 19 | 16,7 |
| Jagger completa (2791–2901) | 18 | 14,8 |
| Caniggia (564–612) | 18 | **8,8** |
| Oso Yogi completo (1528–1610) | 13 | **8,2** |
| Ledley King (3737–3811) | 13 | **10,5** |
| Asociación ilícita (6503–6545) | 9 | 11,4 |
| Luis Enrique (6311–6361) | 10 | 12,3 |

`select_finalists` (umbral 15, tope 12, piso 5) entrega 5 clips.

**A.3 — `fb287cba`, cronología:**
- 13:39–13:40: se escribe la caché con el doblaje.
- 14:22–14:24: merge y deploy de W16.
- 14:43–14:45: merge y deploy de W17.
- 14:50: arranca el job, que carga el transcript local en inglés y el análisis cacheado.
- 14:52: descarga por clip (14/30 fallan en paralelo); luego 30 candidatos × (Whisper + Whisper con margen + juez + Jev).
- 15:20: Pasada B de 5.
- 15:21: completed.

Horarios en hora local; en UTC son +3 h.

## Anexo B — Semilla de Referencias de B60BHDNFNxM (para validar)

Criterio de Claude, un solo anotador. Hay que validarlas (W19). El **núcleo** es lo mínimo que el clip tiene que contener; los tiempos son del transcript real, en segundos.

| Ref. | Cal. | Tramo | Núcleo | Tipo | Por qué |
|---|---|---|---|---|---|
| R01 | A | 521–628 | 557–626 | anécdota | Bilardo manda a tirar piedritas al auto de Caniggia; remate absurdo |
| R02 | A | 1164–1229 | 1174–1229 | anécdota | Autodesprecio con remate: "Mamá, fracasé, voy a ser periodista" |
| R03 | A | 1528–1610 | 1528–1605 | imitación | Voces en vivo; reacción del panel ("acabo de ser feliz") |
| R04 | A | 1651–1709 | 1651–1676 | opinión con remate | "¡Es periodista, hijo de puta, fue penal!" |
| R05 | A | 1843–1885 | 1843–1885 | cruce con el público | Un hincha lo expone; confiesa que mira Twitter en el gol |
| R06 | A | 1964–2012 | 1967–2012 | anécdota | Cuti: "la espalda de Tyson… tenés que rezar" |
| R07 | A | 2711–2769 | 2711–2769 | anécdota histórica | Maradona: "pongan el grabador… siempre fuimos sudacas" |
| R08 | A | 2791–2901 | 2796–2896 | anécdota | La foto con Jagger que perdió en la mudanza |
| R09 | A | 3060–3090 | 3060–3087 | frase citable | "Públicamente no hay frío ni calor… después llorás en el baño" |
| R10 | A | 3737–3811 | 3743–3782 | cruce con el público | Ídolo de Tottenham: "¿Quién es? Será un cantante" |
| R11 | A | 4126–4193 | 4128–4180 | opinión polémica | "El Arsenal es el Huracán de la Premier" |
| R12 | A | 4437–4470 | 4437–4462 | chicana | Bienvenida a los hinchas nuevos de Boca "con una condición" |
| R13 | A | 6311–6361 | 6316–6350 | cruce con el público | La "frase de Luis Enrique"; "te quemaron el chiste" |
| R14 | A | 6386–6407 | 6386–6403 | frase citable | "Hasta los 22 podés hinchar por uno de Europa" |
| R15 | A | 6503–6545 | 6517–6535 | frase citable | "El Real Madrid es una asociación ilícita" |
| R16 | B | 283–335 | 283–328 | anécdota | Romay, "colifa divino" (lo etiquetaste posteable) |
| R17 | B | 2022–2048 | 2030–2046 | anécdota | Yerry Mina y Guardiola: "¿que le dé un beso en la boca?" |
| R18 | B | 2209–2265 | 2209–2255 | anécdota | Enzo Fernández: "yo soy culón" |
| R19 | B | 2633–2671 | 2645–2669 | anécdota (sensible) | Maradona en Barcelona: "ocho amigos, ocho enemigos" |
| R20 | B | 1064–1098 | 1065–1092 | cruce con el público | Hinchas del Arsenal: "amargos, pecho frío" |
| R21 | B | 3466–3491 | 3466–3480 | chicana | "Una Libertadores, pero que se muera Arteta" |
| R22 | B | 3894–3905 | 3895–3901 | chiste corto | Herbert Chapman, "el que mató a Lennon" |
| R23 | B | 5714–5728 | 5719–5727 | frase citable | "Lo más injusto del futbolista…" |
| R24 | B | 3169–3182 | 3172–3181 | cierre | "¿Cómo la pasaste? Como el orto. No vengo más." |
| R25 | B | 1450–1494 | 1450–1484 | anécdota | Bielsa "después enloqueció"; la final del 92 |

**Excluir siempre:** 3100–3168 (aviso de DiDi).
