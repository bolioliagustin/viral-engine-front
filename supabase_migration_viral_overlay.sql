-- Add viral_overlay column to content_results
-- Stores the short UPPERCASE title burned on TikTok/Reels clips
-- (hook corto ≤4 palabras que va como overlay visual)

ALTER TABLE content_results
  ADD COLUMN IF NOT EXISTS viral_overlay TEXT;

COMMENT ON COLUMN content_results.viral_overlay IS
  'Short UPPERCASE title burned onto the vertical clip (max 4 words, TikTok-style hook overlay).';
