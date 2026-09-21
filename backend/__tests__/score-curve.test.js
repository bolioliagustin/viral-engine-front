/**
 * W10 — capa de presentación del score ("Score visible", CONTEXT.md).
 */
const {
    curveMomentScores,
    letterGrade,
    judgeSum,
    gradesFor,
    normalizeScoreJudge,
} = require('../src/lib/score-curve');

function sj(hook, retention, shareability, reasoning = 'ok') {
    return { hook, retention, shareability, reasoning };
}

describe('letterGrade', () => {
    test.each([
        [10, 'A'], [9, 'A'],
        [8, 'B'], [7, 'B'],
        [6, 'C'], [5, 'C'],
        [4, 'D'], [1, 'D'],
    ])('%i -> %s', (score, expected) => {
        expect(letterGrade(score)).toBe(expected);
    });
});

describe('normalizeScoreJudge', () => {
    test('acepta objeto', () => {
        expect(normalizeScoreJudge(sj(8, 7, 9))).toEqual({ hook: 8, retention: 7, shareability: 9 });
    });

    test('acepta string JSON (serialización del worker)', () => {
        expect(normalizeScoreJudge(JSON.stringify(sj(8, 7, 9)))).toEqual({
            hook: 8, retention: 7, shareability: 9,
        });
    });

    test('null si falta una métrica', () => {
        expect(normalizeScoreJudge({ hook: 8, retention: 7 })).toBeNull();
    });

    test('null si es null/undefined/string inválido', () => {
        expect(normalizeScoreJudge(null)).toBeNull();
        expect(normalizeScoreJudge(undefined)).toBeNull();
        expect(normalizeScoreJudge('no es json')).toBeNull();
    });
});

describe('judgeSum / gradesFor', () => {
    test('judgeSum suma las tres métricas', () => {
        expect(judgeSum(sj(8, 7, 9))).toBe(24);
    });

    test('judgeSum null sin juez', () => {
        expect(judgeSum(null)).toBeNull();
    });

    test('gradesFor por dimensión', () => {
        expect(gradesFor(sj(9, 6, 3))).toEqual({ hook: 'A', retention: 'C', shareability: 'D' });
    });

    test('gradesFor null sin juez', () => {
        expect(gradesFor(null)).toBeNull();
    });
});

describe('curveMomentScores', () => {
    test('un solo momento con juez -> 99', () => {
        const out = curveMomentScores([{ moment_index: 1, score_judge: sj(5, 5, 5) }]);
        expect(out.get(1).score_display).toBe(99);
        expect(out.get(1).grades).toEqual({ hook: 'C', retention: 'C', shareability: 'C' });
    });

    test('el mejor siempre es 99', () => {
        const out = curveMomentScores([
            { moment_index: 1, score_judge: sj(9, 9, 9) },
            { moment_index: 2, score_judge: sj(3, 3, 3) },
        ]);
        expect(out.get(1).score_display).toBe(99);
    });

    test('cada siguiente baja según la distancia (1 juez = 2 display), piso 60', () => {
        // sumas: 27, 24, 21, 6 -> deltas 3,3,15 -> drops 6,6,30(capado por piso)
        const out = curveMomentScores([
            { moment_index: 1, score_judge: sj(9, 9, 9) },  // 27
            { moment_index: 2, score_judge: sj(8, 8, 8) },  // 24
            { moment_index: 3, score_judge: sj(7, 7, 7) },  // 21
            { moment_index: 4, score_judge: sj(2, 2, 2) },  // 6
        ]);
        expect(out.get(1).score_display).toBe(99);
        expect(out.get(2).score_display).toBe(93); // 99 - 6
        expect(out.get(3).score_display).toBe(87); // 93 - 6
        expect(out.get(4).score_display).toBe(60); // piso, no 87-30=57
    });

    test('mínimo 1 punto de bajada por puesto aunque la diferencia sea chica', () => {
        const out = curveMomentScores([
            { moment_index: 1, score_judge: sj(9, 9, 8) }, // 26
            { moment_index: 2, score_judge: sj(9, 9, 7) }, // 25 (delta 1 -> drop max(1, 2)=2)
        ]);
        expect(out.get(1).score_display).toBe(99);
        expect(out.get(2).score_display).toBe(97);
    });

    test('empates -> mismo número, no bajan', () => {
        const out = curveMomentScores([
            { moment_index: 1, score_judge: sj(8, 8, 8) }, // 24
            { moment_index: 2, score_judge: sj(9, 7, 8) }, // 24 (empate)
            { moment_index: 3, score_judge: sj(5, 5, 5) }, // 15
        ]);
        expect(out.get(1).score_display).toBe(99);
        expect(out.get(2).score_display).toBe(99);
        expect(out.get(3).score_display).toBeLessThan(99);
    });

    test('momento sin juez -> score_display null, no participa del ranking', () => {
        const out = curveMomentScores([
            { moment_index: 1, score_judge: sj(9, 9, 9) },
            { moment_index: 2, score_judge: null },
            { moment_index: 3, score_judge: sj(5, 5, 5) },
        ]);
        expect(out.get(1).score_display).toBe(99);
        expect(out.get(2).score_display).toBeNull();
        expect(out.get(2).grades).toBeNull();
        expect(out.get(3).score_display).toBeLessThan(99);
    });

    test('mismo juez en dos corridas -> misma curva (determinístico)', () => {
        const moments = [
            { moment_index: 1, score_judge: sj(7, 6, 8) },
            { moment_index: 2, score_judge: sj(9, 8, 9) },
            { moment_index: 3, score_judge: sj(4, 5, 3) },
        ];
        const a = curveMomentScores(moments);
        const b = curveMomentScores(moments.map((m) => ({ ...m })));
        for (const m of moments) {
            expect(a.get(m.moment_index)).toEqual(b.get(m.moment_index));
        }
    });

    test('nunca baja del piso 60 con muchos momentos muy dispersos', () => {
        const moments = Array.from({ length: 10 }, (_, i) => ({
            moment_index: i + 1,
            score_judge: sj(10 - i, 10 - i, 10 - i),
        }));
        const out = curveMomentScores(moments);
        for (const m of moments) {
            expect(out.get(m.moment_index).score_display).toBeGreaterThanOrEqual(60);
        }
    });

    test('lista vacía no rompe', () => {
        expect(curveMomentScores([]).size).toBe(0);
        expect(curveMomentScores(undefined).size).toBe(0);
    });

    test('acepta score_judge serializado como string (worker)', () => {
        const out = curveMomentScores([
            { moment_index: 1, score_judge: JSON.stringify(sj(9, 9, 9)) },
        ]);
        expect(out.get(1).score_display).toBe(99);
    });
});
