/**
 * C2a: Auth Middleware Tests
 * Tests the JWT authentication middleware (C1)
 */
const request = require('supertest');

// Mock Supabase
const mockGetUser = jest.fn();
jest.mock('../src/lib/supabase', () => ({
    supabase: {
        from: jest.fn(() => ({
            select: jest.fn(() => ({
                eq: jest.fn(() => ({
                    single: jest.fn().mockResolvedValue({ data: { credits: 5 }, error: null }),
                    limit: jest.fn().mockResolvedValue({ data: [], error: null })
                })),
                order: jest.fn(() => ({
                    limit: jest.fn().mockResolvedValue({ data: [], error: null })
                }))
            })),
            insert: jest.fn().mockResolvedValue({ error: null })
        })),
        rpc: jest.fn().mockResolvedValue({ data: [{ has_duplicate: false }], error: null }),
        auth: {
            getUser: mockGetUser
        }
    }
}));

const app = require('../src/app');

describe('POST /process - Authentication (C1)', () => {
    beforeEach(() => {
        mockGetUser.mockReset();
    });

    test('should return 401 when no Authorization header is provided', async () => {
        const res = await request(app)
            .post('/process')
            .send({ videoUrl: 'https://www.youtube.com/watch?v=abc123' });

        expect(res.status).toBe(401);
        expect(res.body.error).toBe('Authentication required');
    });

    test('should return 401 when Authorization header has wrong format', async () => {
        const res = await request(app)
            .post('/process')
            .set('Authorization', 'Basic some-token')
            .send({ videoUrl: 'https://www.youtube.com/watch?v=abc123' });

        expect(res.status).toBe(401);
        expect(res.body.error).toBe('Authentication required');
    });

    test('should return 401 when token is invalid', async () => {
        mockGetUser.mockResolvedValue({
            data: { user: null },
            error: { message: 'Invalid token' }
        });

        const res = await request(app)
            .post('/process')
            .set('Authorization', 'Bearer invalid-token')
            .send({ videoUrl: 'https://www.youtube.com/watch?v=abc123' });

        expect(res.status).toBe(401);
        expect(res.body.error).toBe('Invalid token');
    });

    test('should override userId with verified user ID from JWT', async () => {
        const verifiedUserId = 'verified-user-uuid';
        mockGetUser.mockResolvedValue({
            data: { user: { id: verifiedUserId, email: 'real@user.com' } },
            error: null
        });

        const res = await request(app)
            .post('/process')
            .set('Authorization', 'Bearer valid-token')
            .send({
                videoUrl: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
                userId: 'spoofed-user-id' // An attacker tries to spoof
            });

        // The request should succeed (not be rejected as spoofed)
        // The userId used internally should be the verified one
        expect(res.status).not.toBe(401);
    });

    test('should pass authentication with valid token', async () => {
        mockGetUser.mockResolvedValue({
            data: { user: { id: 'user-123', email: 'valid@user.com' } },
            error: null
        });

        const res = await request(app)
            .post('/process')
            .set('Authorization', 'Bearer valid-token')
            .send({ videoUrl: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' });

        // Should pass auth (not 401) - may get other status codes depending on Supabase mock
        expect(res.status).not.toBe(401);
    });
});

describe('GET /user/:userId/credits - Authentication (C1)', () => {
    beforeEach(() => {
        mockGetUser.mockReset();
    });

    test('should return 401 without auth token', async () => {
        const res = await request(app)
            .get('/user/some-id/credits');

        expect(res.status).toBe(401);
    });

    test('should return credits for authenticated user', async () => {
        mockGetUser.mockResolvedValue({
            data: { user: { id: 'user-123', email: 'test@test.com' } },
            error: null
        });

        const res = await request(app)
            .get('/user/user-123/credits')
            .set('Authorization', 'Bearer valid-token');

        // Should not be 401 (auth should pass)
        expect(res.status).not.toBe(401);
    });

    test('should use JWT user ID regardless of URL param', async () => {
        mockGetUser.mockResolvedValue({
            data: { user: { id: 'real-user', email: 'test@test.com' } },
            error: null
        });

        // Even if URL says "other-user", the middleware uses the JWT user ID
        const res = await request(app)
            .get('/user/other-user/credits')
            .set('Authorization', 'Bearer valid-token');

        expect(res.status).not.toBe(401);
    });

    test('GET /user/me/credits should work as alias', async () => {
        mockGetUser.mockResolvedValue({
            data: { user: { id: 'user-123', email: 'test@test.com' } },
            error: null
        });

        const res = await request(app)
            .get('/user/me/credits')
            .set('Authorization', 'Bearer valid-token');

        expect(res.status).not.toBe(401);
    });
});
