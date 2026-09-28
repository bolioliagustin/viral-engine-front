# Análisis de la validación de Referencias (28-sep-2026)

Validación de Agustín sobre los 7 videos del golden set: **121 Referencias validadas (80 A) y 37 descartadas**. 62 decisiones traen comentario (26 descartes y 36 aceptados). Fuentes: `worker/eval/referencias/*.json` (campos `comentario_validacion` y `motivo_descarte`) y `2026-09-28-seleccion-baseline-validado.json`. Todo el análisis corrió sin llamadas pagas.

## 1. La vara real (baseline `seleccion` contra Referencias validadas)

| Video | Formato | Refs A | `recall_completo` A | `min_cuarto` | `precision_ref@10` |
|---|---|---|---|---|---|
| claude_hacks_regression_01 (19 min) | clase | 4 | 92 % ± 14 | 11 % | 78 % |
| charla_humor_02 (Blender) | charla | 8 | 46 % ± 14 | 12 % | 20 % |
| monologo_coach_01 (Duró) | monólogo | 17 | 33 % ± 15 | 1 % | 43 % |
| business_spanish_01 | clase | 11 | 27 % ± 24 | 6 % | 37 % |
| podcast_general_01 (Wild Project) | entrevista | 19 | 16 % ± 11 | 6 % | 53 % |
| charla_humor_01 (B60) | charla | 12 | 14 % ± 5 | 12 % | 53 % |
| user_recommended_01 (108 min) | charla | 9 | 4 % ± 6 | 4 % | 17 % |
| **Macro** | | **80** | **33 % ± 29** | **7 %** | **43 %** |

Sin el video corto, los videos largos promedian ~23 % de `recall_completo`: la Pasada A contiene completos menos de 1 de cada 4 momentos que Agustín considera de lo mejor.

## 2. Cobertura: el sesgo a la primera mitad es del modelo y el prompt actuales

Propuestas por cuarto del video (entre paréntesis, el cuarto con menos propuestas):

| Video | Borradores Sonnet 5 (1 llamada) | Pasada A actual (3 reps) | Aceptados por Agustín |
|---|---|---|---|
| business_spanish_01 | [3, 3, 5, 8] (16 %) | [55, 21, 9, 5] (6 %) | [2, 3, 4, 8] |
| podcast_general_01 | [8, 6, 3, 6] (13 %) | [53, 23, 7, 7] (8 %) | [8, 5, 3, 6] |
| user_recommended_01 | [7, 5, 6, 4] (18 %) | [44, 28, 14, 4] (4 %) | [5, 4, 5, 2] |
| charla_humor_02 | [7, 6, 7, 7] (22 %) | [29, 25, 21, 15] (17 %) | [6, 6, 4, 3] |
| monologo_coach_01 | [8, 10, 5, 5] (18 %) | [49, 30, 4, 1] (1 %) | [5, 6, 5, 5] |

- **Lo mejor está repartido en todo el video.** En business_spanish_01, 8 de los 17 aceptados están en el último cuarto, donde la Pasada A pone 5 de 90 candidatos.
- **Un modelo fuerte con otro prompt cubre parejo en una sola llamada.** El sesgo no es inevitable; viene de gemini-3.5-flash con razonamiento bajo más el prompt actual.
- **Circularidad:** las Referencias salieron de los borradores de Sonnet (más la semilla de B60), así que un Sonnet como Pasada A tendría el recall inflado contra ellas. La comparación justa es de cobertura por cuartos y de aceptación, no de recall.

## 3. Qué acepta y qué descarta Agustín

**Aceptación por formato:** entrevista 96 %, clase 64–89 %, monólogo 75 %, charla 68–73 %. Por calidad propuesta: A 85 %, B 64 %: la calidad que propone el modelo informa algo. Borradores de Sonnet: 78 % aceptados; semilla de Claude en B60: 68 %.

**Por tipo:** explicación 94 %, dato 88 %, anécdota 76 %, opinión 75 %, cruce con el público 67 %, frase citable 64 %, imitación 33 % (n = 3).

**Duración:**
- Mediana del núcleo: 43 s en los aceptados y 29 s en los descartados.
- Núcleos de 45 s o más: 86 % aceptados. Núcleos de menos de 25 s: 67 %.
- "Corto" es el motivo de descarte más frecuente.

**Motivos de descarte (26 con comentario):**

| Motivo | Casos | Ejemplos |
|---|---|---|
| Corto / sin conclusión | 10 | Duró: 5 frases de 6–19 s ("muy corto y no hay conclusión"); claude_hacks: 11–18 s |
| Sin remate | 6 | Blender: "no tiene remate", "no hay anécdota graciosa"; Wild Project: 99 s "no hay remate" |
| Flojo / no destacado / básico | 7 | "siempre hacen eso, no creo que sea algo destacado", "es básico", "no imita nada" |
| No se entiende / depende de otro momento | 4 | "necesita del R07 para que se entienda", "no queda claro de qué habla" |
| Solo muestra, no explica | 3 | tutoriales: "solo lo muestra y nada más", "es flojo, no explica" |
| Estructura | 3 | "demora mucho en llegar al núcleo", "muy largo y hay un tema en medio", "son dos temas" |
| Publicidad | 1 | "es una publicidad" |
| Promete y no muestra | 1 | "no muestra la remontada, solo que ganaron" |

**Reparos en los aceptados (36 comentarios): casi nunca son del momento.**

| Reparo | Casos | Qué línea lo ataca |
|---|---|---|
| Flojo pero sirve / como extra | 13 | Piso de calidad (W25): contenido "B" |
| **Título o descripción sin contexto** ("¿qué virus?", "no nombra con qué compara") | 5 | **W27 (copy)** |
| **Inicio del corte**: arranca antes, tarda en llegar al impacto, o arranca con el resultado sin contexto | 6 | **W21/W22 (corte)** |
| **Final del corte**: "se corta en medio", "lo alargaría al remate", "lo extendería con R10" | 3 | **W21 (fusión de historias) y corte** |
| Falta contexto / no explica el cómo | 6 | W22 (foco por formato) |
| Dato viejo | 1 | Frescura (clase/tech) |

## 4. ¿La nota de la Pasada A (`rank_score`) predice lo mejor? (hipótesis del estudio de ML)

AUC contra Referencias validadas, con candidatos agrupados entre repeticiones y bootstrap de 2000 remuestras:

| Video | AUC `rank_score` [IC 95 %] | AUC duración | AUC posición | precision@5 por `rank_score` (base) |
|---|---|---|---|---|
| business_spanish_01 | 0,83 [0,67–0,96] | 0,62 | 0,71 | 47 % (20 %) |
| podcast_general_01 | 0,69 [0,48–0,88] | 0,57 | 0,60 | 47 % (39 %) |
| claude_hacks_regression_01 | 0,62 [0,00–1,00] | 0,74 | 0,90 | 73 % (70 %) |
| user_recommended_01 | 0,47 [0,19–0,73] | 0,53 | 0,42 | 7 % (14 %) |
| charla_humor_01 (B60) | 0,84 [0,67–0,97] | 0,80 | 0,46 | 67 % (21 %) |
| charla_humor_02 | 0,54 [0,35–0,72] | 0,53 | 0,43 | 27 % (27 %) |
| monologo_coach_01 | 0,76 [0,54–0,94] | 0,85 | 0,66 | 73 % (31 %) |
| **Macro** | **0,68** | **0,67** | **0,60** | **49 % (32 %)** |

**Confirmada a medias:** hay señal en 4 de 7 videos y nada en 2. En promedio no le gana a la duración sola. Sirve como una señal más en W24, no como rankeador.

## 5. Implicancias para el plan

1. **W22 (formatos) tiene ahora criterio explícito de Agustín por formato:**
   - clase: impacto + automatismo + resultado + **cómo** se hace, con contexto antes del resultado y frescura;
   - monólogo: idea completa con conclusión, nada de frases sueltas de menos de 20 s;
   - charla: anécdota con remate, entendible sola, un solo tema, llegar rápido al punto; sin rutinas, sin publicidad y sin reacciones largas;
   - entrevista: casi todo sirve, el problema es el título.
2. **Duración mínima efectiva:** núcleos de menos de 25 s son la primera causa de descarte. W22 debería pedir ideas completas de al menos ~30 s, salvo frases excepcionales.
3. **W21:** medir las Ventanas como estaba previsto. Además, **probar un modelo más fuerte en una sola pasada como variable aparte**, con la advertencia de circularidad (evaluarlo por cobertura y aceptación, no por recall contra estas Referencias).
4. **W27 (copy):** el título tiene que **nombrar de qué se habla** (el virus, la herramienta, con qué se compara), no solo la afirmación. Es el reparo más repetido en entrevista.
5. **W24:** `rank_score` entra como una señal más (AUC 0,68, la misma que la duración); no alcanza para decidir sola.
6. **Etiquetas:** los 36 "sí, pero…" son una etiqueta de tres niveles de hecho (excelente / sirve / no). Conviene registrarla así en el próximo ciclo.
