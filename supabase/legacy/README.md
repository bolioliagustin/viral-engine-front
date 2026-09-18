# SQL histórico (aplicado a mano)

Estos archivos se ejecutaron manualmente en el SQL Editor de Supabase entre febrero y julio de 2026. **No son la fuente de verdad del esquema**: las tablas base (`users`, `jobs`, `content_results`, `transactions`) se crearon desde el dashboard y nunca se versionaron. Desde septiembre de 2026 el esquema se versiona con Supabase CLI en `supabase/migrations/` (ADR 0006); estos archivos quedan solo como referencia de qué cambió y por qué.

Orden aproximado de aplicación:

| Archivo | Qué agregó |
|---|---|
| `supabase_migration_saas_phase1.sql` | Trigger de alta de usuario (5 créditos), RLS y políticas |
| `supabase_sprint1_functions.sql` | RPCs `deduct_user_credit` y `check_duplicate_job` |
| `supabase_migration_progress_tracking.sql` | `jobs.current_step`, `jobs.progress_percentage` |
| `supabase_migration_tiktok.sql` | Tipos de `content_results` (incl. `tiktok_caption`) |
| `supabase_migration_viral_overlay.sql` | `content_results.viral_overlay` |
| `supabase_migration_s3_cache.sql` | Tabla `transcription_cache` |
| `supabase_migration_billing.sql` | `users.billing_subscription_id`, `users.plan` |
| `supabase_migration_fix_status_constraint.sql` | Valores de `subscription_status` para Lemon Squeezy |
| `supabase_migration_clip_edits.sql` | Tabla `clip_edits` |
| `supabase_migration_clip_edit_cache.sql` | `content_results.raw_clip_url`, `whisper_words` |
| `supabase_migration_analysis_cache.sql` | Tablas `analysis_cache` y `category_cache` |
| `supabase_migration_ai_quality.sql` | Scores del juez, métricas de calidad, `jobs.tone`, perfil del creador |
| `supabase_migration_clip_quality.sql` | `content_results.clip_quality_issues`, `clip_generation_error` |
| `supabase_migration_clip_edits_word_styles.sql` | `clip_edits.word_styles` |
| `supabase_migration_job_usage.sql` | Tabla `job_usage_events`, `jobs.usage_summary`, RPC `get_job_usage_summary` |
