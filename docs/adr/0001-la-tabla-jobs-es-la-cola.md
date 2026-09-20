# La tabla `jobs` de Postgres es la cola de trabajo

El worker corre separado del API y el volumen es bajo (decenas de jobs por día). En vez de sumar Redis o RabbitMQ, la cola es la tabla `jobs` en Supabase: el API inserta filas en `pending`, el worker las sondea cada 3 segundos y las reclama con un `UPDATE … WHERE status = 'pending'` atómico, lo que permite varios workers sin doble procesamiento. La cola secundaria de ediciones (`clip_edits`) sigue el mismo patrón. Se eligió porque no agrega infraestructura, el estado de la cola es visible desde el dashboard y Supabase ya era la fuente de verdad.

## Consequences

- Latencia de hasta 3 s entre encolar y empezar; sin prioridades ni reintentos automáticos con backoff.
- No hay afinidad de worker: cualquier worker conectado a la misma base reclama jobs. Por eso el entorno de desarrollo usa un proyecto Supabase separado y nunca comparte base con el worker del VPS.
- Revisar esta decisión si se supera ~1 job por segundo o se necesita fan-out por etapa.
