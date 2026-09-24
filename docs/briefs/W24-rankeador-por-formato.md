# W24 — Rankeador por Formato, calibrado

**Rama:** `feat/worker-rankeador-por-formato` (el spike de audio va en `feat/eval-spike-juez-que-escucha`) · **Agente:** ranking · **Ola:** 2 · **Base:** `integracion/mejora-ola-2` · **Depende de:** W22 (Formato) y etiquetas de Posteable por formato (Agustín y canarios) · **Categoría:** mejora

## Contexto

Leé [`../PLAN_MEJORA.md`](../PLAN_MEJORA.md) §1 (H10), §2 (la fila de juez/Jev), §5 (D8) y el Anexo A.2. Leé también [`../PLAN_CALIDAD.md`](../PLAN_CALIDAD.md) §5 (W12: la rueda gira sobre Posteable), `worker/eval/calibracion.py` y los términos Rankeador, Juez y Posteable de [`../../CONTEXT.md`](../../CONTEXT.md).

El juez y Jev anclan "compartir" en "dato útil / contraintuitivo / que da estatus". En humor hunden lo más gracioso: Caniggia 8,8 en Jev contra 18 del juez, Oso Yogi 8,2. `RANKER=jev` se eligió con un solo video de entrevista informativa.

## Comportamiento actual

- `_JUDGE_RUBRIC` (juez) y `QUESTIONS` (Jev) tienen una sola rúbrica para todo contenido.
- `ranker_is_jev()` decide globalmente.
- `calibracion.py` compara rankeadores contra `clip_feedback` sin distinguir formato.

## Comportamiento deseado

1. **Rúbrica por Formato** en el juez y en Jev.
   - **charla:**
     - gancho = "¿los primeros segundos plantean una situación, un personaje o una tensión que da ganas de ver cómo termina?";
     - retención = "¿la historia avanza sin relleno y llega al remate?";
     - compartir = "¿da risa o provoca una reacción que dan ganas de mandarle a alguien?".
   - **entrevista, monólogo y clase:** las anclas de hoy (dato, verdad incómoda, utilidad).

   Detrás de `RUBRICA_POR_FORMATO=on|off`.
2. **Calibración por Formato.** `calibracion.py` reporta, por Formato y por variante (juez hoy, juez con rúbrica por Formato, Jev hoy, Jev por Formato y, si el spike pasa, juez que escucha): `precision@k` (k = 3, 5, 10), gap y correlación punto-biserial contra Posteable, con n a la vista.
3. **Rankeador por Formato.** Una variable de entorno que mapea cada Formato a su rankeador (por ejemplo `RANKER_POR_FORMATO=charla:juez,entrevista:jev`). Sin ella, rige `RANKER` como hoy.
4. **Spike "juez que escucha"** (rama aparte, sin tocar el pipeline):
   - ¿OpenRouter acepta audio de entrada para un modelo Gemini flash? Verificalo primero con un clip de 60 s.
   - Si lo acepta: puntuá los clips etiquetados de charla con audio (≤ 90 s, extraído del Clip o del Preview en R2) + texto + rúbrica de charla.
   - Compará `precision@5` con las otras variantes.
   - **Se corta** si no se acepta audio, si cuesta > US$0,005 por candidato, o si la mejora de `precision@5` en charla es < 0,1.

## Etiquetas (insumo crítico)

G2 exige ≥ 40 clips etiquetados, ≥ 15 de charla. El coordinador y Agustín corren los videos del golden set como jobs reales en la cuenta de Agustín, desde la Ola 1, y los etiquetan en la galería. Si al empezar esta línea hay menos de 15 de charla, calibrá con lo que haya, reportalo con su n y marcá la decisión como provisoria.

## Criterios de aceptación

- [ ] Tests de construcción de rúbrica por Formato (juez y Jev) y del mapeo `RANKER_POR_FORMATO`, con valor inválido que cae a `RANKER` y se loguea.
- [ ] Reporte de calibración por Formato commiteado en `worker/eval/runs/<fecha>-calibracion-por-formato.json`, con n por celda.
- [ ] Reporte del spike de audio: se adopta o se corta, con números.
- [ ] Decisión: en charla, `precision@5` ≥ 0,8 con el rankeador elegido; en entrevista, sin regresión frente a Jev de hoy.
- [ ] ADR `0012-rankeador-por-formato.md`. `CONTEXT.md` (Rankeador) y `PROYECTO.md` §6 y §11 actualizados.
- [ ] La suite del worker está en verde.

## Fuera de alcance

Umbral y cupo de entrega (W25) · la Pasada A · cambiar el modelo del juez por fuera de la variante con audio.

## Entrega

Commit y push incremental. PR del rankeador contra `integracion/mejora-ola-2`; el spike, como reporte en `worker/eval/runs/` dentro del mismo PR o en uno chico aparte. Plantilla de `PLAN_MEJORA.md` §8.4.
