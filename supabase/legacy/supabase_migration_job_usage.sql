-- ═══════════════════════════════════════════════════════════════════════════
-- job_usage_events — métricas granulares de costo LLM/Whisper por job
-- ═══════════════════════════════════════════════════════════════════════════
-- Una fila por llamada LLM o transcripción Whisper. El worker calcula
-- estimated_cost_usd con pricing.py y hace rollup en jobs.usage_summary
-- al finalizar cada job.

CREATE TABLE IF NOT EXISTS job_usage_events (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id              UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  user_id             UUID,
  event_type          TEXT NOT NULL DEFAULT 'llm_chat',
  provider            TEXT NOT NULL,
  task                TEXT NOT NULL,
  model               TEXT,
  moment_index        INT,
  input_tokens        INT NOT NULL DEFAULT 0,
  output_tokens       INT NOT NULL DEFAULT 0,
  reasoning_tokens    INT NOT NULL DEFAULT 0,
  audio_seconds       NUMERIC(10, 3),
  estimated_cost_usd  NUMERIC(12, 6) NOT NULL DEFAULT 0,
  cache_hit           BOOLEAN NOT NULL DEFAULT false,
  latency_ms          INT,
  metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_job_usage_events_job_id
  ON job_usage_events(job_id);

CREATE INDEX IF NOT EXISTS idx_job_usage_events_user_created
  ON job_usage_events(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_job_usage_events_created
  ON job_usage_events(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_job_usage_events_task
  ON job_usage_events(task);

-- Rollup denormalizado en jobs (actualizado por worker al completar/fallar)
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS usage_summary JSONB;

-- RLS: sin políticas para anon/authenticated — solo service_role accede
ALTER TABLE job_usage_events ENABLE ROW LEVEL SECURITY;

-- RPC para detalle admin sin duplicar lógica de rollup en Node
CREATE OR REPLACE FUNCTION get_job_usage_summary(p_job_id UUID)
RETURNS JSONB
LANGUAGE sql
STABLE
AS $$
  SELECT jsonb_build_object(
    'job_id', p_job_id,
    'event_count', COUNT(*)::int,
    'total_cost_usd', COALESCE(SUM(estimated_cost_usd), 0),
    'total_input_tokens', COALESCE(SUM(input_tokens), 0)::int,
    'total_output_tokens', COALESCE(SUM(output_tokens), 0)::int,
    'reasoning_tokens', COALESCE(SUM(reasoning_tokens), 0)::int,
    'whisper_seconds', COALESCE(SUM(audio_seconds) FILTER (WHERE task = 'whisper'), 0),
    'cache_hits', COUNT(*) FILTER (WHERE cache_hit)::int,
    'cost_avoided_usd', COALESCE(
      SUM((metadata->>'cost_avoided_usd')::numeric) FILTER (WHERE cache_hit),
      0
    ),
    'by_task', COALESCE(
      (SELECT jsonb_object_agg(task, task_cost)
       FROM (
         SELECT task, SUM(estimated_cost_usd) AS task_cost
         FROM job_usage_events
         WHERE job_id = p_job_id
         GROUP BY task
       ) t),
      '{}'::jsonb
    ),
    'by_model', COALESCE(
      (SELECT jsonb_object_agg(model, model_cost)
       FROM (
         SELECT model, SUM(estimated_cost_usd) AS model_cost
         FROM job_usage_events
         WHERE job_id = p_job_id AND model IS NOT NULL
         GROUP BY model
       ) m),
      '{}'::jsonb
    ),
    'by_provider', COALESCE(
      (SELECT jsonb_object_agg(provider, prov_cost)
       FROM (
         SELECT provider, SUM(estimated_cost_usd) AS prov_cost
         FROM job_usage_events
         WHERE job_id = p_job_id
         GROUP BY provider
       ) p),
      '{}'::jsonb
    )
  )
  FROM job_usage_events
  WHERE job_id = p_job_id;
$$;
