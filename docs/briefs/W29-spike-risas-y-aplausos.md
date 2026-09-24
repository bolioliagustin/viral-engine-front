# W29 — Spike: risas y aplausos

**Rama:** `feat/eval-spike-risas` · **Agente:** señales · **Ola:** 4 · **Base:** `integracion/mejora-ola-4` · **Depende de:** W19 (Referencias de charla) · **Categoría:** investigación medida (no toca el pipeline)

## Pregunta

¿Un detector de eventos de audio en CPU encuentra las risas y los aplausos de nuestros videos lo bastante rápido, y esas risas marcan el remate de los mejores momentos de charla?

## Contexto

Leé [`../PLAN_MEJORA.md`](../PLAN_MEJORA.md) §2 (la fila de solo texto), §5 (D11) y el Anexo B. En humor el chiste vive en la voz y en la reacción de la sala, que el transcript no registra. El VPS tiene 6 vCPU, sin GPU ([`../PROYECTO.md`](../PROYECTO.md) §10.1). El audio completo ya se descarga para `whisper_full`.

## Pasos

1. Elegí un modelo de eventos de audio que corra en CPU con ONNX o TFLite y tenga clases de risa y aplauso (por ejemplo YAMNet o PANNs). Justificá la elección por tamaño, licencia y costo de CPU. **Sin instalar paquetes del sistema** (`AGENTS.md`): todo va por `pip` dentro de `worker/.venv` o en un entorno aparte del spike.
2. Corré el detector sobre el audio completo de los videos de charla con Referencias (`B60BHDNFNxM` y `charla_humor_02`) y de un video de entrevista como control.
3. Medí:
   - **tiempo de CPU** por hora de audio;
   - **recall:** Referencias A de charla con una risa detectada entre el remate y 10 s después;
   - **precisión:** risas detectadas que caen en una Referencia (A o B) ± 10 s;
   - **falsos positivos** en el control.
4. Reportá en `worker/eval/runs/<fecha>-risas.json`, con un resumen en su README.

## Criterio de corte

- **Se corta** si tarda > 3 min de CPU por hora de audio o si el recall es < 0,5.
- **Si pasa**, proponé la integración: marcas "[risas]" o "[aplausos]" en las Líneas que recibe la Pasada A en charla y una señal de "reacción" para el Rankeador, con su costo. Va en una línea nueva.

## Criterios de aceptación

- [ ] Script reproducible con tests de la agregación de eventos a segmentos (sin modelo real, con fixtures).
- [ ] Reporte con tiempo, recall, precisión, falsos positivos y la decisión.
- [ ] Nada del pipeline de producción cambia.

## Entrega

Commit y push incremental. PR contra `integracion/mejora-ola-4`.
