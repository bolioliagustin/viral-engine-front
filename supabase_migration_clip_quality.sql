-- Migration: Calidad de clips post-producción (Plan mejora clips)
-- clip_quality_issues: JSON array de flags (incomplete_tail, late_hook, clip_not_rendered, etc.)
-- clip_generation_error: mensaje de error si el MP4 no se generó (fallback YouTube)

ALTER TABLE content_results
  ADD COLUMN IF NOT EXISTS clip_quality_issues JSONB,
  ADD COLUMN IF NOT EXISTS clip_generation_error TEXT;

COMMENT ON COLUMN content_results.clip_quality_issues IS
  'Array JSON de flags de calidad: incomplete_tail, late_hook, whisper_mismatch_first, clip_not_rendered, clip_generation_failed.';
COMMENT ON COLUMN content_results.clip_generation_error IS
  'Error truncado si la generación del clip MP4 falló y se usó fallback YouTube.';
