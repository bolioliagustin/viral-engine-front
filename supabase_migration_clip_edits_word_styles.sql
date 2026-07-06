-- ═══════════════════════════════════════════════════════════════════════════
-- clip_edits.word_styles — per-word subtitle styling (Phase E.2)
-- ═══════════════════════════════════════════════════════════════════════════
-- JSON array: [{start, end, style: "default"|"highlight"|"emphasis", color?: "#hex"}]

ALTER TABLE clip_edits
  ADD COLUMN IF NOT EXISTS word_styles JSONB;

COMMENT ON COLUMN clip_edits.word_styles IS
  'Per-word ASS style overrides applied during clip re-render.';
