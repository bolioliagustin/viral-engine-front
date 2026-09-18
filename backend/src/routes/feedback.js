/**
 * Feedback humano de clips — "¿lo publicarías tal cual?" (W7)
 *
 * Fuente de verdad de calidad (docs/PLAN_CALIDAD.md §2): el juez
 * (content_results.score_judge) es un proxy automático que se calibra
 * contra esta etiqueta humana, no al revés. Un usuario puede etiquetar el
 * mismo clip más de una vez (se guarda historial completo en clip_feedback;
 * este endpoint siempre lee/reporta la fila más reciente).
 */
const express = require('express');
const { supabase } = require('../lib/supabase');
const { requireAuth } = require('../middleware/auth');
const { verifyClipOwnership } = require('./clip-edits');
const logger = require('../lib/logger');

const router = express.Router();

// Misma lista que el CHECK de la migración (supabase/migrations/*_clip_feedback.sql).
const MOTIVOS = [
    'arranca_mal',
    'termina_mal',
    'momento_flojo',
    'subtitulos_mal',
    'se_ve_mal',
    'copy_malo',
    'otro',
];

/**
 * POST /api/clips/:contentResultId/feedback
 * Body: { posteable: boolean, motivo?: string, comentario?: string }
 */
router.post('/api/clips/:contentResultId/feedback', requireAuth, async (req, res) => {
    if (!supabase) return res.status(503).json({ error: 'Database not configured' });

    const { posteable, comentario } = req.body || {};
    const motivo = req.body?.motivo || null;

    if (typeof posteable !== 'boolean') {
        return res.status(400).json({ error: 'posteable (boolean) is required' });
    }
    if (motivo !== null && !MOTIVOS.includes(motivo)) {
        return res.status(400).json({
            error: `Invalid motivo. Must be one of: ${MOTIVOS.join(', ')}`,
        });
    }

    try {
        const ownership = await verifyClipOwnership(supabase, req.params.contentResultId, req.user.id);
        if (!ownership.ok) return res.status(ownership.status).json({ error: ownership.message });

        const { data, error } = await supabase
            .from('clip_feedback')
            .insert({
                content_result_id: req.params.contentResultId,
                user_id: req.user.id,
                posteable,
                motivo,
                comentario: comentario ? String(comentario).trim().slice(0, 1000) : null,
            })
            .select()
            .single();

        if (error) throw error;
        res.status(201).json({ feedback: data });
    } catch (e) {
        logger.error('POST /api/clips/:id/feedback failed', { error: e.message });
        res.status(500).json({ error: 'Failed to save feedback' });
    }
});

/**
 * GET /api/clips/:contentResultId/feedback
 * Devuelve la última etiqueta del usuario autenticado para este clip, o null.
 */
router.get('/api/clips/:contentResultId/feedback', requireAuth, async (req, res) => {
    if (!supabase) return res.status(503).json({ error: 'Database not configured' });

    try {
        const ownership = await verifyClipOwnership(supabase, req.params.contentResultId, req.user.id);
        if (!ownership.ok) return res.status(ownership.status).json({ error: ownership.message });

        const { data, error } = await supabase
            .from('clip_feedback')
            .select('*')
            .eq('content_result_id', req.params.contentResultId)
            .eq('user_id', req.user.id)
            .order('created_at', { ascending: false })
            .limit(1);

        if (error) throw error;
        res.json({ feedback: data?.[0] || null });
    } catch (e) {
        logger.error('GET /api/clips/:id/feedback failed', { error: e.message });
        res.status(500).json({ error: 'Failed to fetch feedback' });
    }
});

module.exports = router;
module.exports.MOTIVOS = MOTIVOS;
