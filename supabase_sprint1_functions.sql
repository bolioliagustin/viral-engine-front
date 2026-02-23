-- ========================================
-- Sprint 1: Atomic Credit Deduction Function
-- ========================================
-- This function ensures atomic deduction of credits, preventing race conditions

CREATE OR REPLACE FUNCTION deduct_user_credit(
    p_user_id UUID,
    p_job_id UUID,
    p_description TEXT
)
RETURNS TABLE(success BOOLEAN, new_credits INT, message TEXT) AS $$
DECLARE
    updated_credits INT;
BEGIN
    -- Atomic decrement with check
    UPDATE users 
    SET credits = credits - 1 
    WHERE id = p_user_id AND credits > 0
    RETURNING credits INTO updated_credits;
    
    -- Check if update succeeded
    IF updated_credits IS NULL THEN
        -- Either user not found or no credits
        RETURN QUERY SELECT FALSE, 0, 'Insufficient credits or user not found'::TEXT;
    ELSE
        -- Log transaction
        INSERT INTO transactions (user_id, type, credits, description)
        VALUES (
            p_user_id, 
            'usage',
            -1, 
            p_description || ' [Job: ' || p_job_id::text || ']'
        );
        
        RETURN QUERY SELECT TRUE, updated_credits, 'Credit deducted successfully'::TEXT;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- ========================================
-- Helper function to check for duplicate jobs
-- ========================================
CREATE OR REPLACE FUNCTION check_duplicate_job(
    p_user_id UUID,
    p_video_url TEXT,
    p_days_back INT DEFAULT 7
)
RETURNS TABLE(has_duplicate BOOLEAN, existing_job_id UUID) AS $$
DECLARE
    found_job_id UUID;
BEGIN
    -- Check for completed job with same URL in last N days
    SELECT id INTO found_job_id
    FROM jobs
    WHERE user_id = p_user_id
    AND video_url = p_video_url
    AND status = 'completed'
    AND created_at > NOW() - (p_days_back || ' days')::INTERVAL
    LIMIT 1;
    
    IF found_job_id IS NOT NULL THEN
        RETURN QUERY SELECT TRUE, found_job_id;
    ELSE
        RETURN QUERY SELECT FALSE, NULL::UUID;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- Test the functions
-- SELECT * FROM deduct_user_credit('user-uuid-here'::UUID, 'job-uuid-here'::UUID, 'Test video');
-- SELECT * FROM check_duplicate_job('user-uuid-here'::UUID, 'https://youtube.com/watch?v=test');
