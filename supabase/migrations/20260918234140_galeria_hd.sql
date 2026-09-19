-- ═══════════════════════════════════════════════════════════════════════════
-- Galería de clips + HD a pedido (W9-A, docs/PLAN_CALIDAD.md §9 Fase 1 W9)
-- ═══════════════════════════════════════════════════════════════════════════
-- docs/adr/0008-entrega-galeria-y-creditos.md: crédito por job (sin cambios,
-- ADR 0005), galería y HD ilimitados dentro de ese job. Preview 480x854 lo
-- llena el worker en una tarea futura (W9, mitad worker, va sobre la rama de
-- integración) — por eso preview_url queda NULL hoy en todos los clips.
--
-- hd_url / hd_status NO son columnas nuevas: se derivan en caliente de
-- clip_edits (edit_type='hd_upgrade', el mecanismo de re-render que ya
-- existe desde W7) en GET /status/:jobId — el estado de un pedido de HD es
-- ni más ni menos que el status de su fila en clip_edits (queued|processing|
-- completed|failed), igual que un re-render de estilo. edit_type es lo que
-- permite distinguirlo de una edición manual del usuario.

ALTER TABLE "public"."content_results"
    ADD COLUMN IF NOT EXISTS "preview_url" TEXT;

COMMENT ON COLUMN "public"."content_results"."preview_url" IS
    'Preview 480x854 de baja resolución para la galería (W9). NULL hasta que '
    'el worker la genere (pendiente, mitad worker de W9) — la UI cae a '
    'clip_url cuando falta, así que ningún job viejo se rompe.';

ALTER TABLE "public"."clip_edits"
    ADD COLUMN IF NOT EXISTS "edit_type" TEXT NOT NULL DEFAULT 'style';

ALTER TABLE "public"."clip_edits"
    DROP CONSTRAINT IF EXISTS "clip_edits_edit_type_check";

ALTER TABLE "public"."clip_edits"
    ADD CONSTRAINT "clip_edits_edit_type_check"
    CHECK (("edit_type" = ANY (ARRAY['style'::"text", 'hd_upgrade'::"text"])));

COMMENT ON COLUMN "public"."clip_edits"."edit_type" IS
    '"style" (default): edición manual del usuario (overlay, subtítulos, '
    'recorte — W7). "hd_upgrade" (W9-A): re-render en HD pedido desde la '
    'galería, POST /api/clips/:id/hd. El worker de hoy no distingue entre '
    'los dos (mismo target_width/height, mismo pipeline) — la distinción de '
    'calidad real queda pendiente (mitad worker de W9).';
