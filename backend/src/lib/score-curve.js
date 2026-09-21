/**
 * Capa de presentación del score (W10, docs/PLAN_CALIDAD.md §9 Fase 0).
 *
 * "Score visible" (CONTEXT.md) es un percentil curvado 60-99 sobre el
 * ranking del juez DENTRO de un job, más una letra A-D por dimensión
 * (hook/retention/shareability). Es puramente presentación: no toca el
 * "Score" del juez (1-10, interno) ni el ranking que usa el pipeline para
 * elegir o renderizar momentos — inspirado en cómo Opus Clip muestra
 * 83-99 con letras sobre un juez que tampoco discrimina mucho en crudo
 * (docs/ANALISIS_OPUS_CLIP.md §2.3).
 */

const DISPLAY_MAX = 99;
const DISPLAY_FLOOR = 60;
const DISPLAY_POINTS_PER_JUDGE_POINT = 2;

/** Suma de las tres métricas del juez, o null si falta alguna. */
function judgeSum(scoreJudge) {
    const s = normalizeScoreJudge(scoreJudge);
    if (!s) return null;
    return s.hook + s.retention + s.shareability;
}

/** Letra por métrica: 9-10 A, 7-8 B, 5-6 C, <=4 D. */
function letterGrade(score) {
    if (score >= 9) return 'A';
    if (score >= 7) return 'B';
    if (score >= 5) return 'C';
    return 'D';
}

/** {hook, retention, shareability} → letra por dimensión, o null si falta el juez. */
function gradesFor(scoreJudge) {
    const s = normalizeScoreJudge(scoreJudge);
    if (!s) return null;
    return {
        hook: letterGrade(s.hook),
        retention: letterGrade(s.retention),
        shareability: letterGrade(s.shareability),
    };
}

/**
 * score_judge llega de Supabase normalmente como objeto (columna jsonb),
 * pero el worker lo serializa como string antes del insert (Python
 * json.dumps) y en algún caso legacy puede llegar sin castear — se
 * normaliza acá para no repetir el parseo en cada caller.
 */
function normalizeScoreJudge(scoreJudge) {
    let s = scoreJudge;
    if (typeof s === 'string') {
        try {
            s = JSON.parse(s);
        } catch {
            return null;
        }
    }
    if (!s || typeof s !== 'object') return null;
    const { hook, retention, shareability } = s;
    if (![hook, retention, shareability].every((v) => typeof v === 'number' && Number.isFinite(v))) {
        return null;
    }
    return { hook, retention, shareability };
}

/**
 * Curva los momentos de UN job por la suma del juez.
 *
 * @param {Array<{moment_index: number, score_judge: object|string|null}>} moments
 * @returns {Map<number, {score_display: number|null, grades: object|null}>}
 *
 * Regla: ordenar por suma del juez desc; el mejor → 99; cada siguiente baja
 * según la distancia en la suma (1 punto de juez = 2 de display, mínimo 1
 * de bajada por puesto cuando la suma difiere), piso 60; empates (misma
 * suma) → mismo número; un solo momento con juez → 99; sin juez → null.
 */
function curveMomentScores(moments) {
    const out = new Map();

    const withJudge = [];
    for (const m of moments || []) {
        const sum = judgeSum(m.score_judge);
        const grades = gradesFor(m.score_judge);
        if (sum === null) {
            out.set(m.moment_index, { score_display: null, grades });
            continue;
        }
        withJudge.push({ moment_index: m.moment_index, sum, grades });
    }

    withJudge.sort((a, b) => b.sum - a.sum);

    let prevDisplay = null;
    let prevSum = null;
    for (const m of withJudge) {
        let display;
        if (prevDisplay === null) {
            display = DISPLAY_MAX;
        } else if (m.sum === prevSum) {
            display = prevDisplay;
        } else {
            const drop = Math.max(1, (prevSum - m.sum) * DISPLAY_POINTS_PER_JUDGE_POINT);
            display = Math.max(DISPLAY_FLOOR, prevDisplay - drop);
        }
        out.set(m.moment_index, { score_display: display, grades: m.grades });
        prevDisplay = display;
        prevSum = m.sum;
    }

    return out;
}

module.exports = {
    curveMomentScores,
    letterGrade,
    judgeSum,
    gradesFor,
    normalizeScoreJudge,
    DISPLAY_MAX,
    DISPLAY_FLOOR,
};
