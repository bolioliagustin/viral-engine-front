/**
 * Clip Edits Router
 *
 * Save/retrieve post-clip edits and queue re-renders.
 * Worker polls clip_edits.status='queued' and processes via clip_edit_processor.
 */
const express = require('express');
const { createClient } = require('@supabase/supabase-js');
const { requireAuth } = require('../middleware/auth');
const logger = require('../lib/logger');

const router = express.Router();

const getSupabase = () => {
    if (!process.env.SUPABASE_URL) return null;
    return createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_KEY);
};

/** Verify the authenticated user owns the content_result (via jobs.user_id). */
async function verifyClipOwnership(supabase, contentResultId, userId) {
    const { data, error } = await supabase
        .from('content_results')
        .select('id, jobs!inner(user_id)')
        .eq('id', contentResultId)
        .single();

    if (error || !data) return { ok: false, status: 404, message: 'Clip not found' };
    if (data.jobs.user_id !== userId) {
        return { ok: false, status: 403, message: 'Not authorized to edit this clip' };
    }
    return { ok: true };
}

/**
 * GET /api/clips/:contentResultId/edit
 */
router.get('/api/clips/:contentResultId/edit', requireAuth, async (req, res) => {
    const supabase = getSupabase();
    if (!supabase) return res.status(503).json({ error: 'Database not configured' });

    try {
        const ownership = await verifyClipOwnership(supabase, req.params.contentResultId, req.user.id);
        if (!ownership.ok) return res.status(ownership.status).json({ error: ownership.message });

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
 */
router.post('/api/clips/:contentResultId/edit', requireAuth, async (req, res) => {
    const supabase = getSupabase();
    if (!supabase) return res.status(503).json({ error: 'Database not configured' });

    const {
        overlay_text,
        overlay_position,
        subtitle_style,
        overlay_style,
        word_corrections,
        word_styles,
        trim_start_offset,
        trim_end_offset,
        music_track_id,
    } = req.body || {};

    try {
        const ownership = await verifyClipOwnership(supabase, req.params.contentResultId, req.user.id);
        if (!ownership.ok) return res.status(ownership.status).json({ error: ownership.message });

        const { data, error } = await supabase
            .from('clip_edits')
            .insert({
                content_result_id: req.params.contentResultId,
                user_id: req.user.id,
                overlay_text,
                overlay_position,
                subtitle_style,
                overlay_style,
                word_corrections,
                word_styles,
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
 */
router.post('/api/clips/:contentResultId/regenerate', requireAuth, async (req, res) => {
    const supabase = getSupabase();
    if (!supabase) return res.status(503).json({ error: 'Database not configured' });

    try {
        const ownership = await verifyClipOwnership(supabase, req.params.contentResultId, req.user.id);
        if (!ownership.ok) return res.status(ownership.status).json({ error: ownership.message });

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
        });
    } catch (e) {
        logger.error('POST /api/clips/:id/regenerate failed', { error: e.message });
        res.status(500).json({ error: 'Failed to queue regeneration' });
    }
});

module.exports = router;
