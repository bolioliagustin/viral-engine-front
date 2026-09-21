/**
 * W9-A: GET /status/:jobId agrega preview_url/hd_url/hd_status por resultado
 * (docs/adr/0008). hd_url/hd_status se derivan del último clip_edits
 * (edit_type='hd_upgrade') de cada content_result_id; preview_url es una
 * columna real (migración galeria_hd) que viaja tal cual.
 */
const request = require('supertest');

function makeAwaitable(value) {
    return { then: (resolve, reject) => Promise.resolve(value).then(resolve, reject) };
}

// `let` reasignado en cada beforeEach; el closure de `mockSupabase.from` lee
// esta MISMA variable en cada llamada (no una copia), así que reasignarla
// alcanza para reconfigurar el mock test a test sin recrear el objeto que
// `jobs.js` ya destructuró una sola vez al importarse.
let tableData = {};

// jest hoistea jest.mock() por encima de los imports; solo puede referenciar
// variables cuyo nombre empieza con "mock" (convención ya usada en
// health.test.js/auth.test.js de este mismo repo).
const mockSupabase = {
    from: jest.fn((table) => {
        const builder = {};
        const chain = () => builder;
        builder.select = jest.fn(chain);
        builder.eq = jest.fn(chain);
        builder.in = jest.fn(chain);
        builder.order = jest.fn(chain);
        builder.limit = jest.fn(chain);
        builder.single = jest.fn(() => Promise.resolve(tableData[table]?.single ?? { data: null, error: null }));
        Object.assign(builder, makeAwaitable(tableData[table]?.list ?? { data: null, error: null }));
        return builder;
    }),
    rpc: jest.fn().mockResolvedValue({ data: [], error: null }),
    auth: {
        getUser: jest.fn().mockResolvedValue({ data: { user: null }, error: { message: 'no token' } }),
    },
};

jest.mock('../src/lib/supabase', () => ({ supabase: mockSupabase }));

const app = require('../src/app');

const JOB_ID = 'job-1';

beforeEach(() => {
    tableData = {
        jobs: {
            single: {
                data: {
                    id: JOB_ID,
                    video_url: 'https://youtube.com/watch?v=x',
                    video_title: 'Video de prueba',
                    status: 'completed',
                    current_step: 'completed',
                    progress_percentage: 100,
                    error_message: null,
                    user_id: null, // anónimo: accesible sin auth
                    created_at: '2026-09-18T00:00:00Z',
                    updated_at: '2026-09-18T00:10:00Z',
                },
                error: null,
            },
        },
        content_results: {
            list: {
                data: [
                    {
                        id: 'cr-1', job_id: JOB_ID, moment_index: 0, type: 'twitter_thread',
                        clip_url: 'https://r2.example/clip1.mp4', preview_url: null,
                        score_judge: { hook: 8, retention: 7, shareability: 8, reasoning: 'ok' },
                    },
                    {
                        id: 'cr-2', job_id: JOB_ID, moment_index: 1, type: 'twitter_thread',
                        clip_url: 'https://r2.example/clip2.mp4', preview_url: 'https://r2.example/preview2.jpg',
                        score_judge: { hook: 6, retention: 6, shareability: 6, reasoning: 'ok' },
                    },
                ],
                error: null,
            },
        },
        clip_edits: { list: { data: [], error: null } },
    };
});

describe('GET /status/:jobId — campos de galería (W9-A)', () => {
    test('hd_status "none" y preview_url null cuando no hay pedido de HD ni preview (compatibilidad con jobs viejos)', async () => {
        const res = await request(app).get(`/status/${JOB_ID}`);
        expect(res.status).toBe(200);
        const cr1 = res.body.results.find((r) => r.id === 'cr-1');
        expect(cr1.hd_status).toBe('none');
        expect(cr1.hd_url).toBeNull();
        expect(cr1.preview_url).toBeNull();
    });

    test('preview_url viaja tal cual cuando el worker ya lo generó', async () => {
        const res = await request(app).get(`/status/${JOB_ID}`);
        const cr2 = res.body.results.find((r) => r.id === 'cr-2');
        expect(cr2.preview_url).toBe('https://r2.example/preview2.jpg');
    });

    test('hd_status "queued" mientras el clip_edits de hd_upgrade está en curso', async () => {
        tableData.clip_edits = {
            list: {
                data: [{ content_result_id: 'cr-1', status: 'queued', rendered_clip_url: null, created_at: '2026-09-18T00:05:00Z' }],
                error: null,
            },
        };
        const res = await request(app).get(`/status/${JOB_ID}`);
        const cr1 = res.body.results.find((r) => r.id === 'cr-1');
        expect(cr1.hd_status).toBe('queued');
        expect(cr1.hd_url).toBeNull();
    });

    test('hd_status "ready" y hd_url presente cuando el clip_edits de hd_upgrade completó', async () => {
        tableData.clip_edits = {
            list: {
                data: [{
                    content_result_id: 'cr-1', status: 'completed',
                    rendered_clip_url: 'https://r2.example/hd1.mp4', created_at: '2026-09-18T00:05:00Z',
                }],
                error: null,
            },
        };
        const res = await request(app).get(`/status/${JOB_ID}`);
        const cr1 = res.body.results.find((r) => r.id === 'cr-1');
        expect(cr1.hd_status).toBe('ready');
        expect(cr1.hd_url).toBe('https://r2.example/hd1.mp4');
    });
});

describe('GET /status/:jobId — pantalla de progreso en dos fases (P1)', () => {
    test('progress_detail null y partial false en un job viejo/completado sin la columna', async () => {
        const res = await request(app).get(`/status/${JOB_ID}`);
        expect(res.status).toBe(200);
        expect(res.body.progress_detail).toBeNull();
        expect(res.body.partial).toBe(false);
    });

    test('progress_detail viaja tal cual cuando el worker lo escribió', async () => {
        tableData.jobs.single.data.progress_detail = {
            current: 7, total: 24, message: 'Evaluando candidato 7 de 24', clips_ready: 3,
        };
        const res = await request(app).get(`/status/${JOB_ID}`);
        expect(res.body.progress_detail).toEqual({
            current: 7, total: 24, message: 'Evaluando candidato 7 de 24', clips_ready: 3,
        });
    });

    test('partial true y results ya presentes mientras el job sigue processing', async () => {
        tableData.jobs.single.data.status = 'processing';
        tableData.jobs.single.data.current_step = 'evaluating';
        const res = await request(app).get(`/status/${JOB_ID}`);
        expect(res.status).toBe(200);
        expect(res.body.partial).toBe(true);
        expect(res.body.current_step).toBe('evaluating');
        // Los content_results ya entregados viajan igual que en un job completado.
        expect(res.body.results.length).toBe(2);
    });

    test('partial false cuando el job falló (lo que hay ya es definitivo, no "por venir")', async () => {
        tableData.jobs.single.data.status = 'failed';
        const res = await request(app).get(`/status/${JOB_ID}`);
        expect(res.body.partial).toBe(false);
    });
});
