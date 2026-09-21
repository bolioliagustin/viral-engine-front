-- ═══════════════════════════════════════════════════════════════════════════
-- content_results.title / description / hashtags — copy por clip (W10)
-- ═══════════════════════════════════════════════════════════════════════════
-- docs/PLAN_CALIDAD.md §9 Fase 0, docs/ANALISIS_OPUS_CLIP.md §2.4: lo que la
-- persona pega al publicar en YouTube Shorts / TikTok, además de las tres
-- piezas de copy actuales (hilo, post, caption). La Pasada B
-- (services/processor.py::generate_moment_copy_full) las llena junto con el
-- resto del copy; se repiten en las 3 filas de content_results de un mismo
-- momento, igual que hook/viral_overlay.

ALTER TABLE "public"."content_results"
    ADD COLUMN IF NOT EXISTS "title" TEXT,
    ADD COLUMN IF NOT EXISTS "description" TEXT,
    ADD COLUMN IF NOT EXISTS "hashtags" TEXT[];

COMMENT ON COLUMN "public"."content_results"."title" IS
    'Título del clip para publicar, ≤60 caracteres, patrón "Tema: ¡afirmación o pregunta!" (W10).';
COMMENT ON COLUMN "public"."content_results"."description" IS
    'Descripción de 2 oraciones (qué se ve + invitación a mirar) para publicar junto al clip (W10).';
COMMENT ON COLUMN "public"."content_results"."hashtags" IS
    '10 hashtags en español, sin acentos, CamelCase, con "#" — sin genéricos vacíos como #Viral (W10).';
