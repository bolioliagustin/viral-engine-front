/**
 * Clip Edits Router (Phase 3 — foundation)
 *
 * Endpoints to save/retrieve user-driven post-clip edits and queue
 * a re-render job. The actual re-render is handled by the worker
 * (it polls clip_edits.status='queued').
 *
 * For now the heavy lifting (worker side) is NOT IMPLEMENTED — these
 * endpoints persist the user's intent in `clip_edits` and respond
 * with 202 Accepted. When the worker side ships, status transitions
 * will flow naturally without API contract changes.
 */
const express = require('express');
const { createClient } = require('@supabase/supabase-js');
const logger = require('../lib/logger');

const router = express.Router();

const getSupabase = () => {
    if (!process.env.SUPABASE_URL) return null;
    return createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_KEY);
};

/**
 * GET /api/clips/:contentResultId/edit
 * Returns the latest draft/queued edit for this clip (or null).
 */
router.get('/api/clips/:contentResultId/edit', async (req, res) => {
    const supabase = getSupabase();
    if (!supabase) return res.status(503).json({ error: 'Database not configured' });

    try {
        const { data, error } = await supabase
            .from('clip_edits')
            .select('*')
            .eq('content_result_id', req.params.contentResultId)
            .order('created_at', { ascending: false })
            .limit(1);

        if (error) throw error;
        res.json({ edit: data?.[0] || null });
    } catch (e) {
        logger.error('GET /api/clips/:id/edit failed', { error: e.message });
        res.status(500).json({ error: 'Failed to fetch edit' });
    }
});

/**
 * POST /api/clips/:contentResultId/edit
 * Persist an edit draft. Body matches clip_edits columns.
 */
router.post('/api/clips/:contentResultId/edit', async (req, res) => {
    const supabase = getSupabase();
    if (!supabase) return res.status(503).json({ error: 'Database not configured' });

    const {
        overlay_text,
        overlay_position,
        subtitle_style,
        overlay_style,
        word_corrections,
        trim_start_offset,
        trim_end_offset,
        music_track_id,
        user_id,
    } = req.body || {};

    try {
        const { data, error } = await supabase
            .from('clip_edits')
            .insert({
                content_result_id: req.params.contentResultId,
                user_id: user_id || null,
                overlay_text,
                overlay_position,
                subtitle_style,
                overlay_style,
                word_corrections,
                trim_start_offset,
                trim_end_offset,
                music_track_id,
                status: 'draft',
            })
            .select()
            .single();

        if (error) throw error;
        res.status(201).json({ edit: data });
    } catch (e) {
        logger.error('POST /api/clips/:id/edit failed', { error: e.message });
        res.status(500).json({ error: 'Failed to save edit' });
    }
});

/**
 * POST /api/clips/:contentResultId/regenerate
 * Mark the latest edit as 'queued' so the worker picks it up.
 *
 * NOTE: worker side not yet implemented — for now this just
 * transitions state. UI can show "queued" status to the user.
 */
router.post('/api/clips/:contentResultId/regenerate', async (req, res) => {
    const supabase = getSupabase();
    if (!supabase) return res.status(503).json({ error: 'Database not configured' });

    try {
        const { data: latest, error: fetchErr } = await supabase
            .from('clip_edits')
            .select('id, status')
            .eq('content_result_id', req.params.contentResultId)
            .order('created_at', { ascending: false })
            .limit(1)
            .single();

        if (fetchErr || !latest) {
            return res.status(404).json({
                error: 'No edit draft found. Save edits first via POST /edit.',
            });
        }

        if (!['draft', 'failed'].includes(latest.status)) {
            return res.status(409).json({
                error: `Cannot regenerate: edit is already ${latest.status}`,
            });
        }

        const { data, error } = await supabase
            .from('clip_edits')
            .update({ status: 'queued', error_message: null })
            .eq('id', latest.id)
            .select()
            .single();

        if (error) throw error;
        res.status(202).json({
            edit: data,
            message: 'Re-render queued. Worker will pick it up shortly.',
            note: 'Worker re-render not yet active — endpoint persisted for forward compatibility.',
        });
    } catch (e) {
        logger.error('POST /api/clips/:id/regenerate failed', { error: e.message });
        res.status(500).json({ error: 'Failed to queue regeneration' });
    }
});

module.exports = router;
