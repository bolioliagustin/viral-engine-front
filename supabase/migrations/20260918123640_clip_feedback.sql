-- ═══════════════════════════════════════════════════════════════════════════
-- clip_feedback — etiqueta humana "¿lo publicarías tal cual?" (W7)
-- ═══════════════════════════════════════════════════════════════════════════
-- Fuente de verdad de calidad (docs/PLAN_CALIDAD.md §2): el juez (score_judge
-- en content_results) es un proxy automático que se calibra contra esta
-- etiqueta humana, no al revés. Un mismo usuario puede etiquetar el mismo
-- clip más de una vez (se guarda historial completo; la UI muestra y
-- reemplaza la última fila por content_result_id + user_id).

CREATE TABLE IF NOT EXISTS "public"."clip_feedback" (
    "id" UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "content_result_id" UUID NOT NULL REFERENCES "public"."content_results"("id") ON DELETE CASCADE,
    "user_id" UUID NOT NULL,
    "posteable" BOOLEAN NOT NULL,
    "motivo" TEXT CHECK (
        "motivo" IN (
            'arranca_mal',
            'termina_mal',
            'momento_flojo',
            'subtitulos_mal',
            'se_ve_mal',
            'copy_malo',
            'otro'
        )
    ),
    "comentario" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE "public"."clip_feedback" IS
    'Etiqueta humana "lo publicaría tal cual sí/no" por clip (Momento). Fuente de verdad de calidad; score_judge se calibra contra esto (PLAN_CALIDAD.md §2, W7).';
COMMENT ON COLUMN "public"."clip_feedback"."content_result_id" IS
    'FK a content_results. Un momento tiene 3 filas de content_results (una por pieza de copy); el feedback se da sobre el clip, así que aplica a cualquiera de las 3 (el frontend etiqueta la primera que carga la card).';
COMMENT ON COLUMN "public"."clip_feedback"."posteable" IS
    'true = "sí, lo publicaría tal cual". false = "no" (requiere motivo en la UI, aunque no es obligatorio a nivel de esquema).';
COMMENT ON COLUMN "public"."clip_feedback"."motivo" IS
    'Solo aplica cuando posteable=false. Lista fija del brief de W7: arranca_mal, termina_mal, momento_flojo, subtitulos_mal, se_ve_mal, copy_malo, otro.';

CREATE INDEX IF NOT EXISTS "idx_clip_feedback_content_result" ON "public"."clip_feedback" USING "btree" ("content_result_id");
CREATE INDEX IF NOT EXISTS "idx_clip_feedback_user" ON "public"."clip_feedback" USING "btree" ("user_id");

ALTER TABLE "public"."clip_feedback" ENABLE ROW LEVEL SECURITY;

-- El usuario autenticado inserta y ve solo sus propias filas. No hay policy
-- de UPDATE/DELETE: no se corrige una etiqueta, se agrega una fila nueva
-- (historial completo; la UI/API se quedan con la más reciente).
CREATE POLICY "Users can insert own feedback" ON "public"."clip_feedback"
    FOR INSERT WITH CHECK (("auth"."uid"() = "user_id"));

CREATE POLICY "Users can view own feedback" ON "public"."clip_feedback"
    FOR SELECT USING (("auth"."uid"() = "user_id"));

-- service_role (backend/worker) bypassea RLS por defecto; sin policy extra
-- para ese rol, igual que el resto de las tablas del esquema.
