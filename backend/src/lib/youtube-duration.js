/**
 * Duración de un video de YouTube sin API key (F1, docs/PROYECTO.md §14/§15).
 *
 * No hay integración con la YouTube Data API en el backend (requeriría una
 * key nueva, `.env.example` no la tiene) y agregarla es una dependencia
 * externa nueva que esta tarea no pide. En su lugar, se lee `lengthSeconds`
 * del JSON embebido en el HTML público de la página del video
 * (`ytInitialPlayerResponse`) — mismo dato que usan yt-dlp y otras
 * herramientas sin key, con `fetch` nativo de Node 20 (sin dependencias
 * nuevas).
 *
 * Deliberadamente "fail open": si la red falla, YouTube cambia el HTML, o
 * hay timeout, se devuelve `null` (duración desconocida) en vez de tirar —
 * un problema de scraping no debe bloquear la creación de un job.
 */
const logger = require('./logger');

const FETCH_TIMEOUT_MS = 4000;

function extractVideoId(videoUrl) {
    const match = String(videoUrl || '').match(
        /(?:youtube\.com\/(?:watch\?v=|shorts\/)|youtu\.be\/)([\w-]{11})/
    );
    return match ? match[1] : null;
}

/**
 * @returns {Promise<number|null>} minutos (redondeado), o null si no se pudo determinar.
 */
async function getVideoDurationMinutes(videoUrl) {
    const videoId = extractVideoId(videoUrl);
    if (!videoId) return null;

    try {
        const res = await fetch(`https://www.youtube.com/watch?v=${videoId}`, {
            headers: { 'User-Agent': 'Mozilla/5.0 (compatible; viral-engine/1.0)' },
            signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
        });
        if (!res.ok) return null;

        const html = await res.text();
        const match = html.match(/"lengthSeconds":"(\d+)"/);
        if (!match) return null;

        return Math.round(Number(match[1]) / 60);
    } catch (e) {
        logger.debug('getVideoDurationMinutes: no se pudo determinar la duración (fail open)', {
            videoUrl,
            error: e.message,
        });
        return null;
    }
}

module.exports = { getVideoDurationMinutes, extractVideoId };
