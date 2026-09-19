-- ═══════════════════════════════════════════════════════════════════════════
-- Créditos reservados al crear el job (F1, ADR 0005 — implementado)
-- ═══════════════════════════════════════════════════════════════════════════
-- Hasta ahora POST /process solo validaba credits > 0 y el worker descontaba
-- al completar (deduct_user_credit): con 1 crédito se podían encolar N jobs
-- antes de que el primero terminara. Se reserva 1 crédito de forma atómica
-- al crear el job (reserve_credit) y se libera si el job termina failed
-- (release_credit, disparado por trigger).

ALTER TABLE "public"."jobs"
    ADD COLUMN IF NOT EXISTS "credit_reserved" boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN "public"."jobs"."credit_reserved" IS
    'true si POST /process (o /jobs/:id/retry) ya reservó 1 crédito para este '
    'job (ADR 0005). El trigger release_credit_on_job_failed lo vuelve a '
    'false cuando el job pasa a failed (y libera el crédito). '
    'deduct_user_credit lo usa para no descontar dos veces al completar.';

ALTER TABLE "public"."jobs"
    ADD COLUMN IF NOT EXISTS "failure_alert_sent" boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN "public"."jobs"."failure_alert_sent" IS
    'F1: si ya se mandó la alerta de Telegram de este job failed '
    '(backend/src/lib/failure-watcher.js, poll cada 60s) — evita reavisar '
    'el mismo job en cada ciclo.';

-- ── reserve_credit / release_credit ─────────────────────────────────────────

CREATE OR REPLACE FUNCTION "public"."reserve_credit"("p_user_id" "uuid")
    RETURNS boolean
    LANGUAGE "plpgsql"
    AS $$
DECLARE
    v_ok boolean;
BEGIN
    UPDATE "public"."users"
    SET credits = credits - 1
    WHERE id = p_user_id AND credits > 0
    RETURNING true INTO v_ok;

    RETURN COALESCE(v_ok, false);
END;
$$;

ALTER FUNCTION "public"."reserve_credit"("p_user_id" "uuid") OWNER TO "postgres";
GRANT ALL ON FUNCTION "public"."reserve_credit"("p_user_id" "uuid") TO "anon";
GRANT ALL ON FUNCTION "public"."reserve_credit"("p_user_id" "uuid") TO "authenticated";
GRANT ALL ON FUNCTION "public"."reserve_credit"("p_user_id" "uuid") TO "service_role";

CREATE OR REPLACE FUNCTION "public"."release_credit"("p_user_id" "uuid")
    RETURNS void
    LANGUAGE "plpgsql"
    AS $$
BEGIN
    UPDATE "public"."users"
    SET credits = credits + 1
    WHERE id = p_user_id;
END;
$$;

ALTER FUNCTION "public"."release_credit"("p_user_id" "uuid") OWNER TO "postgres";
GRANT ALL ON FUNCTION "public"."release_credit"("p_user_id" "uuid") TO "anon";
GRANT ALL ON FUNCTION "public"."release_credit"("p_user_id" "uuid") TO "authenticated";
GRANT ALL ON FUNCTION "public"."release_credit"("p_user_id" "uuid") TO "service_role";

-- ── deduct_user_credit: no-op si el job ya reservó (evita doble descuento) ──
-- El worker (worker/services/supabase_client.py::deduct_credit) sigue
-- llamando a esta RPC sin ningún cambio de código al completar un job — la
-- decisión de descontar o no vive acá, en SQL, justamente para no tener que
-- tocar el worker (fuera del alcance de esta tarea).

CREATE OR REPLACE FUNCTION "public"."deduct_user_credit"("p_user_id" "uuid", "p_job_id" "uuid", "p_description" "text")
    RETURNS TABLE("success" boolean, "new_credits" integer, "message" "text")
    LANGUAGE "plpgsql"
    AS $$
DECLARE
    updated_credits INT;
    already_reserved boolean;
BEGIN
    SELECT credit_reserved INTO already_reserved FROM "public"."jobs" WHERE id = p_job_id;

    IF already_reserved IS TRUE THEN
        SELECT credits INTO updated_credits FROM "public"."users" WHERE id = p_user_id;
        RETURN QUERY SELECT TRUE, COALESCE(updated_credits, 0),
            'Credit already reserved at job creation (ADR 0005) — no-op'::TEXT;
        RETURN;
    END IF;

    -- Compatibilidad: jobs creados antes de esta migración, o por caminos
    -- que no pasan por POST /process (ej. worker/eval), sin credit_reserved
    -- -- comportamiento de siempre (descuenta acá).
    UPDATE "public"."users"
    SET credits = credits - 1
    WHERE id = p_user_id AND credits > 0
    RETURNING credits INTO updated_credits;

    IF updated_credits IS NULL THEN
        RETURN QUERY SELECT FALSE, 0, 'Insufficient credits or user not found'::TEXT;
    ELSE
        INSERT INTO "public"."transactions" (user_id, type, credits, description)
        VALUES (p_user_id, 'usage', -1, p_description || ' [Job: ' || p_job_id::text || ']');
        RETURN QUERY SELECT TRUE, updated_credits, 'Credit deducted successfully'::TEXT;
    END IF;
END;
$$;

-- ── Trigger: liberar el crédito cuando un job pasa a 'failed' ───────────────
-- BEFORE UPDATE (no AFTER) para poder poner credit_reserved=false en la
-- MISMA fila sin un UPDATE adicional (que re-dispararía el trigger). Cubre
-- tanto los fallos que marca el worker (update_job_error, la mayoría) como
-- los que pudiera marcar el backend en el futuro — nadie tiene que llamar
-- a release_credit a mano.

CREATE OR REPLACE FUNCTION "public"."release_credit_on_job_failed"()
    RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
    IF NEW.status = 'failed' AND OLD.status IS DISTINCT FROM 'failed'
       AND NEW.credit_reserved IS TRUE AND NEW.user_id IS NOT NULL THEN
        PERFORM "public"."release_credit"(NEW.user_id);
        NEW.credit_reserved := false;
    END IF;
    RETURN NEW;
END;
$$;

ALTER FUNCTION "public"."release_credit_on_job_failed"() OWNER TO "postgres";

DROP TRIGGER IF EXISTS "trg_release_credit_on_job_failed" ON "public"."jobs";
CREATE TRIGGER "trg_release_credit_on_job_failed"
    BEFORE UPDATE ON "public"."jobs"
    FOR EACH ROW
    EXECUTE FUNCTION "public"."release_credit_on_job_failed"();

-- Nota (pendiente, fuera de esta tarea): esto libera el crédito cada vez que
-- jobs.status pasa a 'failed'. La definición de "Job fallido" en CONTEXT.md
-- ("si ningún momento tiene clip, falla") es más estricta que "el proceso
-- tiró una excepción" — hoy el worker solo pone status='failed' en el caso
-- de excepción/timeout; un job que termina con 0 clips reales pero SIN
-- excepción sigue quedando 'completed' y cobra igual. Corregir eso requiere
-- tocar worker/main.py (fuera del alcance — no se tocó el worker en F1).
