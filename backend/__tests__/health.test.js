/**
 * C2a: Health Check Tests (C3)
 * Tests the advanced health check endpoint
 */
const request = require('supertest');

jest.mock('../src/lib/supabase', () => ({
    supabase: {
        from: jest.fn(() => ({
            select: jest.fn(() => ({
                eq: jest.fn(() => ({
                    single: jest.fn().mockResolvedValue({ data: null, error: null }),
                    limit: jest.fn().mockResolvedValue({ data: [], error: null })
                })),
                order: jest.fn(() => ({
                    limit: jest.fn().mockResolvedValue({ data: [], error: null })
                }))
            })),
            insert: jest.fn().mockResolvedValue({ error: null })
        })),
        rpc: jest.fn().mockResolvedValue({ data: [], error: null }),
        auth: {
            getUser: jest.fn().mockResolvedValue({
                data: { user: null },
                error: { message: 'No token' }
            })
        }
    }
}));

const app = require('../src/app');

describe('GET /health - Advanced Health Check (C3)', () => {
    test('should return 200 with health status', async () => {
        const res = await request(app).get('/health');

        // Should return either 200 or 503 (depends on env)
        expect([200, 503]).toContain(res.status);
        expect(res.body).toHaveProperty('api');
        expect(res.body).toHaveProperty('database');
        expect(res.body).toHaveProperty('queue');
        expect(res.body).toHaveProperty('timestamp');
        expect(res.body).toHaveProperty('uptime');
        expect(res.body.api.status).toBe('ok');
    });

    test('should include timestamp in ISO format', async () => {
        const res = await request(app).get('/health');

        const timestamp = new Date(res.body.timestamp);
        expect(timestamp.toISOString()).toBe(res.body.timestamp);
    });

    test('should include uptime as a number', async () => {
        const res = await request(app).get('/health');

        expect(typeof res.body.uptime).toBe('number');
        expect(res.body.uptime).toBeGreaterThan(0);
    });
});

describe('GET / - Root Endpoint', () => {
    test('should return API info', async () => {
        const res = await request(app).get('/');

        expect(res.status).toBe(200);
        expect(res.body.name).toBe('YouTube Viral Content Engine');
        expect(res.body.version).toBe('1.0.0');
        expect(res.body.endpoints).toBeDefined();
    });
});

describe('GET /status/:jobId - Job Status', () => {
    test('should return 404 for non-existent job', async () => {
        const res = await request(app)
            .get('/status/non-existent-job-id');

        expect(res.status).toBe(404);
    });
});
