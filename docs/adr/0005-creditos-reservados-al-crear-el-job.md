# Los créditos se reservan al crear el job y se liberan si el job falla

Estado: aceptado el 2026-09-16, pendiente de implementación.

Hasta ahora el API solo validaba `credits > 0` al encolar y el worker descontaba al completar: con 1 crédito se podían encolar N jobs y, si el descuento fallaba, el job igual se entregaba. Se decide reservar 1 crédito de forma atómica al crear el job, liberarlo si el job termina en `failed`, y volver a reservar cuando el usuario reintenta un job fallido. Se descartó contar los jobs pendientes contra el saldo porque no cierra la carrera y es más difícil de explicar que "un job vale 1 crédito; si falla, te lo devolvemos".

## Consequences

- El saldo visible baja en el momento de encolar.
- Qué cuenta como job fallido a efectos de la devolución lo define el glosario (`CONTEXT.md`, término Job).
