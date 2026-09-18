/**
 * W7: Feedback humano de clips — "¿lo publicarías tal cual?"
 * Tests de /api/clips/:id/feedback (POST/GET) y /admin/usage/feedback.
 */
const request = require('supertest');

const mockSupabase = {
    from: jest.fn(),
    rpc: jest.fn(),
    auth: {
        getUser: jest.fn(),
    },
};

jest.mock('../src/lib/supabase', () => ({
    supabase: mockSupabase,
}));

const app = require('../src/app');

const OWNER_ID = 'owner-user-id';
const OTHER_USER_ID = 'other-user-id';
const CONTENT_RESULT_ID = 'cr-123';

function mockAuthenticatedUser(id = OWNER_ID, email = 'owner@test.com') {
    mockSupabase.auth.getUser.mockResolvedValue({
        data: { user: { id, email } },
        error: null,
    });
}

/** content_results.select('id, jobs!inner(user_id)').eq('id', X).single() */
function mockOwnershipCheck({ ownerId = OWNER_ID, found = true } = {}) {
    return {
        select: jest.fn().mockReturnValue({
            eq: jest.fn().mockReturnValue({
                single: jest.fn().mockResolvedValue(
                    found
                        ? { data: { id: CONTENT_RESULT_ID, jobs: { user_id: ownerId } }, error: null }
                        : { data: null, error: { message: 'not found' } }
                ),
            }),
        }),
    };
}

function resetMocks() {
    mockSupabase.auth.getUser.mockReset();
    mockSupabase.from.mockReset();
    mockSupabase.rpc.mockReset();
    delete process.env.ADMIN_USER_IDS;
    delete process.env.ADMIN_EMAILS;
}

describe('POST /api/clips/:id/feedback', () => {
    beforeEach(resetMocks);

    test('401 sin token', async () => {
        const res = await request(app)
            .post(`/api/clips/${CONTENT_RESULT_ID}/feedback`)
            .send({ posteable: true });

        expect(res.status).toBe(401);
    });

    test('403 si el clip no es del usuario autenticado', async () => {
        mockAuthenticatedUser();
        mockSupabase.from.mockImplementation((table) => {
            if (table === 'content_results') return mockOwnershipCheck({ ownerId: OTHER_USER_ID });
            return {};
        });

        const res = await request(app)
            .post(`/api/clips/${CONTENT_RESULT_ID}/feedback`)
            .set('Authorization', 'Bearer valid-token')
            .send({ posteable: false, motivo: 'arranca_mal' });

        expect(res.status).toBe(403);
    });

    test('400 con motivo inválido', async () => {
        mockAuthenticatedUser();
        // No debería ni llegar a consultar ownership, pero por si acaso lo dejamos listo.
        mockSupabase.from.mockImplementation((table) => {
            if (table === 'content_results') return mockOwnershipCheck();
            return {};
        });

        const res = await request(app)
            .post(`/api/clips/${CONTENT_RESULT_ID}/feedback`)
            .set('Authorization', 'Bearer valid-token')
            .send({ posteable: false, motivo: 'motivo_inventado' });

        expect(res.status).toBe(400);
        expect(res.body.error).toMatch(/Invalid motivo/);
    });

    test('400 si falta posteable o no es boolean', async () => {
        mockAuthenticatedUser();

        const res = await request(app)
            .post(`/api/clips/${CONTENT_RESULT_ID}/feedback`)
            .set('Authorization', 'Bearer valid-token')
            .send({ motivo: 'arranca_mal' });

        expect(res.status).toBe(400);
    });

    test('201 en el caso feliz (posteable=true, sin motivo)', async () => {
        mockAuthenticatedUser();
        const insertedRow = {
            id: 'fb-1',
            content_result_id: CONTENT_RESULT_ID,
            user_id: OWNER_ID,
            posteable: true,
            motivo: null,
            comentario: null,
            created_at: '2026-09-18T00:00:00Z',
        };
        const mockInsert = jest.fn().mockReturnValue({
            select: jest.fn().mockReturnValue({
                single: jest.fn().mockResolvedValue({ data: insertedRow, error: null }),
            }),
        });
        mockSupabase.from.mockImplementation((table) => {
            if (table === 'content_results') return mockOwnershipCheck();
            if (table === 'clip_feedback') return { insert: mockInsert };
            return {};
        });

        const res = await request(app)
            .post(`/api/clips/${CONTENT_RESULT_ID}/feedback`)
            .set('Authorization', 'Bearer valid-token')
            .send({ posteable: true });

        expect(res.status).toBe(201);
        expect(res.body.feedback).toEqual(insertedRow);
        expect(mockInsert).toHaveBeenCalledWith(
            expect.objectContaining({
                content_result_id: CONTENT_RESULT_ID,
                user_id: OWNER_ID,
                posteable: true,
                motivo: null,
            })
        );
    });

    test('201 con posteable=false, motivo válido y comentario', async () => {
        mockAuthenticatedUser();
        const insertedRow = {
            id: 'fb-2',
            content_result_id: CONTENT_RESULT_ID,
            user_id: OWNER_ID,
            posteable: false,
            motivo: 'termina_mal',
            comentario: 'se corta a mitad de frase',
            created_at: '2026-09-18T00:00:00Z',
        };
        const mockInsert = jest.fn().mockReturnValue({
            select: jest.fn().mockReturnValue({
                single: jest.fn().mockResolvedValue({ data: insertedRow, error: null }),
            }),
        });
        mockSupabase.from.mockImplementation((table) => {
            if (table === 'content_results') return mockOwnershipCheck();
            if (table === 'clip_feedback') return { insert: mockInsert };
            return {};
        });

        const res = await request(app)
            .post(`/api/clips/${CONTENT_RESULT_ID}/feedback`)
            .set('Authorization', 'Bearer valid-token')
            .send({ posteable: false, motivo: 'termina_mal', comentario: 'se corta a mitad de frase' });

        expect(res.status).toBe(201);
        expect(res.body.feedback.motivo).toBe('termina_mal');
    });
});

describe('GET /api/clips/:id/feedback', () => {
    beforeEach(resetMocks);

    test('401 sin token', async () => {
        const res = await request(app).get(`/api/clips/${CONTENT_RESULT_ID}/feedback`);
        expect(res.status).toBe(401);
    });

    test('403 si el clip no es del usuario', async () => {
        mockAuthenticatedUser();
        mockSupabase.from.mockImplementation((table) => {
            if (table === 'content_results') return mockOwnershipCheck({ ownerId: OTHER_USER_ID });
            return {};
        });

        const res = await request(app)
            .get(`/api/clips/${CONTENT_RESULT_ID}/feedback`)
            .set('Authorization', 'Bearer valid-token');

        expect(res.status).toBe(403);
    });

    test('devuelve la última etiqueta del usuario', async () => {
        mockAuthenticatedUser();
        const row = { id: 'fb-1', posteable: true, motivo: null, created_at: '2026-09-18T00:00:00Z' };
        mockSupabase.from.mockImplementation((table) => {
            if (table === 'content_results') return mockOwnershipCheck();
            if (table === 'clip_feedback') {
                return {
                    select: jest.fn().mockReturnValue({
                        eq: jest.fn().mockReturnValue({
                            eq: jest.fn().mockReturnValue({
                                order: jest.fn().mockReturnValue({
                                    limit: jest.fn().mockResolvedValue({ data: [row], error: null }),
                                }),
                            }),
                        }),
                    }),
                };
            }
            return {};
        });

        const res = await request(app)
            .get(`/api/clips/${CONTENT_RESULT_ID}/feedback`)
            .set('Authorization', 'Bearer valid-token');

        expect(res.status).toBe(200);
        expect(res.body.feedback).toEqual(row);
    });

    test('devuelve null cuando no hay etiqueta todavía', async () => {
        mockAuthenticatedUser();
        mockSupabase.from.mockImplementation((table) => {
            if (table === 'content_results') return mockOwnershipCheck();
            if (table === 'clip_feedback') {
                return {
                    select: jest.fn().mockReturnValue({
                        eq: jest.fn().mockReturnValue({
                            eq: jest.fn().mockReturnValue({
                                order: jest.fn().mockReturnValue({
                                    limit: jest.fn().mockResolvedValue({ data: [], error: null }),
                                }),
                            }),
                        }),
                    }),
                };
            }
            return {};
        });

        const res = await request(app)
            .get(`/api/clips/${CONTENT_RESULT_ID}/feedback`)
            .set('Authorization', 'Bearer valid-token');

        expect(res.status).toBe(200);
        expect(res.body.feedback).toBeNull();
    });
});

describe('GET /admin/usage/feedback', () => {
    beforeEach(resetMocks);

    test('403 para usuario no-admin', async () => {
        mockAuthenticatedUser('regular-user', 'regular@test.com');
        process.env.ADMIN_EMAILS = 'admin@test.com';

        const res = await request(app)
            .get('/admin/usage/feedback')
            .set('Authorization', 'Bearer valid-token');

        expect(res.status).toBe(403);
    });

    test('401 sin token', async () => {
        const res = await request(app).get('/admin/usage/feedback');
        expect(res.status).toBe(401);
    });

    test('200 para admin: agrega posteable_rate, by_motivo y judge_avg por grupo', async () => {
        mockAuthenticatedUser('admin-user', 'admin@test.com');
        process.env.ADMIN_EMAILS = 'admin@test.com';

        const rows = [
            {
                id: 'fb-1',
                content_result_id: 'cr-1',
                user_id: OWNER_ID,
                posteable: true,
                motivo: null,
                comentario: null,
                created_at: '2026-09-18T02:00:00Z',
                content_results: { job_id: 'job-1', score_judge: { hook: 8, retention: 7, shareability: 9 } },
            },
            {
                id: 'fb-2',
                content_result_id: 'cr-2',
                user_id: OWNER_ID,
                posteable: false,
                motivo: 'termina_mal',
                comentario: null,
                created_at: '2026-09-18T01:00:00Z',
                content_results: { job_id: 'job-1', score_judge: { hook: 4, retention: 3, shareability: 5 } },
            },
        ];
        mockSupabase.from.mockImplementation((table) => {
            if (table === 'clip_feedback') {
                return {
                    select: jest.fn().mockReturnValue({
                        gte: jest.fn().mockReturnValue({
                            lte: jest.fn().mockReturnValue({
                                order: jest.fn().mockResolvedValue({ data: rows, error: null }),
                            }),
                        }),
                    }),
                };
            }
            return {};
        });

        const res = await request(app)
            .get('/admin/usage/feedback')
            .set('Authorization', 'Bearer valid-token');

        expect(res.status).toBe(200);
        expect(res.body.total).toBe(2);
        expect(res.body.posteable_rate).toBe(50);
        expect(res.body.by_motivo).toEqual({ termina_mal: 1 });
        expect(res.body.judge_avg_posteable).toBeCloseTo(8, 1);
        expect(res.body.judge_avg_no_posteable).toBeCloseTo(4, 1);
        expect(res.body.rows).toHaveLength(2);
        expect(res.body.rows[0]).toEqual(
            expect.objectContaining({ content_result_id: 'cr-1', job_id: 'job-1', posteable: true })
        );
    });

    test('deduplica por content_result_id, quedándose con la fila más reciente', async () => {
        mockAuthenticatedUser('admin-user', 'admin@test.com');
        process.env.ADMIN_EMAILS = 'admin@test.com';

        // Mismo content_result_id re-etiquetado: primero "no", después "sí".
        // El endpoint ordena desc por created_at, así que la más reciente
        // (posteable=true) llega primero en `data`.
        const rows = [
            {
                id: 'fb-2',
                content_result_id: 'cr-1',
                user_id: OWNER_ID,
                posteable: true,
                motivo: null,
                created_at: '2026-09-18T02:00:00Z',
                content_results: { job_id: 'job-1', score_judge: { hook: 8, retention: 8, shareability: 8 } },
            },
            {
                id: 'fb-1',
                content_result_id: 'cr-1',
                user_id: OWNER_ID,
                posteable: false,
                motivo: 'copy_malo',
                created_at: '2026-09-18T01:00:00Z',
                content_results: { job_id: 'job-1', score_judge: { hook: 3, retention: 3, shareability: 3 } },
            },
        ];
        mockSupabase.from.mockImplementation((table) => {
            if (table === 'clip_feedback') {
                return {
                    select: jest.fn().mockReturnValue({
                        gte: jest.fn().mockReturnValue({
                            lte: jest.fn().mockReturnValue({
                                order: jest.fn().mockResolvedValue({ data: rows, error: null }),
                            }),
                        }),
                    }),
                };
            }
            return {};
        });

        const res = await request(app)
            .get('/admin/usage/feedback')
            .set('Authorization', 'Bearer valid-token');

        expect(res.status).toBe(200);
        expect(res.body.total).toBe(1);
        expect(res.body.posteable_rate).toBe(100);
        expect(res.body.by_motivo).toEqual({});
    });
});
