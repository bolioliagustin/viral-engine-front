const express = require('express');
const { v4: uuidv4 } = require('uuid');
const path = require('path');
const { supabase } = require('../lib/supabase');
const rateLimit = require('express-rate-limit');
const { requireAuth, optionalAuth } = require('../middleware/auth');
const { curveMomentScores } = require('../lib/score-curve');
const { getVideoDurationMinutes } = require('../lib/youtube-duration');
const { notify } = require('../lib/telegram');
const logger = require('../lib/logger');

const router = express.Router();

// F1 (docs/PROYECTO.md §14/§15): tope de duración en la beta.
const MAX_VIDEO_MINUTES = Number(process.env.MAX_VIDEO_MINUTES) || 90;

// Rate limiter for /process endpoint
// Uses IP by default (with proper IPv6 support)
// For authenticated users, we could add userId to headers for better tracking
const processLimiter = rateLimit({
    windowMs: 15 * 60 * 1000, // 15 minutes
    max: 5, // 5 requests per window
    keyGenerator: (req) => req.user?.id || 'anonymous', // Use verified user ID (set by requireAuth which runs before this)
    message: {
        error: 'Too many requests',
        message: 'Has excedido el límite de solicitudes. Por favor espera 15 minutos.'
    },
    standardHeaders: true,
    legacyHeaders: false,
    validate: false, // Disable built-in validation (we manage keys via requireAuth)
});

/**
 * POST /process
 * Receives a YouTube URL, creates a Job, and queues it for processing
 */
router.post('/process', requireAuth, processLimiter, async (req, res) => {
    try {
        // userId is now guaranteed to be the verified user's ID (set by requireAuth middleware)
        const { videoUrl, userId, tone } = req.body;

        // Validate YouTube URL
        if (!videoUrl) {
            return res.status(400).json({ error: 'videoUrl is required' });
        }

        // Fase 5: tono opcional del contenido generado
        const VALID_TONES = ['profesional', 'sarcastico', 'motivador', 'casual'];
        const jobTone = tone && VALID_TONES.includes(String(tone).toLowerCase())
            ? String(tone).toLowerCase()
            : null;

        const youtubeRegex = /^(https?:\/\/)?(www\.)?(youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/shorts\/)[\w-]+/;
        if (!youtubeRegex.test(videoUrl)) {
            return res.status(400).json({ error: 'Invalid YouTube URL' });
        }

        const jobId = uuidv4();
        let creditReserved = false;

        if (userId) {
            // Check for duplicate job in last 7 days
            const { data: duplicateCheck } = await supabase
                .rpc('check_duplicate_job', {
                    p_user_id: userId,
                    p_video_url: videoUrl,
                    p_days_back: 7
                });

            if (duplicateCheck && duplicateCheck[0]?.has_duplicate) {
                return res.status(409).json({
                    error: 'Duplicate job',
                    message: 'Este video ya fue procesado recientemente',
                    existingJobId: duplicateCheck[0].existing_job_id
                });
            }

            // F1: tope de duración de la beta. "Fail open" — si no se pudo
            // determinar la duración (red, HTML cambiado), no bloqueamos.
            const durationMinutes = await getVideoDurationMinutes(videoUrl);
            if (durationMinutes !== null && durationMinutes > MAX_VIDEO_MINUTES) {
                return res.status(400).json({
                    error: 'Video too long',
                    message: `El video dura ${durationMinutes} min; el máximo en la beta es ${MAX_VIDEO_MINUTES} min.`,
                });
            }

            // F1 (ADR 0005): reserva atómica del crédito, ANTES de crear el
            // job — reemplaza el viejo "solo valido credits > 0" que dejaba
            // encolar N jobs con 1 crédito.
            const { data: reserved, error: reserveErr } = await supabase
                .rpc('reserve_credit', { p_user_id: userId });

            if (reserveErr) {
                console.error('Error reserving credit:', reserveErr);
                return res.status(500).json({
                    error: 'Server error',
                    message: 'Error al verificar tus créditos. Por favor intenta de nuevo.'
                });
            }
            if (!reserved) {
                // F1: si el usuario ya corrió jobs antes, avisar por Telegram
                // (señal de que hay que cargarle créditos a mano en la beta).
                const { count } = await supabase
                    .from('jobs')
                    .select('id', { count: 'exact', head: true })
                    .eq('user_id', userId);
                if (count && count > 0) {
                    notify(
                        `💳 <b>Sin créditos</b>\nUsuario: <code>${userId}</code>\nYa corrió ${count} job(s) — puede necesitar recarga.`
                    ).catch((e) => logger.error('notify sin créditos falló', { error: e.message }));
                }
                return res.status(402).json({
                    error: 'Insufficient credits',
                    message: 'No tienes créditos disponibles. Por favor recarga para continuar.'
                });
            }
            creditReserved = true;
        }

        // Create job in Supabase
        const jobRow = {
            id: jobId,
            user_id: userId || null,
            video_url: videoUrl,
            status: 'pending',
            credit_reserved: creditReserved,
        };
        if (jobTone) jobRow.tone = jobTone;
        let { error: jobError } = await supabase.from('jobs').insert(jobRow);

        // Compat: si la columna tone no existe todavía (migración
        // ai_quality sin correr), reintentar sin tone.
        if (jobError && jobTone && /tone/i.test(jobError.message || '')) {
            delete jobRow.tone;
            ({ error: jobError } = await supabase.from('jobs').insert(jobRow));
        }

        if (jobError) {
            console.error('Error creating job:', jobError);
            // F1: el insert falló DESPUÉS de reservar el crédito — liberarlo,
            // si no el usuario pierde un crédito por un job que no existe.
            if (creditReserved) {
                try {
                    await supabase.rpc('release_credit', { p_user_id: userId });
                } catch (releaseErr) {
                    logger.error('release_credit falló tras insert error', {
                        error: releaseErr.message, userId, jobId,
                    });
                }
            }
            return res.status(500).json({ error: 'Failed to create job' });
        }

        // La fila en `jobs` con status=pending ES la entrada en la cola (ADR 0001)

        res.status(201).json({
            success: true,
            jobId,
            status: 'pending',
            message: 'Job queued for processing'
        });

    } catch (error) {
        console.error('Error creating job:', error);
        res.status(500).json({ error: 'Failed to create job' });
    }
});

/**
 * GET /status/:jobId
 * Returns the status and results of a job.
 * If the caller is authenticated, enforces ownership (can only see own jobs).
 * Anonymous callers can still poll (useful for public share links) but only
 * if the job has no owner (user_id IS NULL).
 */
router.get('/status/:jobId', optionalAuth, async (req, res) => {
    try {
        const { jobId } = req.params;

        const { data: job, error: jobError } = await supabase
            .from('jobs')
            .select('*')
            .eq('id', jobId)
            .single();

        if (jobError || !job) {
            return res.status(404).json({ error: 'Job not found' });
        }

        // Ownership check: authenticated users can only see their own jobs.
        // Jobs without an owner (anonymous submissions) are accessible to anyone.
        if (req.user && job.user_id && job.user_id !== req.user.id) {
            return res.status(403).json({ error: 'Forbidden' });
        }
        // Unauthenticated callers cannot see jobs that belong to a user.
        if (!req.user && job.user_id) {
            return res.status(401).json({ error: 'Authentication required' });
        }

        const { data: results } = await supabase
            .from('content_results')
            .select('*')
            .eq('job_id', jobId)
            .order('moment_index', { ascending: true });

        // W10: "Score visible" — curva 60-99 + letras A-D, calculada por
        // momento DENTRO de este job. Es solo presentación (score-curve.js);
        // no toca score_judge ni el ranking que usa el pipeline. Una fila
        // por content_result (3 por momento) trae el mismo score_judge
        // repetido, así que alcanza con una fila por moment_index.
        const rows = results || [];
        const byMoment = new Map();
        for (const r of rows) {
            if (!byMoment.has(r.moment_index)) {
                byMoment.set(r.moment_index, { moment_index: r.moment_index, score_judge: r.score_judge });
            }
        }
        const curved = curveMomentScores(Array.from(byMoment.values()));

        // W9-A (docs/adr/0008): hd_status/hd_url no son columnas — se derivan
        // en caliente del último clip_edits (edit_type='hd_upgrade') de cada
        // content_result_id, el mismo mecanismo de W7. preview_url sí es una
        // columna real (migración galeria_hd), la trae el select('*') de
        // arriba; NULL hasta que el worker la llene (pendiente, mitad worker
        // de W9) — la UI cae a clip_url cuando falta.
        const resultIds = rows.map((r) => r.id);
        const hdByResultId = new Map();
        if (resultIds.length > 0) {
            const { data: hdEdits } = await supabase
                .from('clip_edits')
                .select('content_result_id, status, rendered_clip_url, created_at')
                .in('content_result_id', resultIds)
                .eq('edit_type', 'hd_upgrade')
                .order('created_at', { ascending: false });
            for (const edit of hdEdits || []) {
                if (!hdByResultId.has(edit.content_result_id)) {
                    hdByResultId.set(edit.content_result_id, edit);
                }
            }
        }

        const resultsWithDisplay = rows.map((r) => {
            const hdEdit = hdByResultId.get(r.id);
            let hd_status = 'none';
            let hd_url = null;
            if (hdEdit) {
                if (hdEdit.status === 'completed' && hdEdit.rendered_clip_url) {
                    hd_status = 'ready';
                    hd_url = hdEdit.rendered_clip_url;
                } else if (hdEdit.status === 'queued' || hdEdit.status === 'processing') {
                    hd_status = hdEdit.status;
                } else if (hdEdit.status === 'failed') {
                    hd_status = 'error';
                }
            }
            return {
                ...r,
                ...(curved.get(r.moment_index) || { score_display: null, grades: null }),
                preview_url: r.preview_url ?? null,
                hd_url,
                hd_status,
            };
        });

        res.json({
            id: job.id,
            videoUrl: job.video_url,
            videoTitle: job.video_title,
            status: job.status,
            current_step: job.current_step,
            progress_percentage: job.progress_percentage,
            errorMessage: job.error_message,
            createdAt: job.created_at,
            updatedAt: job.updated_at,
            results: resultsWithDisplay
        });

    } catch (error) {
        console.error('Error getting job status:', error);
        res.status(500).json({ error: 'Failed to get job status' });
    }
});

/**
 * GET /jobs
 * Returns jobs for the authenticated user only.
 */
router.get('/jobs', requireAuth, async (req, res) => {
    try {
        const userId = req.user.id;

        const { data: jobs, error } = await supabase
            .from('jobs')
            .select('*')
            .eq('user_id', userId)
            .order('created_at', { ascending: false })
            .limit(50);

        if (error) throw error;
        res.json(jobs);
    } catch (error) {
        console.error('Error listing jobs:', error);
        res.status(500).json({ error: 'Failed to list jobs' });
    }
});

/**
 * GET /user/:userId/credits
 * GET /user/me/credits  (alias — frontend uses this path)
 * Returns user credits
 */
router.get('/user/me/credits', requireAuth, getUserCredits);
router.get('/user/:userId/credits', requireAuth, getUserCredits);

async function getUserCredits(req, res) {
    try {
        // Use verified user ID from auth middleware, not URL param (prevents enumeration)
        const userId = req.user.id;

        const { data: user, error } = await supabase
            .from('users')
            .select('credits, subscription_status')
            .eq('id', userId)
            .single();

        if (error || !user) {
            return res.status(404).json({ error: 'User not found' });
        }

        res.json({
            credits: user.credits,
            subscription: user.subscription_status
        });

    } catch (error) {
        console.error('Error getting credits:', error);
        res.status(500).json({ error: 'Failed to get credits' });
    }
}

/**
 * POST /jobs/:jobId/retry
 * Reintenta un job 'failed' o 'completed' reseteandolo a 'pending'.
 * El worker lo va a tomar en el siguiente poll. NO duplica el row.
 * F1 (ADR 0005): vuelve a reservar 1 crédito — un reintento es un nuevo
 * procesamiento y cuesta como tal, ya sea que el original haya fallado
 * (se le devolvió el crédito por el trigger) o haya completado (ese
 * crédito ya se gastó en ESE resultado).
 */
router.post('/jobs/:jobId/retry', requireAuth, async (req, res) => {
    try {
        const { jobId } = req.params;
        const userId = req.user.id;

        // Fetch + ownership check
        const { data: job, error: fetchErr } = await supabase
            .from('jobs')
            .select('id, user_id, status')
            .eq('id', jobId)
            .single();

        if (fetchErr || !job) {
            return res.status(404).json({ error: 'Job not found' });
        }
        if (job.user_id !== userId) {
            return res.status(403).json({ error: 'Forbidden' });
        }
        // Solo permitimos reintentar jobs en estado terminal
        if (!['failed', 'completed'].includes(job.status)) {
            return res.status(409).json({
                error: `Cannot retry: job is ${job.status}`,
            });
        }

        const { data: reserved, error: reserveErr } = await supabase
            .rpc('reserve_credit', { p_user_id: userId });
        if (reserveErr) {
            console.error('Error reserving credit for retry:', reserveErr);
            return res.status(500).json({
                error: 'Server error',
                message: 'Error al verificar tus créditos. Por favor intenta de nuevo.'
            });
        }
        if (!reserved) {
            return res.status(402).json({
                error: 'Insufficient credits',
                message: 'No tienes créditos disponibles. Por favor recarga para continuar.'
            });
        }

        const { data: updated, error: updateErr } = await supabase
            .from('jobs')
            .update({
                status: 'pending',
                error_message: null,
                progress_percentage: 0,
                current_step: null,
                credit_reserved: true,
                failure_alert_sent: false,
            })
            .eq('id', jobId)
            .select()
            .single();

        if (updateErr) {
            // El update falló después de reservar -- liberar para no cobrar
            // un crédito por un retry que no se pudo encolar.
            try {
                await supabase.rpc('release_credit', { p_user_id: userId });
            } catch { /* best-effort */ }
            throw updateErr;
        }
        res.json({ job: updated, message: 'Job re-encolado' });
    } catch (error) {
        console.error('Error retrying job:', error);
        res.status(500).json({ error: 'Failed to retry job' });
    }
});

/**
 * DELETE /jobs/:jobId
 * Elimina un job + sus content_results + intenta limpiar los clips de R2.
 */
router.delete('/jobs/:jobId', requireAuth, async (req, res) => {
    try {
        const { jobId } = req.params;
        const userId = req.user.id;

        // Fetch + ownership
        const { data: job, error: fetchErr } = await supabase
            .from('jobs')
            .select('id, user_id, status')
            .eq('id', jobId)
            .single();

        if (fetchErr || !job) {
            return res.status(404).json({ error: 'Job not found' });
        }
        if (job.user_id !== userId) {
            return res.status(403).json({ error: 'Forbidden' });
        }

        // Bloquear delete de jobs en proceso (evita corrupcion del worker)
        if (job.status === 'processing' || job.status === 'pending') {
            return res.status(409).json({
                error: 'No se puede eliminar un job en proceso. Esperá a que termine o falle.',
            });
        }

        // 1. Borrar content_results (cascade no esta configurado; mejor explicito)
        await supabase.from('content_results').delete().eq('job_id', jobId);

        // 2. Borrar clip_edits asociados (CASCADE FK en content_result_id ya
        //    deberia limpiar pero lo hacemos explicito por si)
        // (clip_edits.content_result_id tiene ON DELETE CASCADE)

        // 3. Borrar el job
        const { error: deleteErr } = await supabase
            .from('jobs')
            .delete()
            .eq('id', jobId);

        if (deleteErr) throw deleteErr;

        // Nota: clips MP4 en R2 quedan huérfanos. Cleanup tipo TTL del bucket
        // se puede hacer aparte (lifecycle rules). Borrarlos sincrónicamente
        // aca alargaria mucho el endpoint y no es critico.
        res.json({ ok: true, message: 'Job eliminado' });
    } catch (error) {
        console.error('Error deleting job:', error);
        res.status(500).json({ error: 'Failed to delete job' });
    }
});

module.exports = router;
