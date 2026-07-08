/**
 * Constantes y helpers para métricas de costo admin.
 */

const CREDIT_PRICE_USD = 9 / 40;

const LEGACY_CANVAS_ESTIMATES = {
    happy_path: 0.13,
    docs_current_config: 0.20,
};

const TASK_LABELS = {
    classifier: 'Clasificador',
    analysis: 'Selección de momentos',
    copy: 'Copy (pasada B)',
    judge: 'Juez de scoring',
    whisper: 'Whisper',
};

const TASK_ORDER = ['classifier', 'analysis', 'copy', 'judge', 'whisper'];

function num(v) {
    const n = parseFloat(v);
    return Number.isFinite(n) ? n : 0;
}

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

/** Promedia valores numéricos de un array de objetos JSONB (by_task, by_model). */
function avgNestedMaps(jobs, key) {
    const sums = {};
    let count = 0;
    for (const j of jobs) {
        const map = j.usage_summary?.[key];
        if (!map || typeof map !== 'object') continue;
        count += 1;
        for (const [k, v] of Object.entries(map)) {
            sums[k] = (sums[k] || 0) + num(v);
        }
    }
    if (count === 0) return {};
    const out = {};
    for (const [k, v] of Object.entries(sums)) {
        out[k] = round(v / count);
    }
    return out;
}

/**
 * Calcula benchmarks desde jobs completed con usage_summary.
 * @param {Array} jobs - filas de jobs con usage_summary
 */
function computeBenchmarks(jobs) {
    const withSummary = (jobs || []).filter((j) => j.usage_summary && j.usage_summary.event_count > 0);
    const n = withSummary.length;

    if (n === 0) {
        return {
            sample_size: 0,
            avg_cost_per_job: 0,
            avg_by_task: {},
            avg_by_model: {},
            avg_by_provider: {},
            avg_whisper_seconds: 0,
            avg_cache_hits: 0,
            avg_cost_avoided_usd: 0,
            legacy_estimates: LEGACY_CANVAS_ESTIMATES,
        };
    }

    let totalCost = 0;
    let totalWhisper = 0;
    let totalCacheHits = 0;
    let totalAvoided = 0;

    for (const j of withSummary) {
        const s = j.usage_summary;
        totalCost += num(s.total_cost_usd);
        totalWhisper += num(s.whisper_seconds);
        totalCacheHits += parseInt(s.cache_hits, 10) || 0;
        totalAvoided += num(s.cost_avoided_usd);
    }

    return {
        sample_size: n,
        avg_cost_per_job: round(totalCost / n),
        avg_by_task: avgNestedMaps(withSummary, 'by_task'),
        avg_by_model: avgNestedMaps(withSummary, 'by_model'),
        avg_by_provider: avgNestedMaps(withSummary, 'by_provider'),
        avg_whisper_seconds: round(totalWhisper / n, 1),
        avg_cache_hits: round(totalCacheHits / n, 2),
        avg_cost_avoided_usd: round(totalAvoided / n),
        legacy_estimates: LEGACY_CANVAS_ESTIMATES,
    };
}

/** Agrupa eventos por task y moment_index para vista pipeline. */
function groupEventsByPipeline(events) {
    const byTask = {};
    for (const e of events || []) {
        const task = e.task || 'unknown';
        if (!byTask[task]) byTask[task] = [];
        byTask[task].push(e);
    }

    const grouped = {};
    for (const task of TASK_ORDER) {
        const list = byTask[task];
        if (!list?.length) continue;

        const byMoment = {};
        const noMoment = [];
        for (const e of list) {
            if (e.moment_index != null) {
                const k = String(e.moment_index);
                if (!byMoment[k]) byMoment[k] = [];
                byMoment[k].push(e);
            } else {
                noMoment.push(e);
            }
        }

        grouped[task] = {
            label: TASK_LABELS[task] || task,
            total_cost_usd: round(list.reduce((s, e) => s + num(e.estimated_cost_usd), 0)),
            event_count: list.length,
            events: noMoment,
            by_moment: byMoment,
        };
    }

    // Tareas no estándar
    for (const [task, list] of Object.entries(byTask)) {
        if (grouped[task] || !list.length) continue;
        grouped[task] = {
            label: TASK_LABELS[task] || task,
            total_cost_usd: round(list.reduce((s, e) => s + num(e.estimated_cost_usd), 0)),
            event_count: list.length,
            events: list,
            by_moment: {},
        };
    }

    return grouped;
}

module.exports = {
    CREDIT_PRICE_USD,
    LEGACY_CANVAS_ESTIMATES,
    TASK_LABELS,
    TASK_ORDER,
    num,
    round,
    mapRound,
    computeBenchmarks,
    groupEventsByPipeline,
};
