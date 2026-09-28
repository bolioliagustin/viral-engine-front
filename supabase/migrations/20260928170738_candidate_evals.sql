-- ═══════════════════════════════════════════════════════════════════════════
-- candidate_evals — Registro de candidatos (W30)
-- ═══════════════════════════════════════════════════════════════════════════
-- Una fila por Candidato evaluado en un job de producción: lo que el
-- pipeline ya calcula (rank_score de la Pasada A, Juez, Jev, flags de
-- calidad, w2_score, elegido/descartado) y hasta hoy se perdía o se pisaba
-- (analysis_cache.candidates_all se reescribe en cada job del mismo video y
-- la nota de Jev no se guardaba en ningún lado). Es la materia prima para
-- aprender a elegir (docs/ESTUDIO_ML_EVALUADOR.md §2.4 y §8.2): las
-- etiquetas (Referencias, clip_feedback) se cruzan después con
-- worker/eval/exportar_candidatos.py.
--
-- Solo INSERT: las filas no se pisan entre jobs. La escribe el worker con la
-- service key (bypassea RLS); no la lee ni el backend ni el frontend.

CREATE TABLE IF NOT EXISTS "public"."candidate_evals" (
    "id" UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "job_id" UUID NOT NULL REFERENCES "public"."jobs"("id") ON DELETE CASCADE,
    "video_id" TEXT NOT NULL,
    "video_duration" NUMERIC(10,3),
    "candidate_index" INTEGER NOT NULL,
    "delivery_index" INTEGER,
    "proposed_start" NUMERIC(10,3),
    "proposed_end" NUMERIC(10,3),
    "final_start" NUMERIC(10,3),
    "final_end" NUMERIC(10,3),
    "hook" TEXT,
    "lines_text" TEXT,
    "clip_text" TEXT,
    "rank_score" NUMERIC(8,3),
    "self_score" NUMERIC(8,3),
    "judge_hook" NUMERIC(6,3),
    "judge_retention" NUMERIC(6,3),
    "judge_shareability" NUMERIC(6,3),
    "judge_reasoning" TEXT,
    "jev_rank_score" NUMERIC(8,3),
    "jev_hook" NUMERIC(6,3),
    "jev_retention" NUMERIC(6,3),
    "jev_shareability" NUMERIC(6,3),
    "jev_confidence" NUMERIC(6,3),
    "usable" BOOLEAN NOT NULL DEFAULT true,
    "flags" JSONB NOT NULL DEFAULT '{}'::JSONB,
    "w2_score" NUMERIC(8,3),
    "selected" BOOLEAN NOT NULL DEFAULT false,
    "discard_reason" TEXT,
    "ranker" TEXT,
    "prompt_version" TEXT,
    "analysis_model" TEXT,
    "judge_model" TEXT,
    "jev_model" TEXT,
    "category" TEXT,
    "formato" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE "public"."candidate_evals" OWNER TO "postgres";

COMMENT ON TABLE "public"."candidate_evals" IS
    'Registro de candidatos (W30): una fila por Candidato evaluado en un job, sin pisar entre jobs. Rasgos para aprender a elegir; las etiquetas se cruzan con worker/eval/exportar_candidatos.py.';
COMMENT ON COLUMN "public"."candidate_evals"."video_id" IS
    'ID de YouTube (el mismo video_id de analysis_cache y de eval/referencias/<id>.json).';
COMMENT ON COLUMN "public"."candidate_evals"."candidate_index" IS
    'Posición 1-based del Candidato en la lista de la Pasada A (CandidateEval.index).';
COMMENT ON COLUMN "public"."candidate_evals"."delivery_index" IS
    'content_results.moment_index del clip entregado (1..n) si el Candidato fue elegido; null si se descartó. Une con clip_feedback vía content_results(job_id, moment_index).';
COMMENT ON COLUMN "public"."candidate_evals"."proposed_start" IS
    'Inicio propuesto por la Pasada A (segundos absolutos del video). proposed_end: fin propuesto.';
COMMENT ON COLUMN "public"."candidate_evals"."final_start" IS
    'Inicio del corte evaluado tras el ancla de frases (W1) o el refinamiento numérico; null si no hubo clip. final_end: fin.';
COMMENT ON COLUMN "public"."candidate_evals"."lines_text" IS
    'Texto de las Líneas del transcript que tocan el tramo final (o el propuesto si no hubo corte).';
COMMENT ON COLUMN "public"."candidate_evals"."clip_text" IS
    'Texto de Whisper del clip, el que leyeron el Juez y Jev.';
COMMENT ON COLUMN "public"."candidate_evals"."rank_score" IS
    'rank_score de la Pasada A: auto-score (hook+retention+shareability) menos la penalización de borde de segmento.';
COMMENT ON COLUMN "public"."candidate_evals"."jev_rank_score" IS
    'Nota de Jev normalizada a la escala 0..30 del Juez; jev_hook/jev_retention/jev_shareability son las tres notas 0–4 y jev_confidence la confianza media. Null si Jev no corrió o falló.';
COMMENT ON COLUMN "public"."candidate_evals"."flags" IS
    'Flags de calidad del CandidateEval (hook_not_found, payoff_not_found, bad_segment, insufficient_source, timestamps_suspect, late_hook, incomplete_tail, min_duration_reverted, density_out_of_range).';
COMMENT ON COLUMN "public"."candidate_evals"."w2_score" IS
    'score_candidate(): la nota con la que select_finalists rankeó (Juez o Jev, menos penalizaciones).';
COMMENT ON COLUMN "public"."candidate_evals"."ranker" IS
    'Rankeador que ordenó el job: "juez" o "jev" (RANKER).';
COMMENT ON COLUMN "public"."candidate_evals"."prompt_version" IS
    'Versión efectiva de la Pasada A (PROMPT_VERSION + fuente del transcript), la misma clave de analysis_cache.';
COMMENT ON COLUMN "public"."candidate_evals"."formato" IS
    'Formato de contenido (W22); null mientras no exista. category es la Categoría de hoy.';

CREATE INDEX IF NOT EXISTS "idx_candidate_evals_video_id" ON "public"."candidate_evals" USING "btree" ("video_id");
CREATE INDEX IF NOT EXISTS "idx_candidate_evals_job_id" ON "public"."candidate_evals" USING "btree" ("job_id");

-- RLS y grants iguales a job_usage_events: RLS prendido y sin policies, así
-- que anon/authenticated no ven nada; el worker escribe con service_role.
ALTER TABLE "public"."candidate_evals" ENABLE ROW LEVEL SECURITY;

GRANT ALL ON TABLE "public"."candidate_evals" TO "anon";
GRANT ALL ON TABLE "public"."candidate_evals" TO "authenticated";
GRANT ALL ON TABLE "public"."candidate_evals" TO "service_role";
