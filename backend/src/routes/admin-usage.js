/**
 * Admin usage metrics API — costos reales por job (LLM + Whisper).
 * Requiere JWT + allowlist ADMIN_USER_IDS / ADMIN_EMAILS.
 */
const express = require('express');
const { supabase } = require('../lib/supabase');
const { requireAuth } = require('../middleware/auth');
const { requireAdmin, isAdminUser } = require('../middleware/admin');

const router = express.Router();

const CREDIT_PRICE_USD = 9 / 40; // plan Starter: $9 / 40 créditos
const CANVAS_ESTIMATES = {
    happy_path: 0.13,
    current_config: 0.20,
};

function parseDateRange(req) {
    const now = new Date();
    const to = req.query.to ? new Date(req.query.to) : now;
    const from = req.query.from
        ? new Date(req.query.from)
        : new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
    return {
        from: from.toISOString(),
        to: to.toISOString(),
    };
}

function num(v) {
    const n = parseFloat(v);
    return Number.isFinite(n) ? n : 0;
}

/** GET /admin/usage/me — gate del frontend */
router.get('/usage/me', requireAuth, (req, res) => {
    res.json({ isAdmin: isAdminUser(req.user) });
});

/** GET /admin/usage/summary */
router.get('/usage/summary', requireAuth, requireAdmin, async (req, res) => {
    try {
        const { from, to } = parseDateRange(req);

        const { data: jobs, error: jobsErr } = await supabase
            .from('jobs')
            .select('id, status, usage_summary, created_at')
            .eq('status', 'completed')
            .gte('created_at', from)
            .lte('created_at', to);

        if (jobsErr) throw jobsErr;

        const completed = jobs || [];
        let totalCost = 0;
        let totalTokensIn = 0;
        let totalTokensOut = 0;
        let whisperSeconds = 0;

        for (const j of completed) {
            const s = j.usage_summary || {};
            totalCost += num(s.total_cost_usd);
            totalTokensIn += parseInt(s.total_input_tokens, 10) || 0;
            totalTokensOut += parseInt(s.total_output_tokens, 10) || 0;
            whisperSeconds += num(s.whisper_seconds);
        }

        const jobCount = completed.length;
        const revenueUsd = jobCount * CREDIT_PRICE_USD;

        res.json({
            period: { from, to },
            completed_jobs: jobCount,
            total_cost_usd: round(totalCost),
            avg_cost_per_job: jobCount ? round(totalCost / jobCount) : 0,
            total_input_tokens: totalTokensIn,
            total_output_tokens: totalTokensOut,
            whisper_minutes: round(whisperSeconds / 60, 2),
            revenue_usd: round(revenueUsd),
            margin_usd: round(revenueUsd - totalCost),
            credit_price_usd: CREDIT_PRICE_USD,
        });
    } catch (err) {
        console.error('admin/usage/summary error:', err.message);
        res.status(500).json({ error: 'Failed to load usage summary' });
    }
});

/** GET /admin/usage/jobs */
router.get('/usage/jobs', requireAuth, requireAdmin, async (req, res) => {
    try {
        const { from, to } = parseDateRange(req);
        const limit = Math.min(parseInt(req.query.limit, 10) || 50, 200);
        const offset = parseInt(req.query.offset, 10) || 0;

        const { data, error, count } = await supabase
            .from('jobs')
            .select('id, video_title, video_url, status, usage_summary, created_at', {
                count: 'exact',
            })
            .gte('created_at', from)
            .lte('created_at', to)
            .order('created_at', { ascending: false })
            .range(offset, offset + limit - 1);

        if (error) throw error;

        const jobs = (data || []).map((j) => ({
            id: j.id,
            video_title: j.video_title,
            video_url: j.video_url,
            status: j.status,
            created_at: j.created_at,
            total_cost_usd: num(j.usage_summary?.total_cost_usd),
            total_input_tokens: j.usage_summary?.total_input_tokens || 0,
            total_output_tokens: j.usage_summary?.total_output_tokens || 0,
            whisper_seconds: num(j.usage_summary?.whisper_seconds),
            event_count: j.usage_summary?.event_count || 0,
        }));

        res.json({ jobs, total: count ?? jobs.length, limit, offset });
    } catch (err) {
        console.error('admin/usage/jobs error:', err.message);
        res.status(500).json({ error: 'Failed to load jobs usage' });
    }
});

/** GET /admin/usage/jobs/:jobId */
router.get('/usage/jobs/:jobId', requireAuth, requireAdmin, async (req, res) => {
    try {
        const { jobId } = req.params;

        const { data: job, error: jobErr } = await supabase
            .from('jobs')
            .select('id, video_title, video_url, status, usage_summary, created_at, user_id')
            .eq('id', jobId)
            .single();

        if (jobErr || !job) {
            return res.status(404).json({ error: 'Job not found' });
        }

        const { data: events, error: evErr } = await supabase
            .from('job_usage_events')
            .select('*')
            .eq('job_id', jobId)
            .order('created_at', { ascending: true });

        if (evErr) throw evErr;

        const actualCost = num(job.usage_summary?.total_cost_usd)
            || (events || []).reduce((s, e) => s + num(e.estimated_cost_usd), 0);

        res.json({
            job,
            events: events || [],
            comparison: {
                actual_cost_usd: round(actualCost),
                canvas_happy_path_usd: CANVAS_ESTIMATES.happy_path,
                canvas_current_config_usd: CANVAS_ESTIMATES.current_config,
                delta_vs_happy: round(actualCost - CANVAS_ESTIMATES.happy_path),
                delta_vs_current: round(actualCost - CANVAS_ESTIMATES.current_config),
            },
        });
    } catch (err) {
        console.error('admin/usage/jobs/:jobId error:', err.message);
        res.status(500).json({ error: 'Failed to load job usage detail' });
    }
});

/** GET /admin/usage/breakdown */
router.get('/usage/breakdown', requireAuth, requireAdmin, async (req, res) => {
    try {
        const { from, to } = parseDateRange(req);

        const { data: events, error } = await supabase
            .from('job_usage_events')
            .select('task, model, provider, estimated_cost_usd, input_tokens, output_tokens, audio_seconds, cache_hit')
            .gte('created_at', from)
            .lte('created_at', to);

        if (error) throw error;

        const byTask = {};
        const byModel = {};
        const byProvider = {};

        for (const e of events || []) {
            const cost = num(e.estimated_cost_usd);
            byTask[e.task] = (byTask[e.task] || 0) + cost;
            if (e.model) byModel[e.model] = (byModel[e.model] || 0) + cost;
            if (e.provider) byProvider[e.provider] = (byProvider[e.provider] || 0) + cost;
        }

        res.json({
            period: { from, to },
            event_count: (events || []).length,
            by_task: mapRound(byTask),
            by_model: mapRound(byModel),
            by_provider: mapRound(byProvider),
        });
    } catch (err) {
        console.error('admin/usage/breakdown error:', err.message);
        res.status(500).json({ error: 'Failed to load usage breakdown' });
    }
});

function round(n, decimals = 4) {
    const f = 10 ** decimals;
    return Math.round(n * f) / f;
}

function mapRound(obj) {
    const out = {};
    for (const [k, v] of Object.entries(obj)) {
        out[k] = round(v);
    }
    return out;
}

module.exports = router;
