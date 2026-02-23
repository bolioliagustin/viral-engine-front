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

const app = require('../src/app');

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
        mockSupabase.rpc.mockResolvedValue({ data: [{ has_duplicate: false }], error: null });
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

    test('auth OK but no credits → 402', async () => {
        mockSupabase.from.mockImplementation((table) => {
            if (table === 'users') {
                return {
                    select: jest.fn().mockReturnValue({
                        eq: jest.fn().mockReturnValue({
                            single: jest.fn().mockResolvedValue({
                                data: { credits: 0 },
                                error: null
                            })
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

        const res = await request(app)
            .post('/process')
            .set('Authorization', 'Bearer valid-token')
            .send({ videoUrl: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' });

        expect(res.status).toBe(402);
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
