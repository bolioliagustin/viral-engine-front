# La subida directa del video es el camino garantizado; el link de YouTube es el atajo

Estado: aceptado el 2026-09-16, pendiente de implementación.

El proyecto se frenó en julio de 2026 porque la descarga desde YouTube en el VPS era poco fiable (proxies a 30 KB/s → jobs "completados" con links a YouTube en lugar de clips). El cliente objetivo (podcasters y coaches) es dueño de sus videos. Se decide que el producto acepte el archivo del creador como fuente primaria de video, además del link de YouTube: ningún job debe depender exclusivamente de la descarga de YouTube para producir clips. La cascada de descarga actual se mantiene y se mide por estrategia, pero deja de ser el único camino.

## Diseño acordado

- El archivo (MP4/MOV) sube desde el navegador directo al almacenamiento de objetos con URL prefirmada multipart; nunca pasa por el API. El worker lo lee con la API S3 que ya usa para los clips.
- Transcript híbrido: si el creador también pega el link de YouTube del mismo episodio, el transcript sale de los subtítulos de YouTube (centavos) y el archivo solo aporta el video; si no hay link, se transcribe el audio completo por reconocimiento de voz en Groq por trozos (la ruta por trozos existente está atada a OpenAI y debe pasar a Groq: ~US$0.06 vs ~US$0.54 por 90 minutos).
- El archivo fuente se borra a los 7 días (permite reintentar el job); los clips y segmentos crudos siguen su ciclo normal.

## Consequences

- Nuevo flujo de subida en frontend, backend (URL prefirmada) y worker (fuente alternativa al link).
- El límite de duración de la beta (90 minutos) aplica a ambas fuentes; hoy el pipeline activo no impone ningún tope.
