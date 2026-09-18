-- Migration: Add progress tracking columns to jobs table
-- This enables real-time progress updates for the Experience Transparency Update
-- Execute this in Supabase SQL Editor

-- Add current_step column (text, nullable)
ALTER TABLE jobs 
ADD COLUMN current_step text;

-- Add progress_percentage column (int4, nullable)
ALTER TABLE jobs 
ADD COLUMN progress_percentage int4;

-- Create index on current_step for faster queries on processing jobs
CREATE INDEX idx_jobs_current_step ON jobs(current_step) 
WHERE current_step IS NOT NULL;

-- Verify columns were added
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'jobs' 
AND column_name IN ('current_step', 'progress_percentage');
