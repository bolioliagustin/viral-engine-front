/**
 * Alerta por Telegram cuando un job pasa a 'failed' (F1).
 *
 * El backend no controla esa transición: la mayoría de los jobs fallan
 * porque el worker los marca así (`update_job_error`), no por una llamada
 * al API — así que no hay un "endpoint que marca failed" al que engancharle
 * la alerta de forma síncrona. En vez de un trigger de Postgres llamando a
 * un webhook (requeriría la extensión pg_net + una URL pública fija del
 * backend configurada a nivel de base, algo que no se puede verificar
 * desde una migración), se resuelve con un poll liviano acá, en Node,
 * donde `telegram.js` ya vive: cada POLL_INTERVAL_MS se buscan jobs
 * `failed` todavía no alertados (`failure_alert_sent`, migración
 * `creditos_reservados`) y se marcan una vez notificados.
 *
 * Trade-off explícito: hasta POLL_INTERVAL_MS de demora en la alerta: 60s
 * es aceptable para "avisame que algo se rompió", no hace falta tiempo real.
 */
const { supabase } = require('./supabase');
const { notify } = require('./telegram');
const logger = require('./logger');

const POLL_INTERVAL_MS = 60_000;
const BATCH_SIZE = 20;

async function checkFailedJobs() {
    const { data: jobs, error } = await supabase
        .from('jobs')
        .select('id, user_id, video_url, error_message')
        .eq('status', 'failed')
        .eq('failure_alert_sent', false)
        .limit(BATCH_SIZE);

    if (error) {
        logger.error('failure-watcher: error leyendo jobs failed', { error: error.message });
        return;
    }
    if (!jobs || jobs.length === 0) return;

    for (const job of jobs) {
        const text =
            `⚠️ <b>Job falló</b>\n` +
            `ID: <code>${job.id}</code>\n` +
            `Usuario: <code>${job.user_id || 'anónimo'}</code>\n` +
            `Video: ${job.video_url || '—'}\n` +
            `Error: ${(job.error_message || 'sin mensaje').slice(0, 300)}`;

        await notify(text);

        // Se marca como alertado siempre (incluso si notify() falló/no
        // estaba configurado) para no reintentar en loop cada minuto por
        // el mismo job — es un aviso best-effort, no una cola confiable.
        const { error: updateErr } = await supabase
            .from('jobs')
            .update({ failure_alert_sent: true })
            .eq('id', job.id);
        if (updateErr) {
            logger.error('failure-watcher: no se pudo marcar failure_alert_sent', {
                jobId: job.id,
                error: updateErr.message,
            });
        }
    }
}

let intervalHandle = null;

function startFailureWatcher() {
    if (process.env.NODE_ENV === 'test') return; // sin timers en tests
    if (intervalHandle) return; // ya corriendo
    intervalHandle = setInterval(() => {
        checkFailedJobs().catch((e) => logger.error('failure-watcher: error inesperado', { error: e.message }));
    }, POLL_INTERVAL_MS);
    intervalHandle.unref?.(); // no debe mantener vivo el proceso solo por esto
}

function stopFailureWatcher() {
    if (intervalHandle) {
        clearInterval(intervalHandle);
        intervalHandle = null;
    }
}

module.exports = { startFailureWatcher, stopFailureWatcher, checkFailedJobs };
