/**
 * Alertas admin (F1, docs/PROYECTO.md §14/§15).
 * Mandar un mensaje de prueba a Telegram para verificar la configuración
 * (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID) sin esperar a que algo se rompa.
 */
const express = require('express');
const { requireAuth } = require('../middleware/auth');
const { requireAdmin } = require('../middleware/admin');
const { notify } = require('../lib/telegram');
const logger = require('../lib/logger');

const router = express.Router();

/**
 * POST /admin/alerts/test
 */
router.post('/alerts/test', requireAuth, requireAdmin, async (req, res) => {
    try {
        const result = await notify(
            `🧪 Mensaje de prueba de ViralEngine — ${new Date().toISOString()}`
        );
        if (result.skipped) {
            return res.status(200).json({
                sent: false,
                message: 'TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID no configurados — no-op',
            });
        }
        res.status(200).json({ sent: result.ok, message: result.ok ? 'Mensaje enviado' : 'Falló el envío (ver logs)' });
    } catch (e) {
        logger.error('POST /admin/alerts/test failed', { error: e.message });
        res.status(500).json({ error: 'Failed to send test alert' });
    }
});

module.exports = router;
