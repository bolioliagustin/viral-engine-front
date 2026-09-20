-- ═══════════════════════════════════════════════════════════════════════════
-- analysis_cache — cachea el output del análisis Gemini para skip re-análisis
-- ═══════════════════════════════════════════════════════════════════════════
-- Pareja del transcript_cache: si el mismo video se procesa de nuevo con el
-- mismo modelo + tone + prompt_version, devolvemos el resultado guardado.
-- Ahorra ~30-60s + costo del modelo en cada cache hit.
--
-- Invalidación: cuando cambiamos los prompts del worker, bumpeamos la
-- constante PROMPT_VERSION en services/analysis_cache.py — eso fuerza
-- re-análisis de todos los videos (las filas viejas quedan pero no matchean).

CREATE TABLE IF NOT EXISTS analysis_cache (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  video_id        TEXT NOT NULL,
  model           TEXT NOT NULL,
  tone            TEXT NOT NULL DEFAULT 'profesional',
  prompt_version  TEXT NOT NULL DEFAULT 'v1',

  -- AnalysisResult serializado (incluye viral_moments + content_pieces)
  result          JSONB NOT NULL,

  -- Para diagnostics
  category_detected TEXT,            -- business / entertainment / etc
  prompt_chars      INTEGER,         -- tamaño del prompt enviado (debug)

  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  UNIQUE(video_id, model, tone, prompt_version)
);

-- Lookup index — el query más común
CREATE INDEX IF NOT EXISTS idx_analysis_cache_lookup
  ON analysis_cache(video_id, model, tone, prompt_version);

-- updated_at trigger
CREATE OR REPLACE FUNCTION analysis_cache_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS analysis_cache_updated_at ON analysis_cache;
CREATE TRIGGER analysis_cache_updated_at
  BEFORE UPDATE ON analysis_cache
  FOR EACH ROW
  EXECUTE FUNCTION analysis_cache_set_updated_at();

-- Cache del classifier de categoría (más liviano: solo string)
-- Lo guardamos por separado porque el classifier no depende del tone ni
-- de los prompts dinámicos — solo del video_id + model.
CREATE TABLE IF NOT EXISTS category_cache (
  video_id    TEXT NOT NULL,
  model       TEXT NOT NULL,
  category    TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (video_id, model)
);

COMMENT ON TABLE analysis_cache IS
  'Cache de output del análisis Gemini para evitar re-procesar el mismo video.';
COMMENT ON TABLE category_cache IS
  'Cache del classifier de categoría (lighter, separado del analysis_cache).';
