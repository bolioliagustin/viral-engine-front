# La galería entrega todos los candidatos viables; el crédito se cobra por job, no por clip

Estado: **propuesta, pendiente de confirmación de Agustín.** Fecha: 2026-09-18.

## Contexto

Hoy un job entrega 1/3/5 Momentos, todos renderizados directamente en HD (720×1280). `docs/ANALISIS_OPUS_CLIP.md` (§1, §6 fila B) muestra que Opus, sobre el mismo video de 77 minutos de nuestro golden set, entrega **42 clips** como galería ordenada por score, con preview en baja resolución y HD solo al pedido ("Descargar HD" / "Mejorar calidad"). El "Score visible" curvado de W10 (`CONTEXT.md`) ya resuelve la percepción del número; falta la cantidad. Aunque nuestros 5 Momentos fueran de igual calidad que 5 de los suyos, la sensación que se lleva el usuario es "me dio 5 clips regulares" contra "me dio 42 clips buenos" — es la brecha de percepción más grande después del encuadre (W5) y los subtítulos (W11).

Renderizar en HD **todos** los candidatos viables de un video de 60-90 min (objetivo: 1 cada 2-3 min, `docs/PLAN_CALIDAD.md` §9 Fase 1 W9) multiplica el costo de render por 6-8× respecto de hoy. Eso obliga a decidir dos cosas juntas: cuántos clips se muestran y cómo se paga por ellos.

## Decisión propuesta

1. **El crédito se sigue reservando y cobrando por job**, como hoy (ADR 0005): 1 crédito al encolar, se devuelve si el job falla. Esto **no cambia**.
2. **La galería completa y las descargas en HD son ilimitadas dentro de ese job.** Un usuario que pagó 1 crédito puede ver y descargar en HD tantos Momentos como el job haya producido — no hay un segundo cobro por clip ni un tope de descargas.
3. El worker evalúa **todos** los candidatos viables (no solo el `target` de hoy) y renderiza un **preview 480×854** (`veryfast`, barato) de cada uno; la galería los muestra ordenados por Score visible descendente.
4. El **HD 720p** (y más adelante 1080p) de un Momento puntual se genera **al primer pedido** — botón "Descargar" en la card — reutilizando el mecanismo de re-render que ya existe (`clip_edits`, W7), y queda cacheado **7 días** (mismo horizonte que ADR 0007, `storageExpireAt` de Opus es igual). Pedidos repetidos del mismo clip dentro de esos 7 días no vuelven a renderizar.
5. La Pasada B completa (copy final: título, descripción, hashtags, hilo, post, caption) también se puede diferir a "al pedido" para los candidatos que no llegan a HD — **no se implementa en esta tarea** (el worker sigue generando copy para todo lo que renderiza hoy); queda anotado para cuando se mida el costo real con datos de uso.

## Alternativas descartadas

- **Cobrar por clip descargado en vez de por job.** Es más "justo" en términos de costo real (el preview es barato, el HD y la Pasada B no), pero rompe la previsibilidad del precio ("1 crédito = 1 video procesado") y obliga a rediseñar todo el flujo de créditos y el checkout (ADR 0005 completo, `users.credits`, la lógica de reserva/devolución). Se descarta para la beta: el ICP (podcasters) valora poder decir "esto me sale 1 crédito" sin sorpresas, y no hay datos todavía de cuántos HD pide un usuario típico por job para calibrar un precio por clip.
- **Entregar todos los candidatos en HD directamente**, sin preview ni "al pedido". Multiplica el costo de render (CPU + storage R2) por la cantidad de candidatos (6-8× hoy) sin necesidad: la mayoría de los clips de una galería de 30+ nunca se descargan. Se descarta por costo — es la razón por la que Opus tampoco lo hace (§1 y §6 fila B del análisis: "así pueden permitirse 42 clips").

## Qué se decide ahora vs. qué queda para cuando haya datos de uso

**Se decide ahora** (esta ADR, implementado en `feat/galeria-clips`): crédito por job sin cambios; contrato del endpoint `POST /api/clips/:contentResultId/hd` (encola HD si no existe, devuelve la URL si ya está); campos `preview_url` (worker, pendiente) y el estado de HD derivado de `clip_edits.edit_type='hd_upgrade'` (sin columnas nuevas de estado — ver migración `galeria_hd`); la galería en el frontend (grilla + filtro "todos / mejores").

**Queda para cuando haya datos de uso** (no se implementa en esta tarea):
- Si el crédito por job sigue siendo sostenible cuando el worker realmente evalúe 15-20+ candidatos por video en vez de 5 (el costo de LLM de la Pasada A y los previews sube con la cantidad, aunque los previews sean baratos individualmente — `docs/ANALISIS_OPUS_CLIP.md` §6 fila B).
- Si el HD debería expirar del cache antes de los 7 días cuando el volumen de storage en R2 lo justifique.
- Si conviene diferir la Pasada B completa (copy) a "al pedido" también, o si el costo de generarla para todos los candidatos es lo bastante bajo como para no molestarse (hoy la Pasada B es la parte cara de la evaluación, no el render).
- Un tope razonable a "ilimitado dentro del job" si en la práctica alguien pide HD de decenas de clips de un mismo job de forma que rompa el presupuesto de CPU del VPS — hoy no hay evidencia de que esto pase.

## Consequences

- El modelo de créditos actual (ADR 0005) queda intacto; esta ADR no lo reabre.
- El worker necesita, en una tarea futura (la mitad worker de W9, sobre la rama de integración — fuera del alcance de `feat/galeria-clips`): evaluar más candidatos que el `target` actual, renderizar el preview 480×854 de cada uno y escribir `content_results.preview_url`; y hacer que `clip_edit_processor.py` entienda `edit_type='hd_upgrade'` para renderizar a una resolución mayor que hoy (hoy el re-render de un edit usa el mismo `target_width=720, target_height=1280` que el original — sin ese cambio, "HD" no sube la calidad, solo repite el render).
- La galería tiene que degradar bien con jobs viejos (sin `preview_url`, 5 Momentos): se implementa como el caso por defecto, no como una excepción.
