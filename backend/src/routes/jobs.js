const express = require('express');
const { v4: uuidv4 } = require('uuid');
const path = require('path');
const { supabase } = require('../lib/supabase');
const rateLimit = require('express-rate-limit');
const { requireAuth, optionalAuth } = require('../middleware/auth');

const router = express.Router();

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
        const { videoUrl, userId } = req.body;

        // Validate YouTube URL
        if (!videoUrl) {
            return res.status(400).json({ error: 'videoUrl is required' });
        }

        const youtubeRegex = /^(https?:\/\/)?(www\.)?(youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/shorts\/)[\w-]+/;
        if (!youtubeRegex.test(videoUrl)) {
            return res.status(400).json({ error: 'Invalid YouTube URL' });
        }

        const jobId = uuidv4();

        // Check if Supabase is configured
        if (process.env.SUPABASE_URL) {
            // Check user credits if userId provided
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

                const { data: user, error: userError } = await supabase
                    .from('users')
                    .select('credits')
                    .eq('id', userId)
                    .single();

                if (userError) {
                    console.error('Error fetching user:', userError);
                    // Decide if we block or allow on error. Blocking is safer for SaaS.
                    return res.status(500).json({
                        error: 'Server error',
                        message: 'Error al verificar tus créditos. Por favor intenta de nuevo.'
                    });
                } else if (!user || user.credits <= 0) {
                    return res.status(402).json({
                        error: 'Insufficient credits',
                        message: 'No tienes créditos disponibles. Por favor recarga para continuar.'
                    });
                }

                // IMPORTANT: We do NOT deduct credits here. 
                // Credits should be deducted by the worker ONLY upon SUCCESSFUL completion.
                // Here we just validate availability.
            }

            // Create job in Supabase
            const { error: jobError } = await supabase.from('jobs').insert({
                id: jobId,
                user_id: userId || null,
                video_url: videoUrl,
                status: 'pending'
            });

            if (jobError) {
                console.error('Error creating job:', jobError);
                return res.status(500).json({ error: 'Failed to create job' });
            }
        } else {
            // Fallback to SQLite (legacy)
            const { statements } = require('../db/database');
            statements.createJob.run(jobId, userId || null, videoUrl);
        }

        // S1: Queue is now Supabase — no filesystem writes needed
        // The job insert above (line ~97) IS the queue entry

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

        if (process.env.SUPABASE_URL) {
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
                results: results || []
            });
        } else {
            const { statements } = require('../db/database');
            const row = statements.getJobWithResults.get(jobId);

            if (!row) {
                return res.status(404).json({ error: 'Job not found' });
            }

            let results = [];
            try {
                const parsed = JSON.parse(row.results);
                results = parsed.filter(r => r.id !== null);
            } catch (e) {
                results = [];
            }

            res.json({
                id: row.id,
                videoUrl: row.video_url,
                status: row.status,
                errorMessage: row.error_message,
                createdAt: row.created_at,
                updatedAt: row.updated_at,
                results
            });
        }

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

        if (process.env.SUPABASE_URL) {
            const { data: jobs, error } = await supabase
                .from('jobs')
                .select('*')
                .eq('user_id', userId)
                .order('created_at', { ascending: false })
                .limit(50);

            if (error) throw error;
            res.json(jobs);
        } else {
            const { db } = require('../db/database');
            const jobs = db.prepare(
                'SELECT * FROM jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT 50'
            ).all(userId);
            res.json(jobs);
        }
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

        if (!process.env.SUPABASE_URL) {
            return res.status(501).json({ error: 'Supabase not configured' });
        }

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
 * El worker lo va a tomar en el siguiente poll. NO duplica el row, NO
 * descuenta credito (ya se cobro o el job fallo antes de cobrar).
 */
router.post('/jobs/:jobId/retry', requireAuth, async (req, res) => {
    try {
        if (!process.env.SUPABASE_URL) {
            return res.status(501).json({ error: 'Supabase not configured' });
        }

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

        const { data: updated, error: updateErr } = await supabase
            .from('jobs')
            .update({
                status: 'pending',
                error_message: null,
                progress_percentage: 0,
                current_step: null,
            })
            .eq('id', jobId)
            .select()
            .single();

        if (updateErr) throw updateErr;
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
        if (!process.env.SUPABASE_URL) {
            return res.status(501).json({ error: 'Supabase not configured' });
        }

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
