-- ═══════════════════════════════════════════════════════════════════════════
-- clip_edits — user-driven post-clip edits (Phase 3 foundation)
-- ═══════════════════════════════════════════════════════════════════════════
-- Stores user edits to a generated clip (overlay text override, style preset,
-- subtitle word corrections, trim start/end, music). The worker uses these
-- to re-render the clip via POST /api/clips/:id/regenerate.

CREATE TABLE IF NOT EXISTS clip_edits (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  content_result_id UUID NOT NULL REFERENCES content_results(id) ON DELETE CASCADE,
  user_id         UUID,                      -- nullable for now (auth not enforced everywhere)

  -- Title / overlay
  overlay_text    TEXT,                      -- user override of viral_overlay
  overlay_position TEXT CHECK (overlay_position IN ('top','center','bottom')),

  -- Style preset
  subtitle_style  TEXT CHECK (subtitle_style IN ('tiktok_viral','clean','podcast')),
  overlay_style   TEXT CHECK (overlay_style  IN ('tiktok_viral','question','stat')),

  -- Subtitle word corrections (JSON array: [{start, end, original, corrected}])
  word_corrections JSONB,

  -- Audio / trim
  trim_start_offset NUMERIC(6,2),            -- seconds to trim from start
  trim_end_offset   NUMERIC(6,2),            -- seconds to trim from end
  music_track_id    TEXT,                    -- id of selected background track

  -- State
  status          TEXT NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft','queued','processing','completed','failed')),
  rendered_clip_url TEXT,                    -- set when render completes
  error_message   TEXT,

  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_clip_edits_content_result ON clip_edits(content_result_id);
CREATE INDEX IF NOT EXISTS idx_clip_edits_user ON clip_edits(user_id);
CREATE INDEX IF NOT EXISTS idx_clip_edits_status ON clip_edits(status);

-- updated_at trigger
CREATE OR REPLACE FUNCTION clip_edits_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS clip_edits_updated_at ON clip_edits;
CREATE TRIGGER clip_edits_updated_at
  BEFORE UPDATE ON clip_edits
  FOR EACH ROW
  EXECUTE FUNCTION clip_edits_set_updated_at();

COMMENT ON TABLE clip_edits IS
  'User-driven post-clip edits. Worker reads queued rows and re-renders.';
