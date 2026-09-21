/**
 * Alertas por Telegram (F1, docs/PROYECTO.md §14/§15).
 *
 * `notify(text)` manda un mensaje al chat configurado vía la Bot API de
 * Telegram, con `fetch` nativo de Node 20 (sin dependencias nuevas). Sin
 * `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` es un no-op silencioso (solo un
 * log en debug) — para no romper nada en dev/CI sin esas variables.
 *
 * Cómo crear el bot: hablarle a @BotFather en Telegram, `/newbot`, copiar
 * el token a TELEGRAM_BOT_TOKEN. El chat id sale de mandarle un mensaje al
 * bot y pegar la URL `https://api.telegram.org/bot<token>/getUpdates` en
 * el navegador — el `chat.id` de la respuesta es TELEGRAM_CHAT_ID.
 */
const logger = require('./logger');

const TELEGRAM_TIMEOUT_MS = 5000;

/**
 * @param {string} text Mensaje (HTML simple soportado por Telegram: <b>, <i>, <code>).
 * @returns {Promise<{ok: boolean, skipped?: boolean}>}
 */
async function notify(text) {
    const token = process.env.TELEGRAM_BOT_TOKEN;
    const chatId = process.env.TELEGRAM_CHAT_ID;

    if (!token || !chatId) {
        logger.debug('Telegram no configurado (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID) — notify() es no-op', { text });
        return { ok: false, skipped: true };
    }

    try {
        const res = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ chat_id: chatId, text, parse_mode: 'HTML' }),
            signal: AbortSignal.timeout(TELEGRAM_TIMEOUT_MS),
        });

        if (!res.ok) {
            const body = await res.text().catch(() => '');
            logger.error('Telegram notify() falló', { status: res.status, body: body.slice(0, 300) });
            return { ok: false };
        }
        return { ok: true };
    } catch (e) {
        logger.error('Telegram notify() error de red', { error: e.message });
        return { ok: false };
    }
}

module.exports = { notify };
