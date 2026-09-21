/**
 * INT1: API Integration Tests
 * Tests the full request lifecycle for critical endpoints.
 * Validates that auth → validation → processing → response chain works end-to-end.
 */
const request = require('supertest');

// Mock Supabase with realistic behavior
const mockSupabase = {
    from: jest.fn(),
    rpc: jest.fn(),
    auth: {
        getUser: jest.fn()
    }
};

jest.mock('../src/lib/supabase', () => ({
    supabase: mockSupabase
}));

// F1: sin esto, POST /process haría un fetch real a YouTube (duración) y a
// Telegram (alerta) en cada test — lento, no determinístico y depende de red.
jest.mock('../src/lib/youtube-duration', () => ({
    getVideoDurationMinutes: jest.fn().mockResolvedValue(null),
}));
jest.mock('../src/lib/telegram', () => ({
    notify: jest.fn().mockResolvedValue({ ok: true }),
}));

const app = require('../src/app');

function mockRpcByName(overrides = {}) {
    mockSupabase.rpc.mockImplementation((fnName, params) => {
        if (overrides[fnName]) return overrides[fnName](params);
        if (fnName === 'check_duplicate_job') {
            return Promise.resolve({ data: [{ has_duplicate: false }], error: null });
        }
        if (fnName === 'reserve_credit') {
            return Promise.resolve({ data: true, error: null });
        }
        if (fnName === 'release_credit') {
            return Promise.resolve({ data: null, error: null });
        }
        return Promise.resolve({ data: null, error: null });
    });
}

// Helper to set up authenticated user mock
function mockAuthenticatedUser(userId = 'int-test-user', email = 'int@test.com') {
    mockSupabase.auth.getUser.mockResolvedValue({
        data: { user: { id: userId, email } },
        error: null
    });
}

// Helper to reset all mocks
function resetMocks() {
    mockSupabase.auth.getUser.mockReset();
    mockSupabase.from.mockReset();
    mockSupabase.rpc.mockReset();
}

describe('INT1: Full /process lifecycle', () => {
    beforeEach(() => {
        resetMocks();
        mockAuthenticatedUser();

        // Mock Supabase responses for the process flow
        mockRpcByName();
        mockSupabase.from.mockImplementation((table) => {
            if (table === 'users') {
                return {
                    select: jest.fn().mockReturnValue({
                        eq: jest.fn().mockReturnValue({
                            single: jest.fn().mockResolvedValue({
                                data: { credits: 10 },
                                error: null
                            })
                        })
                    })
                };
            }
            if (table === 'jobs') {
                return {
                    insert: jest.fn().mockResolvedValue({ error: null }),
                    select: jest.fn().mockReturnValue({
                        eq: jest.fn().mockReturnValue({
                            single: jest.fn().mockResolvedValue({ data: null, error: { message: 'not found' } }),
                            limit: jest.fn().mockResolvedValue({ data: [], error: null })
                        }),
                        order: jest.fn().mockReturnValue({
                            limit: jest.fn().mockResolvedValue({ data: [], error: null })
                        })
                    })
                };
            }
            return {
                select: jest.fn().mockReturnValue({
                    eq: jest.fn().mockReturnValue({
                        single: jest.fn().mockResolvedValue({ data: null, error: null })
                    })
                })
            };
        });
    });

    test('full happy path: auth → validate → create job → 201', async () => {
        const res = await request(app)
            .post('/process')
            .set('Authorization', 'Bearer valid-token')
            .send({ videoUrl: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' });

        expect(res.status).toBe(201);
        expect(res.body).toHaveProperty('jobId');
        expect(res.body).toHaveProperty('status', 'pending');
        expect(res.body).toHaveProperty('message');
    });

    test('full rejection path: no auth → 401', async () => {
        const res = await request(app)
            .post('/process')
            .send({ videoUrl: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' });

        expect(res.status).toBe(401);
        expect(res.body.error).toBe('Authentication required');
    });

    test('auth OK but invalid URL → 400', async () => {
        const res = await request(app)
            .post('/process')
            .set('Authorization', 'Bearer valid-token')
            .send({ videoUrl: 'https://vimeo.com/12345' });

        expect(res.status).toBe(400);
        expect(res.body.error).toBe('Invalid YouTube URL');
    });

    test('auth OK but no credits → 402 (reserve_credit devuelve false)', async () => {
        mockRpcByName({ reserve_credit: () => Promise.resolve({ data: false, error: null }) });

        const res = await request(app)
            .post('/process')
            .set('Authorization', 'Bearer valid-token')
            .send({ videoUrl: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' });

        expect(res.status).toBe(402);
        expect(res.body.error).toBe('Insufficient credits');
    });

    test('F1: video más largo que MAX_VIDEO_MINUTES → 400', async () => {
        mockAuthenticatedUser('f1-user-duration-over');
        const { getVideoDurationMinutes } = require('../src/lib/youtube-duration');
        getVideoDurationMinutes.mockResolvedValueOnce(120);

        const res = await request(app)
            .post('/process')
            .set('Authorization', 'Bearer valid-token')
            .send({ videoUrl: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' });

        expect(res.status).toBe(400);
        expect(res.body.error).toBe('Video too long');
        expect(res.body.message).toMatch(/120 min/);
        expect(res.body.message).toMatch(/90 min/);
    });

    test('F1: duración desconocida (fail open) no bloquea el job', async () => {
        mockAuthenticatedUser('f1-user-duration-unknown');
        const { getVideoDurationMinutes } = require('../src/lib/youtube-duration');
        getVideoDurationMinutes.mockResolvedValueOnce(null);

        const res = await request(app)
            .post('/process')
            .set('Authorization', 'Bearer valid-token')
            .send({ videoUrl: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' });

        expect(res.status).toBe(201);
    });

    test('F1: job creado con credit_reserved=true', async () => {
        mockAuthenticatedUser('f1-user-credit-reserved');
        let insertedRow = null;
        mockSupabase.from.mockImplementation((table) => {
            if (table === 'users') {
                return {
                    select: jest.fn().mockReturnValue({
                        eq: jest.fn().mockReturnValue({
                            single: jest.fn().mockResolvedValue({ data: { credits: 10 }, error: null })
                        })
                    })
                };
            }
            if (table === 'jobs') {
                return {
                    insert: jest.fn((row) => {
                        insertedRow = row;
                        return Promise.resolve({ error: null });
                    }),
                };
            }
            return {
                select: jest.fn().mockReturnValue({
                    eq: jest.fn().mockReturnValue({
                        single: jest.fn().mockResolvedValue({ data: null, error: null })
                    })
                })
            };
        });

        const res = await request(app)
            .post('/process')
            .set('Authorization', 'Bearer valid-token')
            .send({ videoUrl: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' });

        expect(res.status).toBe(201);
        expect(insertedRow).not.toBeNull();
        expect(insertedRow.credit_reserved).toBe(true);
    });

    test('F1: si el insert del job falla tras reservar, se libera el crédito', async () => {
        mockAuthenticatedUser('f1-user-release-on-failure');
        const released = jest.fn().mockResolvedValue({ data: null, error: null });
        mockRpcByName({ release_credit: released });
        mockSupabase.from.mockImplementation((table) => {
            if (table === 'users') {
                return {
                    select: jest.fn().mockReturnValue({
                        eq: jest.fn().mockReturnValue({
                            single: jest.fn().mockResolvedValue({ data: { credits: 10 }, error: null })
                        })
                    })
                };
            }
            if (table === 'jobs') {
                return {
                    insert: jest.fn().mockResolvedValue({ error: { message: 'insert failed' } }),
                };
            }
            return {
                select: jest.fn().mockReturnValue({
                    eq: jest.fn().mockReturnValue({
                        single: jest.fn().mockResolvedValue({ data: null, error: null })
                    })
                })
            };
        });

        const res = await request(app)
            .post('/process')
            .set('Authorization', 'Bearer valid-token')
            .send({ videoUrl: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' });

        expect(res.status).toBe(500);
        expect(released).toHaveBeenCalledWith({ p_user_id: 'f1-user-release-on-failure' });
    });

    test('F1: dos POST seguidos con 1 crédito → el segundo da 402', async () => {
        mockAuthenticatedUser('f1-user-two-in-a-row');
        let credits = 1;
        mockRpcByName({
            reserve_credit: () => {
                if (credits > 0) {
                    credits -= 1;
                    return Promise.resolve({ data: true, error: null });
                }
                return Promise.resolve({ data: false, error: null });
            },
        });

        const first = await request(app)
            .post('/process')
            .set('Authorization', 'Bearer valid-token')
            .send({ videoUrl: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' });
        expect(first.status).toBe(201);

        const second = await request(app)
            .post('/process')
            .set('Authorization', 'Bearer valid-token')
            .send({ videoUrl: 'https://www.youtube.com/watch?v=9bZkp7q19f0' });
        expect(second.status).toBe(402);
    });
});

describe('INT1: Health check integration', () => {
    test('should return structured health response', async () => {
        const res = await request(app).get('/health');

        expect(res.body.api.status).toBe('ok');
        expect(res.body).toHaveProperty('timestamp');
        expect(res.body).toHaveProperty('uptime');
        expect(typeof res.body.uptime).toBe('number');
    });
});
