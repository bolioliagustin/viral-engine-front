-- Migration: Plan calidad IA (Fases 4 y 5)
-- Fase 4: scoring calibrado (juez independiente), flag de verificación y
--         métricas de calidad del clip (coverage de subs, densidad de palabras).
-- Fase 5: personalización — tono por job y perfil del creador en users.
-- Ejecutar en el SQL Editor de Supabase.

-- ── Fase 4: content_results ──────────────────────────────────────────────────
ALTER TABLE content_results
  ADD COLUMN IF NOT EXISTS score_llm JSONB,
  ADD COLUMN IF NOT EXISTS score_judge JSONB,
  ADD COLUMN IF NOT EXISTS verification_failed BOOLEAN,
  ADD COLUMN IF NOT EXISTS sub_coverage REAL,
  ADD COLUMN IF NOT EXISTS words_per_sec REAL;

COMMENT ON COLUMN content_results.score_llm IS
  'Scores autoevaluados por el modelo de análisis (pasada A): {"hook":n,"retention":n,"shareability":n}. Se guardan para calibración juez-vs-análisis.';
COMMENT ON COLUMN content_results.score_judge IS
  'Scores del juez independiente (MODEL_JUDGE) contra rúbrica anclada, evaluando el texto Whisper real del clip final: {"hook":n,"retention":n,"shareability":n,"reasoning":"..."}.';
COMMENT ON COLUMN content_results.verification_failed IS
  'True cuando first_phrase Y last_phrase del análisis NO matchean el audio real (Whisper) — el corte probablemente no corresponde al momento elegido.';
COMMENT ON COLUMN content_results.sub_coverage IS
  'Fracción 0-1 del clip cubierta por subtítulos (métrica de calidad; target >= 0.9).';
COMMENT ON COLUMN content_results.words_per_sec IS
  'Densidad de palabras del clip (words Whisper / duración). Muy bajo = clip con silencio/relleno.';

-- ── Fase 5: jobs.tone ────────────────────────────────────────────────────────
ALTER TABLE public.jobs
  ADD COLUMN IF NOT EXISTS tone TEXT;

COMMENT ON COLUMN public.jobs.tone IS
  'Tono elegido por el usuario al crear el job: profesional | sarcastico | motivador | casual. NULL = profesional.';

-- ── Fase 5: perfil del creador en users ─────────────────────────────────────
ALTER TABLE public.users
  ADD COLUMN IF NOT EXISTS display_name TEXT,
  ADD COLUMN IF NOT EXISTS professional_title TEXT;

COMMENT ON COLUMN public.users.display_name IS
  'Nombre público del creador — se inyecta en los prompts de generación de copy.';
COMMENT ON COLUMN public.users.professional_title IS
  'Título/profesión del creador (ej. "Growth Marketer") — se inyecta en los prompts.';

-- Verificación
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'content_results'
  AND column_name IN ('score_llm', 'score_judge', 'verification_failed', 'sub_coverage', 'words_per_sec');
