/**
 * C2a: URL Validation Tests
 * Tests the YouTube URL regex and input validation on POST /process
 */
const request = require('supertest');

// Mock Supabase before requiring app
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
            getUser: jest.fn().mockResolvedValue({
                data: { user: { id: 'test-user-123', email: 'test@test.com' } },
                error: null
            })
        }
    }
}));

const app = require('../src/app');

describe('POST /process - URL Validation', () => {
    const validToken = 'valid-test-token';
    const authHeader = { Authorization: `Bearer ${validToken}` };

    test('should reject request without videoUrl', async () => {
        const res = await request(app)
            .post('/process')
            .set(authHeader)
            .send({});

        expect(res.status).toBe(400);
        expect(res.body.error).toBe('videoUrl is required');
    });

    test('should reject empty videoUrl', async () => {
        const res = await request(app)
            .post('/process')
            .set(authHeader)
            .send({ videoUrl: '' });

        expect(res.status).toBe(400);
        expect(res.body.error).toBe('videoUrl is required');
    });

    test.each([
        ['https://vimeo.com/12345'],
        ['https://google.com'],
        ['not-a-url'],
    ])('should reject non-YouTube URL: %s', async (url) => {
        const res = await request(app)
            .post('/process')
            .set(authHeader)
            .send({ videoUrl: url });

        expect(res.status).toBe(400);
        expect(res.body.error).toBe('Invalid YouTube URL');
    });

    test('should accept valid YouTube watch URLs', async () => {
        const validUrls = [
            'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
            'https://youtube.com/watch?v=dQw4w9WgXcQ',
            'http://www.youtube.com/watch?v=dQw4w9WgXcQ',
        ];

        for (const url of validUrls) {
            const res = await request(app)
                .post('/process')
                .set(authHeader)
                .send({ videoUrl: url });

            // Should pass validation (201 = success, or any non-400 status)
            expect(res.status).not.toBe(400);
        }
    });

    test('should accept valid YouTube short URLs', async () => {
        const res = await request(app)
            .post('/process')
            .set(authHeader)
            .send({ videoUrl: 'https://youtu.be/dQw4w9WgXcQ' });

        expect(res.status).not.toBe(400);
    });

    test('should accept YouTube Shorts URLs', async () => {
        const res = await request(app)
            .post('/process')
            .set(authHeader)
            .send({ videoUrl: 'https://www.youtube.com/shorts/dQw4w9WgXcQ' });

        expect(res.status).not.toBe(400);
    });
});
