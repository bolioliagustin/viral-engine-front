# W28 — Spike: mapa de calor de YouTube ("lo más repetido")

**Rama:** `feat/eval-spike-mapa-de-calor` · **Agente:** señales · **Ola:** 4 · **Base:** `integracion/mejora-ola-4` · **Depende de:** W19 (Referencias) · **Categoría:** investigación medida (no toca el pipeline)

## Pregunta

¿Podemos leer desde el VPS el gráfico de "lo más repetido" de YouTube para nuestros videos? Si se puede, ¿sus picos marcan los mejores momentos, medidos contra las Referencias?

## Contexto

Leé [`../PLAN_MEJORA.md`](../PLAN_MEJORA.md) §2 (la fila de solo texto) y §5 (D11). La descarga pasa por yt-dlp con proxies y por RapidAPI (`services/downloader.py`, `AGENTS.md`: proxy sticky). Hay videos que no tienen ese gráfico, porque YouTube lo muestra solo con suficientes vistas.

## Pasos

1. Para cada video del golden set, intentá obtener el mapa de calor:
   - con yt-dlp (campo `heatmap` del info dict), desde la Mac con proxies;
   - por RapidAPI, si lo expone.

   Registrá disponibilidad, método y latencia. Si podés, pedile al coordinador que lo pruebe desde el VPS, que es donde tiene que funcionar.
2. Detectá picos (por ejemplo, máximos locales sobre la mediana más un desvío) y medí contra las Referencias:
   - **precisión:** picos que caen dentro de un tramo de Referencia ± 10 s;
   - **recall:** Referencias A con un pico en su tramo.
3. Reportá en `worker/eval/runs/<fecha>-mapa-de-calor.json`, con un resumen en su README.

## Criterio de corte

- **Se corta** si está disponible en < 50 % de los videos del golden set o si la precisión de los picos es < 0,4.
- **Si pasa**, proponé en el reporte la integración:
  - chequeo de cobertura: Ventanas con un pico y sin candidato se vuelven a analizar;
  - señal extra para el Rankeador;

  con costo y tiempo estimados. La integración va en una línea nueva, no en esta rama.

## Criterios de aceptación

- [ ] Script reproducible en `worker/eval/` con tests de la detección de picos (sin red).
- [ ] Reporte con disponibilidad, precisión y recall por video, y la decisión (se corta o se integra).
- [ ] Nada del pipeline de producción cambia.

## Entrega

Commit y push incremental. PR contra `integracion/mejora-ola-4`.
