# La Pasada A de un video largo corre por Ventanas con solape

Estado: **propuesta, detrás de `SELECCION_POR_VENTANAS` (default `off`) hasta pasar G1.** Fecha: 2026-09-28. Línea: W21 ([brief](../briefs/W21-pasada-a-por-ventanas.md), [`PLAN_MEJORA.md`](../PLAN_MEJORA.md) §6 Ola 1).

## Contexto

La Pasada A recibe el transcript completo en una llamada y pide `candidate_count(duración)` candidatos (tope 30) "ordenados del mejor al peor". El modelo los devuelve en orden cronológico y agota el cupo antes del final:

- B60BHDNFNxM (111 min, transcript correcto): 30 candidatos en los primeros 51 min, cuartos [17, 13, 0, 0]. Con el mismo prompt en 4 ventanas de ~28 min, la segunda hora pasó de 0 a 16 candidatos y las Referencias A completas de 3 a 10 de 15; una historia se perdió en el borde entre dos ventanas sin solape (`PLAN_MEJORA.md`, Anexo A.1).
- Baseline del tier `seleccion` contra las 121 Referencias validadas por Agustín (`worker/eval/runs/2026-09-28-seleccion-baseline-validado.json`): `recall_completo` A 33 % macro (~23 % en videos largos) y `min_cuarto` 7 %. En business_spanish_01, 8 de los 17 aceptados están en el último cuarto, donde la Pasada A pone 5 de 90 candidatos.
- Los borradores de Sonnet 5 en una sola llamada cubren parejo (cuarto mínimo 13–22 %): el sesgo es del modelo y el prompt actuales, no del problema.

## Decisión

Con `SELECCION_POR_VENTANAS=on` y un transcript con Líneas, la Pasada A corre por Ventanas (`worker/services/ventanas.py`, orquestado en `moment_selector.select_moments`):

1. **Ventanas de 20 min con 3 min de solape** (`VENTANA_MIN`, `VENTANA_SOLAPE_SEG`): núcleos [k·20, (k+1)·20) que ven además 90 s de cada lado. Una última Ventana de menos de 8 min se une a la anterior; un video de hasta 25 min usa una sola (la pasada de siempre). Con el solape, cada una de las 80 Referencias A validadas entra entera en al menos una Ventana (medido offline sobre los 7 transcripts del golden set; 4 cruzan un borde de núcleo).
2. **Cupo proporcional a los minutos**, mínimo 3 por Ventana, **total = `candidate_count` (≤ 30)** hasta que W23 abarate la evaluación: más cobertura sin más candidatos que evaluar.
3. **Llamadas en paralelo** (≤ 4) con el mismo prompt; el contexto dice la duración total y el rango de la Ventana, y los timestamps son absolutos.
4. **Unión**: deduplicación (solape > 50 % del más corto), fusión de la misma historia (contiguos a ≤ 5 s, total ≤ 120 s, misma Ventana o empalme en el solape; `VENTANAS_FUSION`) y orden por puesto dentro de la Ventana.
5. **Fallos**: una Ventana que falla no tira el job; si fallan todas, pasada única.
6. **Caché**: `PROMPT_VERSION` v8 → v10 (v9 tiene filas de un experimento) y el flag entra en la versión efectiva (`+ventanas`, mecanismo de W18).

## Alternativas descartadas

- **Un modelo más caro en una sola llamada** (Sonnet 5, Gemini Pro). Los borradores de Sonnet 5 cubren parejo, pero cuestan 2–3× por video (US$0,13–0,33 contra ~US$0,15) y agregan latencia de razonamiento. El plan pide una variable por vez: primero se mide la estructura con el modelo de hoy; el modelo más fuerte se prueba aparte, con tope de US$6 y el recall marcado como circular (las Referencias salieron de borradores de Sonnet).
- **Más candidatos en una pasada.** Subir el tope no corrige el orden cronológico: el modelo sigue llenando el principio primero, y cada candidato extra cuesta evaluación completa (~US$0,0015 y tiempo) hasta W23.
- **Ventanas sin solape** (Anexo A.1). Pierden las historias que cruzan el borde; el solape de 3 min cuesta ~12 % más de tokens de entrada.
- **Repartir por cantidad fija por Ventana** (8 × 4 del Anexo A.1). Castiga a las Ventanas cortas y no escala con la duración; el cupo proporcional mantiene el total.

## Consecuencias

- Entre 3 y 6 llamadas por video de 1–2 h en vez de 1, con ~10–15 % más de tokens de entrada por el solape y el prompt repetido. El gate G1 exige costo de la Pasada A ≤ +25 % y latencia ≤ +20 s.
- Si una Ventana falla, el video queda con un hueco de cobertura en ese tramo: se loguea (`_pasada_a.ventanas_fallidas`), no se reintenta aparte.
- El orden de `viral_moments` deja de ser "lo que devolvió el modelo" y pasa a ser por puesto dentro de la Ventana. `rank_and_prune_candidates` y el resto del pipeline no cambian.
- La fusión puede producir candidatos de hasta 120 s. Si la medición muestra que no baja `historias_partidas`, se apaga con `VENTANAS_FUSION=off`.
- W22 (Formatos) construye sobre esto: el foco por formato va en el prompt que ve cada Ventana.
