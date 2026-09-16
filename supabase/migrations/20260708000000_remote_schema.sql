


SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;


COMMENT ON SCHEMA "public" IS 'standard public schema';



CREATE EXTENSION IF NOT EXISTS "pg_stat_statements" WITH SCHEMA "extensions";






CREATE EXTENSION IF NOT EXISTS "pgcrypto" WITH SCHEMA "extensions";






CREATE EXTENSION IF NOT EXISTS "supabase_vault" WITH SCHEMA "vault";






CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA "extensions";






CREATE OR REPLACE FUNCTION "public"."analysis_cache_set_updated_at"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;


ALTER FUNCTION "public"."analysis_cache_set_updated_at"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."calculate_time_saved"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
  -- Estimate: 15 min per Twitter thread, 20 min per LinkedIn, 30 min per script
  IF NEW.type = 'twitter_thread' THEN
    NEW.time_saved_minutes := 15;
  ELSIF NEW.type = 'linkedin_post' THEN
    NEW.time_saved_minutes := 20;
  ELSIF NEW.type = 'short_script' THEN
    NEW.time_saved_minutes := 30;
  ELSE
    NEW.time_saved_minutes := 5;
  END IF;
  
  RETURN NEW;
END;
$$;


ALTER FUNCTION "public"."calculate_time_saved"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."check_duplicate_job"("p_user_id" "uuid", "p_video_url" "text", "p_days_back" integer DEFAULT 7) RETURNS TABLE("has_duplicate" boolean, "existing_job_id" "uuid")
    LANGUAGE "plpgsql"
    AS $$
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
$$;


ALTER FUNCTION "public"."check_duplicate_job"("p_user_id" "uuid", "p_video_url" "text", "p_days_back" integer) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."clip_edits_set_updated_at"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;


ALTER FUNCTION "public"."clip_edits_set_updated_at"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."deduct_user_credit"("p_user_id" "uuid", "p_job_id" "uuid", "p_description" "text") RETURNS TABLE("success" boolean, "new_credits" integer, "message" "text")
    LANGUAGE "plpgsql"
    AS $$
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
$$;


ALTER FUNCTION "public"."deduct_user_credit"("p_user_id" "uuid", "p_job_id" "uuid", "p_description" "text") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."get_job_usage_summary"("p_job_id" "uuid") RETURNS "jsonb"
    LANGUAGE "sql" STABLE
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


ALTER FUNCTION "public"."get_job_usage_summary"("p_job_id" "uuid") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."handle_new_user"() RETURNS "trigger"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
BEGIN
  INSERT INTO public.users (id, email, name, avatar_url, credits)
  VALUES (
    new.id, 
    new.email, 
    new.raw_user_meta_data->>'full_name', 
    new.raw_user_meta_data->>'avatar_url',
    5 -- Default free credits
  );
  RETURN new;
END;
$$;


ALTER FUNCTION "public"."handle_new_user"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."update_job_metrics"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
  UPDATE jobs SET
    total_time_saved = (
      SELECT COALESCE(SUM(time_saved_minutes), 0) 
      FROM content_results WHERE job_id = NEW.job_id
    ),
    avg_virality_score = (
      SELECT ROUND(AVG((COALESCE(score_hook,5) + COALESCE(score_retention,5) + COALESCE(score_shareability,5)) / 3.0), 1)
      FROM content_results 
      WHERE job_id = NEW.job_id AND type != 'summary'
    )
  WHERE id = NEW.job_id;
  
  RETURN NEW;
END;
$$;


ALTER FUNCTION "public"."update_job_metrics"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."update_transcription_cache_timestamp"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


ALTER FUNCTION "public"."update_transcription_cache_timestamp"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."update_updated_at"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;


ALTER FUNCTION "public"."update_updated_at"() OWNER TO "postgres";

SET default_tablespace = '';

SET default_table_access_method = "heap";


CREATE TABLE IF NOT EXISTS "public"."analysis_cache" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "video_id" "text" NOT NULL,
    "model" "text" NOT NULL,
    "tone" "text" DEFAULT 'profesional'::"text" NOT NULL,
    "prompt_version" "text" DEFAULT 'v1'::"text" NOT NULL,
    "result" "jsonb" NOT NULL,
    "category_detected" "text",
    "prompt_chars" integer,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."analysis_cache" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."category_cache" (
    "video_id" "text" NOT NULL,
    "model" "text" NOT NULL,
    "category" "text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."category_cache" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."clip_edits" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "content_result_id" "uuid" NOT NULL,
    "user_id" "uuid",
    "overlay_text" "text",
    "overlay_position" "text",
    "subtitle_style" "text",
    "overlay_style" "text",
    "word_corrections" "jsonb",
    "trim_start_offset" numeric(6,2),
    "trim_end_offset" numeric(6,2),
    "music_track_id" "text",
    "status" "text" DEFAULT 'draft'::"text" NOT NULL,
    "rendered_clip_url" "text",
    "error_message" "text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "word_styles" "jsonb",
    CONSTRAINT "clip_edits_overlay_position_check" CHECK (("overlay_position" = ANY (ARRAY['top'::"text", 'center'::"text", 'bottom'::"text"]))),
    CONSTRAINT "clip_edits_overlay_style_check" CHECK (("overlay_style" = ANY (ARRAY['tiktok_viral'::"text", 'question'::"text", 'stat'::"text"]))),
    CONSTRAINT "clip_edits_status_check" CHECK (("status" = ANY (ARRAY['draft'::"text", 'queued'::"text", 'processing'::"text", 'completed'::"text", 'failed'::"text"]))),
    CONSTRAINT "clip_edits_subtitle_style_check" CHECK (("subtitle_style" = ANY (ARRAY['tiktok_viral'::"text", 'clean'::"text", 'podcast'::"text"])))
);


ALTER TABLE "public"."clip_edits" OWNER TO "postgres";


COMMENT ON TABLE "public"."clip_edits" IS 'User-driven post-clip edits. Worker reads queued rows and re-renders.';



COMMENT ON COLUMN "public"."clip_edits"."word_styles" IS 'Per-word ASS style overrides applied during clip re-render.';



CREATE TABLE IF NOT EXISTS "public"."content_results" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "job_id" "uuid",
    "type" "text" NOT NULL,
    "content" "text" NOT NULL,
    "clip_url" "text",
    "start_time" integer,
    "end_time" integer,
    "hook" "text",
    "emotional_trigger" "text",
    "moment_index" integer,
    "created_at" timestamp with time zone DEFAULT "now"(),
    "pillar_type" "text",
    "score_hook" integer,
    "score_retention" integer,
    "score_shareability" integer,
    "time_saved_minutes" integer DEFAULT 0,
    "sentiment_detected" "text",
    "roi_time_saved" integer,
    "score_justifications" "jsonb",
    "viral_overlay" "text",
    "raw_clip_url" "text",
    "whisper_words" "jsonb",
    "score_llm" "jsonb",
    "score_judge" "jsonb",
    "verification_failed" boolean,
    "sub_coverage" real,
    "words_per_sec" real,
    "clip_quality_issues" "jsonb",
    "clip_generation_error" "text",
    CONSTRAINT "content_results_pillar_type_check" CHECK (("pillar_type" = ANY (ARRAY['authority'::"text", 'utility'::"text", 'connection'::"text"]))),
    CONSTRAINT "content_results_score_hook_check" CHECK ((("score_hook" >= 1) AND ("score_hook" <= 10))),
    CONSTRAINT "content_results_score_retention_check" CHECK ((("score_retention" >= 1) AND ("score_retention" <= 10))),
    CONSTRAINT "content_results_score_shareability_check" CHECK ((("score_shareability" >= 1) AND ("score_shareability" <= 10))),
    CONSTRAINT "content_results_type_check" CHECK (("type" = ANY (ARRAY['twitter_thread'::"text", 'linkedin_post'::"text", 'short_video_script'::"text", 'tiktok_caption'::"text"])))
);


ALTER TABLE "public"."content_results" OWNER TO "postgres";


COMMENT ON COLUMN "public"."content_results"."sentiment_detected" IS 'Detected tone from audio: sarcastic, serious, motivational, casual';



COMMENT ON COLUMN "public"."content_results"."roi_time_saved" IS 'Estimated minutes saved vs manual creation';



COMMENT ON COLUMN "public"."content_results"."score_justifications" IS 'Array of score justifications with reasoning and improvement tips';



COMMENT ON COLUMN "public"."content_results"."viral_overlay" IS 'Short UPPERCASE title burned onto the vertical clip (max 4 words, TikTok-style hook overlay).';



COMMENT ON COLUMN "public"."content_results"."score_llm" IS 'Scores autoevaluados por el modelo de análisis (pasada A): {"hook":n,"retention":n,"shareability":n}. Se guardan para calibración juez-vs-análisis.';



COMMENT ON COLUMN "public"."content_results"."score_judge" IS 'Scores del juez independiente (MODEL_JUDGE) contra rúbrica anclada, evaluando el texto Whisper real del clip final: {"hook":n,"retention":n,"shareability":n,"reasoning":"..."}.';



COMMENT ON COLUMN "public"."content_results"."verification_failed" IS 'True cuando first_phrase Y last_phrase del análisis NO matchean el audio real (Whisper) — el corte probablemente no corresponde al momento elegido.';



COMMENT ON COLUMN "public"."content_results"."sub_coverage" IS 'Fracción 0-1 del clip cubierta por subtítulos (métrica de calidad; target >= 0.9).';



COMMENT ON COLUMN "public"."content_results"."words_per_sec" IS 'Densidad de palabras del clip (words Whisper / duración). Muy bajo = clip con silencio/relleno.';



COMMENT ON COLUMN "public"."content_results"."clip_quality_issues" IS 'Array JSON de flags de calidad: incomplete_tail, late_hook, whisper_mismatch_first, clip_not_rendered, clip_generation_failed.';



COMMENT ON COLUMN "public"."content_results"."clip_generation_error" IS 'Error truncado si la generación del clip MP4 falló y se usó fallback YouTube.';



CREATE TABLE IF NOT EXISTS "public"."job_usage_events" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "job_id" "uuid" NOT NULL,
    "user_id" "uuid",
    "event_type" "text" DEFAULT 'llm_chat'::"text" NOT NULL,
    "provider" "text" NOT NULL,
    "task" "text" NOT NULL,
    "model" "text",
    "moment_index" integer,
    "input_tokens" integer DEFAULT 0 NOT NULL,
    "output_tokens" integer DEFAULT 0 NOT NULL,
    "reasoning_tokens" integer DEFAULT 0 NOT NULL,
    "audio_seconds" numeric(10,3),
    "estimated_cost_usd" numeric(12,6) DEFAULT 0 NOT NULL,
    "cache_hit" boolean DEFAULT false NOT NULL,
    "latency_ms" integer,
    "metadata" "jsonb" DEFAULT '{}'::"jsonb" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."job_usage_events" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."jobs" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "uuid",
    "video_url" "text" NOT NULL,
    "video_title" "text",
    "video_duration" integer,
    "status" "text" DEFAULT 'pending'::"text",
    "error_message" "text",
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"(),
    "total_time_saved" integer DEFAULT 0,
    "avg_virality_score" numeric(3,1),
    "total_roi_minutes" integer,
    "current_step" "text",
    "progress_percentage" integer,
    "tone" "text",
    "usage_summary" "jsonb",
    CONSTRAINT "jobs_status_check" CHECK (("status" = ANY (ARRAY['pending'::"text", 'processing'::"text", 'completed'::"text", 'failed'::"text"])))
);


ALTER TABLE "public"."jobs" OWNER TO "postgres";


COMMENT ON COLUMN "public"."jobs"."total_roi_minutes" IS 'Total time saved across all moments in this job';



COMMENT ON COLUMN "public"."jobs"."tone" IS 'Tono elegido por el usuario al crear el job: profesional | sarcastico | motivador | casual. NULL = profesional.';



CREATE TABLE IF NOT EXISTS "public"."transactions" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "uuid",
    "type" "text" NOT NULL,
    "credits" integer NOT NULL,
    "description" "text",
    "stripe_session_id" "text",
    "created_at" timestamp with time zone DEFAULT "now"(),
    CONSTRAINT "transactions_type_check" CHECK (("type" = ANY (ARRAY['purchase'::"text", 'usage'::"text", 'bonus'::"text", 'refund'::"text"])))
);


ALTER TABLE "public"."transactions" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."transcription_cache" (
    "video_id" "text" NOT NULL,
    "transcript" "jsonb" NOT NULL,
    "language" "text",
    "duration_seconds" double precision,
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"()
);


ALTER TABLE "public"."transcription_cache" OWNER TO "postgres";


COMMENT ON TABLE "public"."transcription_cache" IS 'S3: Caches Whisper transcriptions to avoid redundant API calls. Keyed by YouTube video_id.';



CREATE TABLE IF NOT EXISTS "public"."users" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "email" "text" NOT NULL,
    "name" "text",
    "avatar_url" "text",
    "credits" integer DEFAULT 3,
    "stripe_customer_id" "text",
    "subscription_status" "text" DEFAULT 'free'::"text",
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"(),
    "stripe_subscription_id" "text",
    "plan" "text" DEFAULT 'free'::"text" NOT NULL,
    "billing_subscription_id" "text",
    "display_name" "text",
    "professional_title" "text",
    CONSTRAINT "users_subscription_status_check" CHECK (("subscription_status" = ANY (ARRAY['free'::"text", 'basic'::"text", 'pro'::"text", 'active'::"text", 'canceled'::"text", 'cancelled'::"text", 'expired'::"text", 'unpaid'::"text", 'paused'::"text"])))
);


ALTER TABLE "public"."users" OWNER TO "postgres";


COMMENT ON COLUMN "public"."users"."display_name" IS 'Nombre público del creador — se inyecta en los prompts de generación de copy.';



COMMENT ON COLUMN "public"."users"."professional_title" IS 'Título/profesión del creador (ej. "Growth Marketer") — se inyecta en los prompts.';



ALTER TABLE ONLY "public"."analysis_cache"
    ADD CONSTRAINT "analysis_cache_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."analysis_cache"
    ADD CONSTRAINT "analysis_cache_video_id_model_tone_prompt_version_key" UNIQUE ("video_id", "model", "tone", "prompt_version");



ALTER TABLE ONLY "public"."category_cache"
    ADD CONSTRAINT "category_cache_pkey" PRIMARY KEY ("video_id", "model");



ALTER TABLE ONLY "public"."clip_edits"
    ADD CONSTRAINT "clip_edits_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."content_results"
    ADD CONSTRAINT "content_results_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."job_usage_events"
    ADD CONSTRAINT "job_usage_events_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."jobs"
    ADD CONSTRAINT "jobs_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."transactions"
    ADD CONSTRAINT "transactions_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."transcription_cache"
    ADD CONSTRAINT "transcription_cache_pkey" PRIMARY KEY ("video_id");



ALTER TABLE ONLY "public"."users"
    ADD CONSTRAINT "users_email_key" UNIQUE ("email");



ALTER TABLE ONLY "public"."users"
    ADD CONSTRAINT "users_pkey" PRIMARY KEY ("id");



CREATE INDEX "idx_analysis_cache_lookup" ON "public"."analysis_cache" USING "btree" ("video_id", "model", "tone", "prompt_version");



CREATE INDEX "idx_clip_edits_content_result" ON "public"."clip_edits" USING "btree" ("content_result_id");



CREATE INDEX "idx_clip_edits_status" ON "public"."clip_edits" USING "btree" ("status");



CREATE INDEX "idx_clip_edits_user" ON "public"."clip_edits" USING "btree" ("user_id");



CREATE INDEX "idx_content_results_sentiment" ON "public"."content_results" USING "btree" ("sentiment_detected");



CREATE INDEX "idx_job_usage_events_created" ON "public"."job_usage_events" USING "btree" ("created_at" DESC);



CREATE INDEX "idx_job_usage_events_job_id" ON "public"."job_usage_events" USING "btree" ("job_id");



CREATE INDEX "idx_job_usage_events_task" ON "public"."job_usage_events" USING "btree" ("task");



CREATE INDEX "idx_job_usage_events_user_created" ON "public"."job_usage_events" USING "btree" ("user_id", "created_at" DESC);



CREATE INDEX "idx_jobs_created" ON "public"."jobs" USING "btree" ("created_at" DESC);



CREATE INDEX "idx_jobs_current_step" ON "public"."jobs" USING "btree" ("current_step") WHERE ("current_step" IS NOT NULL);



CREATE INDEX "idx_jobs_status" ON "public"."jobs" USING "btree" ("status");



CREATE INDEX "idx_jobs_user" ON "public"."jobs" USING "btree" ("user_id");



CREATE INDEX "idx_results_job" ON "public"."content_results" USING "btree" ("job_id");



CREATE INDEX "idx_transactions_user" ON "public"."transactions" USING "btree" ("user_id");



CREATE INDEX "idx_users_billing_subscription_id" ON "public"."users" USING "btree" ("billing_subscription_id");



CREATE INDEX "idx_users_stripe_customer_id" ON "public"."users" USING "btree" ("stripe_customer_id");



CREATE OR REPLACE TRIGGER "analysis_cache_updated_at" BEFORE UPDATE ON "public"."analysis_cache" FOR EACH ROW EXECUTE FUNCTION "public"."analysis_cache_set_updated_at"();



CREATE OR REPLACE TRIGGER "clip_edits_updated_at" BEFORE UPDATE ON "public"."clip_edits" FOR EACH ROW EXECUTE FUNCTION "public"."clip_edits_set_updated_at"();



CREATE OR REPLACE TRIGGER "trigger_calc_time_saved" BEFORE INSERT ON "public"."content_results" FOR EACH ROW EXECUTE FUNCTION "public"."calculate_time_saved"();



CREATE OR REPLACE TRIGGER "trigger_jobs_updated" BEFORE UPDATE ON "public"."jobs" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at"();



CREATE OR REPLACE TRIGGER "trigger_update_job_metrics" AFTER INSERT ON "public"."content_results" FOR EACH ROW EXECUTE FUNCTION "public"."update_job_metrics"();



CREATE OR REPLACE TRIGGER "trigger_update_transcription_cache_timestamp" BEFORE UPDATE ON "public"."transcription_cache" FOR EACH ROW EXECUTE FUNCTION "public"."update_transcription_cache_timestamp"();



CREATE OR REPLACE TRIGGER "trigger_users_updated" BEFORE UPDATE ON "public"."users" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at"();



ALTER TABLE ONLY "public"."clip_edits"
    ADD CONSTRAINT "clip_edits_content_result_id_fkey" FOREIGN KEY ("content_result_id") REFERENCES "public"."content_results"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."content_results"
    ADD CONSTRAINT "content_results_job_id_fkey" FOREIGN KEY ("job_id") REFERENCES "public"."jobs"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."job_usage_events"
    ADD CONSTRAINT "job_usage_events_job_id_fkey" FOREIGN KEY ("job_id") REFERENCES "public"."jobs"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."jobs"
    ADD CONSTRAINT "jobs_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."transactions"
    ADD CONSTRAINT "transactions_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE CASCADE;



CREATE POLICY "Users can create jobs" ON "public"."jobs" FOR INSERT WITH CHECK (("auth"."uid"() = "user_id"));



CREATE POLICY "Users can insert own jobs" ON "public"."jobs" FOR INSERT WITH CHECK (("auth"."uid"() = "user_id"));



CREATE POLICY "Users can update own jobs" ON "public"."jobs" FOR UPDATE USING (("auth"."uid"() = "user_id"));



CREATE POLICY "Users can update own profile" ON "public"."users" FOR UPDATE USING (("auth"."uid"() = "id"));



CREATE POLICY "Users can view own jobs" ON "public"."jobs" FOR SELECT USING (("auth"."uid"() = "user_id"));



CREATE POLICY "Users can view own profile" ON "public"."users" FOR SELECT USING (("auth"."uid"() = "id"));



CREATE POLICY "Users can view own results" ON "public"."content_results" FOR SELECT USING ((EXISTS ( SELECT 1
   FROM "public"."jobs"
  WHERE (("jobs"."id" = "content_results"."job_id") AND ("jobs"."user_id" = "auth"."uid"())))));



CREATE POLICY "Users can view own transactions" ON "public"."transactions" FOR SELECT USING (("auth"."uid"() = "user_id"));



CREATE POLICY "Users can view results of own jobs" ON "public"."content_results" FOR SELECT USING (("job_id" IN ( SELECT "jobs"."id"
   FROM "public"."jobs"
  WHERE ("jobs"."user_id" = "auth"."uid"()))));



ALTER TABLE "public"."analysis_cache" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."category_cache" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."clip_edits" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."content_results" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."job_usage_events" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."jobs" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."transactions" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."users" ENABLE ROW LEVEL SECURITY;




ALTER PUBLICATION "supabase_realtime" OWNER TO "postgres";






GRANT USAGE ON SCHEMA "public" TO "postgres";
GRANT USAGE ON SCHEMA "public" TO "anon";
GRANT USAGE ON SCHEMA "public" TO "authenticated";
GRANT USAGE ON SCHEMA "public" TO "service_role";






















































































































































GRANT ALL ON FUNCTION "public"."analysis_cache_set_updated_at"() TO "anon";
GRANT ALL ON FUNCTION "public"."analysis_cache_set_updated_at"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."analysis_cache_set_updated_at"() TO "service_role";



GRANT ALL ON FUNCTION "public"."calculate_time_saved"() TO "anon";
GRANT ALL ON FUNCTION "public"."calculate_time_saved"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."calculate_time_saved"() TO "service_role";



GRANT ALL ON FUNCTION "public"."check_duplicate_job"("p_user_id" "uuid", "p_video_url" "text", "p_days_back" integer) TO "anon";
GRANT ALL ON FUNCTION "public"."check_duplicate_job"("p_user_id" "uuid", "p_video_url" "text", "p_days_back" integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."check_duplicate_job"("p_user_id" "uuid", "p_video_url" "text", "p_days_back" integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."clip_edits_set_updated_at"() TO "anon";
GRANT ALL ON FUNCTION "public"."clip_edits_set_updated_at"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."clip_edits_set_updated_at"() TO "service_role";



GRANT ALL ON FUNCTION "public"."deduct_user_credit"("p_user_id" "uuid", "p_job_id" "uuid", "p_description" "text") TO "anon";
GRANT ALL ON FUNCTION "public"."deduct_user_credit"("p_user_id" "uuid", "p_job_id" "uuid", "p_description" "text") TO "authenticated";
GRANT ALL ON FUNCTION "public"."deduct_user_credit"("p_user_id" "uuid", "p_job_id" "uuid", "p_description" "text") TO "service_role";



GRANT ALL ON FUNCTION "public"."get_job_usage_summary"("p_job_id" "uuid") TO "anon";
GRANT ALL ON FUNCTION "public"."get_job_usage_summary"("p_job_id" "uuid") TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_job_usage_summary"("p_job_id" "uuid") TO "service_role";



GRANT ALL ON FUNCTION "public"."handle_new_user"() TO "anon";
GRANT ALL ON FUNCTION "public"."handle_new_user"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."handle_new_user"() TO "service_role";



GRANT ALL ON FUNCTION "public"."update_job_metrics"() TO "anon";
GRANT ALL ON FUNCTION "public"."update_job_metrics"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."update_job_metrics"() TO "service_role";



GRANT ALL ON FUNCTION "public"."update_transcription_cache_timestamp"() TO "anon";
GRANT ALL ON FUNCTION "public"."update_transcription_cache_timestamp"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."update_transcription_cache_timestamp"() TO "service_role";



GRANT ALL ON FUNCTION "public"."update_updated_at"() TO "anon";
GRANT ALL ON FUNCTION "public"."update_updated_at"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."update_updated_at"() TO "service_role";


















GRANT ALL ON TABLE "public"."analysis_cache" TO "anon";
GRANT ALL ON TABLE "public"."analysis_cache" TO "authenticated";
GRANT ALL ON TABLE "public"."analysis_cache" TO "service_role";



GRANT ALL ON TABLE "public"."category_cache" TO "anon";
GRANT ALL ON TABLE "public"."category_cache" TO "authenticated";
GRANT ALL ON TABLE "public"."category_cache" TO "service_role";



GRANT ALL ON TABLE "public"."clip_edits" TO "anon";
GRANT ALL ON TABLE "public"."clip_edits" TO "authenticated";
GRANT ALL ON TABLE "public"."clip_edits" TO "service_role";



GRANT ALL ON TABLE "public"."content_results" TO "anon";
GRANT ALL ON TABLE "public"."content_results" TO "authenticated";
GRANT ALL ON TABLE "public"."content_results" TO "service_role";



GRANT ALL ON TABLE "public"."job_usage_events" TO "anon";
GRANT ALL ON TABLE "public"."job_usage_events" TO "authenticated";
GRANT ALL ON TABLE "public"."job_usage_events" TO "service_role";



GRANT ALL ON TABLE "public"."jobs" TO "anon";
GRANT ALL ON TABLE "public"."jobs" TO "authenticated";
GRANT ALL ON TABLE "public"."jobs" TO "service_role";



GRANT ALL ON TABLE "public"."transactions" TO "anon";
GRANT ALL ON TABLE "public"."transactions" TO "authenticated";
GRANT ALL ON TABLE "public"."transactions" TO "service_role";



GRANT ALL ON TABLE "public"."transcription_cache" TO "anon";
GRANT ALL ON TABLE "public"."transcription_cache" TO "authenticated";
GRANT ALL ON TABLE "public"."transcription_cache" TO "service_role";



GRANT ALL ON TABLE "public"."users" TO "anon";
GRANT ALL ON TABLE "public"."users" TO "authenticated";
GRANT ALL ON TABLE "public"."users" TO "service_role";









ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "service_role";































