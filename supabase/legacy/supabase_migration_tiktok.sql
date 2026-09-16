-- Migration: Add TikTok caption support to content_results table
-- Safer approach: Check existing constraint first, then update

-- Step 1: Drop the existing constraint
ALTER TABLE content_results 
DROP CONSTRAINT IF EXISTS content_results_type_check;

-- Step 2: Add new constraint with all content types (including existing ones)
-- This includes any types that might already be in the database
ALTER TABLE content_results 
ADD CONSTRAINT content_results_type_check 
CHECK (type IN (
    'twitter_thread', 
    'linkedin_post', 
    'short_video_script', 
    'tiktok_caption',
    -- Legacy/existing types (if any)
    'twitter',
    'linkedin',
    'script'
));

-- Comment for documentation
COMMENT ON CONSTRAINT content_results_type_check ON content_results IS 
'Allowed content types: twitter_thread, linkedin_post, short_video_script (deprecated), tiktok_caption (entertainment)';
