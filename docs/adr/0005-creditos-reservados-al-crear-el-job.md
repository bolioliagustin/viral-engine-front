# Los créditos se reservan al crear el job y se liberan si el job falla

Estado: **implementado el 2026-09-19** (F1, rama `feat/fiabilidad-beta`). Aceptado el 2026-09-16.

Hasta ahora el API solo validaba `credits > 0` al encolar y el worker descontaba al completar: con 1 crédito se podían encolar N jobs y, si el descuento fallaba, el job igual se entregaba. Se decide reservar 1 crédito de forma atómica al crear el job, liberarlo si el job termina en `failed`, y volver a reservar cuando el usuario reintenta un job fallido. Se descartó contar los jobs pendientes contra el saldo porque no cierra la carrera y es más difícil de explicar que "un job vale 1 crédito; si falla, te lo devolvemos".

## Consequences

- El saldo visible baja en el momento de encolar.
- Qué cuenta como job fallido a efectos de la devolución lo define el glosario (`CONTEXT.md`, término Job).

## Implementación (F1, 2026-09-19)

- **Reserva:** `POST /process` llama a la RPC `reserve_credit(p_user_id)` (`UPDATE users SET credits = credits - 1 WHERE credits > 0 RETURNING true`, atómica) ANTES de insertar el job; si devuelve `false` → `402`. El job se crea con `jobs.credit_reserved = true`. Si el insert del job falla después de reservar, se libera con `release_credit(p_user_id)` (evita cobrar un crédito por un job que no llegó a existir). `POST /jobs/:id/retry` repite la reserva — un reintento es un nuevo procesamiento y cuesta como tal, tanto si el original falló (ya se le devolvió el crédito) como si completó (ese crédito se gastó en ese resultado).
- **Devolución:** trigger `trg_release_credit_on_job_failed` (`BEFORE UPDATE ON jobs`, migración `creditos_reservados`) — cuando `status` pasa a `failed`, libera el crédito y pone `credit_reserved = false`. Se eligió un trigger de base en vez de un endpoint del backend porque la mayoría de los `failed` los marca el worker (`update_job_error`), no una llamada al API; el trigger los cubre a todos sin que nadie tenga que acordarse de llamar a `release_credit`.
- **Doble descuento evitado sin tocar el worker:** el worker sigue llamando a la RPC `deduct_user_credit(p_user_id, p_job_id, p_description)` al completar, sin ningún cambio de código. Esa función se redefinió (`CREATE OR REPLACE`, misma firma) para consultar `jobs.credit_reserved` del `p_job_id`: si es `true`, es un no-op (el crédito ya se cobró al encolar); si es `false` (jobs creados antes de esta migración, o por caminos que no pasan por `POST /process`), cae al comportamiento de siempre. La decisión de cobrar o no vive enteramente en SQL.
- **Límite conocido, no corregido acá:** el trigger libera el crédito cuando `jobs.status` pasa a `'failed'` literalmente. La definición de "Job fallido" en `CONTEXT.md` ("si ningún momento tiene clip, falla") es más estricta — hoy el worker solo pone `status='failed'` ante una excepción/timeout; un job que termina con 0 clips reales pero sin excepción sigue quedando `completed` y cobrando igual. Corregir eso requiere tocar `worker/main.py` (fuera del alcance de F1, que no tocó el worker).
