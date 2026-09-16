-- S3: Transcription Cache Table
-- Run this in Supabase SQL Editor to create the transcription cache table.
-- This enables the worker to skip re-transcribing videos that have already been processed.

CREATE TABLE IF NOT EXISTS transcription_cache (
    video_id TEXT PRIMARY KEY,
    transcript JSONB NOT NULL,
    language TEXT,
    duration_seconds FLOAT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast lookups by video_id (already PK, but adding comment for clarity)
COMMENT ON TABLE transcription_cache IS 'S3: Caches Whisper transcriptions to avoid redundant API calls. Keyed by YouTube video_id.';

-- Auto-update updated_at on changes
CREATE OR REPLACE FUNCTION update_transcription_cache_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_transcription_cache_timestamp
    BEFORE UPDATE ON transcription_cache
    FOR EACH ROW
    EXECUTE FUNCTION update_transcription_cache_timestamp();
