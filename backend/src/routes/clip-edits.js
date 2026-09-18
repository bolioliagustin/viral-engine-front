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

/**
 * POST /api/clips/:contentResultId/hd
 *
 * W9-A (docs/PLAN_CALIDAD.md §9 Fase 1 W9, docs/adr/0008): HD a pedido.
 * Reusa el mecanismo de clip_edits que ya existe (W7) — un pedido de HD es
 * una fila de clip_edits con edit_type='hd_upgrade'; su `status` (que el
 * worker ya actualiza queued→processing→completed|failed sin cambios,
 * clip_edit_processor.py no filtra por edit_type) ES el hd_status. No hay
 * columnas de estado nuevas en content_results — ver la migración
 * `galeria_hd`.
 *
 * Idempotente: llamadas repetidas mientras hay un hd_upgrade en curso no
 * encolan uno nuevo (devuelven 202 con el estado actual); el frontend hace
 * poll llamando este mismo endpoint. Si ya hay un HD listo, 200 con la URL.
 */
router.post('/api/clips/:contentResultId/hd', requireAuth, async (req, res) => {
    const supabase = getSupabase();
    if (!supabase) return res.status(503).json({ error: 'Database not configured' });

    const contentResultId = req.params.contentResultId;

    try {
        const ownership = await verifyClipOwnership(supabase, contentResultId, req.user.id);
        if (!ownership.ok) return res.status(ownership.status).json({ error: ownership.message });

        const { data: existing, error: existingErr } = await supabase
            .from('clip_edits')
            .select('id, status, rendered_clip_url')
            .eq('content_result_id', contentResultId)
            .eq('edit_type', 'hd_upgrade')
            .order('created_at', { ascending: false })
            .limit(1);

        if (existingErr) throw existingErr;

        const latest = existing?.[0];
        if (latest?.status === 'completed' && latest.rendered_clip_url) {
            return res.status(200).json({ hd_url: latest.rendered_clip_url, hd_status: 'ready' });
        }
        if (latest?.status === 'queued' || latest?.status === 'processing') {
            return res.status(202).json({ hd_status: latest.status });
        }
        // Sin pedido previo, o el último terminó en 'failed': encolar uno nuevo.

        const { data: cr, error: crErr } = await supabase
            .from('content_results')
            .select('viral_overlay')
            .eq('id', contentResultId)
            .single();
        if (crErr || !cr) return res.status(404).json({ error: 'Clip not found' });

        const { data: editRow, error: editErr } = await supabase
            .from('clip_edits')
            .insert({
                content_result_id: contentResultId,
                user_id: req.user.id,
                edit_type: 'hd_upgrade',
                // Mejor esfuerzo para que el re-render se parezca al original:
                // el processor cae a subtitle_style/overlay_style por defecto
                // si no vienen (services/clip_edit_processor.py), que hoy
                // coincide con lo que usa main.py — pero no reusa el mismo
                // target_width/height (pendiente, ver ADR 0008).
                overlay_text: cr.viral_overlay || null,
                status: 'queued',
            })
            .select()
            .single();
        if (editErr) throw editErr;

        res.status(202).json({ hd_status: 'queued', clip_edit_id: editRow.id });
    } catch (e) {
        logger.error('POST /api/clips/:id/hd failed', { error: e.message });
        res.status(500).json({ error: 'Failed to request HD render' });
    }
});

module.exports = router;
