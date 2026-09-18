-- ═══════════════════════════════════════════════════════════════════════════
-- clip_edits.subtitle_style — habilitar 'tiktok_viral_v2' (W11)
-- ═══════════════════════════════════════════════════════════════════════════
-- docs/PLAN_CALIDAD.md §9 Fase 1: tiktok_viral_v2 (bloques cortos, MAYÚSCULAS,
-- palabra clave resaltada — services/clip_generator.py) es el nuevo default
-- del pipeline. El CHECK de clip_edits.subtitle_style solo permitía
-- 'tiktok_viral' | 'clean' | 'podcast'; sin esto, el editor no podría guardar
-- 'tiktok_viral_v2' aunque el frontend llegue a ofrecerlo (pendiente — ver
-- worker/WORKER.md).

ALTER TABLE "public"."clip_edits"
    DROP CONSTRAINT IF EXISTS "clip_edits_subtitle_style_check";

ALTER TABLE "public"."clip_edits"
    ADD CONSTRAINT "clip_edits_subtitle_style_check"
    CHECK (("subtitle_style" = ANY (ARRAY[
        'tiktok_viral_v2'::"text",
        'tiktok_viral'::"text",
        'clean'::"text",
        'podcast'::"text"
    ])));
