-- ═══════════════════════════════════════════════════════════════════════════
-- clip_edit_cache — acelerar re-renders del editor post-clip
-- ═══════════════════════════════════════════════════════════════════════════
-- Plan C: cachear el segmento crudo (sin subs ni overlay) en R2 y los words
-- de Whisper en JSONB. Cuando el usuario re-renderiza desde el editor:
--
--   1. Si content_results.raw_clip_url existe → bajar de R2 (rápido, sin
--      yt-dlp ni dependencia de RapidAPI).
--   2. Si content_results.whisper_words existe → usar esos words (skip
--      llamada a Whisper API: ~10s + ~$0.006/min ahorrados).
--
-- Ambos campos son opcionales — si están vacíos el processor cae al path
-- viejo (yt-dlp + Whisper en runtime).

ALTER TABLE content_results
  ADD COLUMN IF NOT EXISTS raw_clip_url TEXT,
  ADD COLUMN IF NOT EXISTS whisper_words JSONB;

COMMENT ON COLUMN content_results.raw_clip_url IS
  'URL en R2 del segmento crudo (sin subs/overlay). Usado por el editor para re-renderizar sin re-descargar de YouTube.';

COMMENT ON COLUMN content_results.whisper_words IS
  'Cache del output de Whisper per-clip: {"words": [...], "segments": [...]}. Evita re-llamar la API en cada re-render.';
