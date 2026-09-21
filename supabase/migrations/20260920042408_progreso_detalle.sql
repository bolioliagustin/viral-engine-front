-- ═══════════════════════════════════════════════════════════════════════════
-- Pantalla de progreso en dos fases (P1, docs/PROYECTO.md §14 Fase 1)
-- ═══════════════════════════════════════════════════════════════════════════
-- `jobs.current_step` sigue siendo TEXT libre (sin CHECK — ya lo era antes de
-- esta migración): el contrato de valores esperados (transcribing|
-- classifying|analyzing|evaluating|ranking|delivering|finalizing) vive en
-- código (worker W9-B emite, backend/frontend lo consumen tal cual) para no
-- romper jobs en curso si el enum crece. Lo único que agrega esta migración
-- es progress_detail: el worker (W9-B, pendiente — no se toca en esta rama)
-- lo va a escribir en cada actualización de progreso; hasta entonces queda
-- NULL y el frontend cae al comportamiento de hoy (barra + current_step
-- sin detalle).

ALTER TABLE "public"."jobs"
    ADD COLUMN IF NOT EXISTS "progress_detail" jsonb;

COMMENT ON COLUMN "public"."jobs"."progress_detail" IS
    'P1: detalle de progreso dentro de current_step — '
    '{"current": int, "total": int, "message": text, "clips_ready": int}. '
    'NULL en jobs viejos o mientras el worker no lo escriba (W9-B, pendiente). '
    'current_step (TEXT libre, sin CHECK): transcribing|classifying|'
    'analyzing|evaluating|ranking|delivering|finalizing — contrato en código '
    '(docs/PROYECTO.md §7), no en el esquema, para no romper jobs en curso '
    'si el worker agrega un paso nuevo.';
