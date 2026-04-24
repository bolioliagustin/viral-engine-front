-- Fix subscription_status check constraint to allow LS webhook values
ALTER TABLE public.users
DROP CONSTRAINT IF EXISTS users_subscription_status_check;

ALTER TABLE public.users
ADD CONSTRAINT users_subscription_status_check
CHECK (subscription_status = ANY (ARRAY[
  'free', 'basic', 'pro',
  'active', 'canceled', 'cancelled', 'expired', 'unpaid', 'paused'
]));
