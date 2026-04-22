-- ============================================================
-- Billing Migration (Lemon Squeezy)
-- Run this in the Supabase SQL Editor
-- ============================================================

-- 1. Add billing columns to users table
ALTER TABLE public.users
  ADD COLUMN IF NOT EXISTS billing_subscription_id TEXT,
  ADD COLUMN IF NOT EXISTS plan                     TEXT NOT NULL DEFAULT 'free';

-- 2. Index for webhook lookups by subscription ID
CREATE INDEX IF NOT EXISTS idx_users_billing_subscription_id
  ON public.users (billing_subscription_id);

-- 3. Ensure subscription_status has a sensible default
ALTER TABLE public.users
  ALTER COLUMN subscription_status SET DEFAULT 'free';

-- 4. Verify columns
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name   = 'users'
  AND column_name  IN ('billing_subscription_id', 'plan', 'subscription_status', 'credits')
ORDER BY ordinal_position;
