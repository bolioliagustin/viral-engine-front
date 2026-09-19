/**
 * F1: POST /admin/alerts/test — manda un mensaje de prueba a Telegram.
 */
const request = require('supertest');

const mockSupabase = {
    from: jest.fn(),
    rpc: jest.fn(),
    auth: { getUser: jest.fn() },
};

jest.mock('../src/lib/supabase', () => ({ supabase: mockSupabase }));
jest.mock('../src/lib/youtube-duration', () => ({
    getVideoDurationMinutes: jest.fn().mockResolvedValue(null),
}));

const mockNotify = jest.fn();
jest.mock('../src/lib/telegram', () => ({ notify: (...args) => mockNotify(...args) }));

const app = require('../src/app');

const ADMIN_ID = 'admin-user-1';
const REGULAR_ID = 'regular-user-1';

function mockUser(userId) {
    mockSupabase.auth.getUser.mockResolvedValue({
        data: { user: { id: userId, email: `${userId}@test.com` } },
        error: null,
    });
}

beforeEach(() => {
    mockSupabase.auth.getUser.mockReset();
    mockNotify.mockReset();
    process.env.ADMIN_USER_IDS = ADMIN_ID;
});

afterAll(() => {
    delete process.env.ADMIN_USER_IDS;
});

describe('POST /admin/alerts/test', () => {
    test('401 sin token', async () => {
        const res = await request(app).post('/admin/alerts/test');
        expect(res.status).toBe(401);
    });

    test('403 si no es admin', async () => {
        mockUser(REGULAR_ID);
        const res = await request(app)
            .post('/admin/alerts/test')
            .set('Authorization', 'Bearer valid-token');
        expect(res.status).toBe(403);
    });

    test('200 y notify() se llama cuando es admin', async () => {
        mockUser(ADMIN_ID);
        mockNotify.mockResolvedValue({ ok: true });

        const res = await request(app)
            .post('/admin/alerts/test')
            .set('Authorization', 'Bearer valid-token');

        expect(res.status).toBe(200);
        expect(res.body.sent).toBe(true);
        expect(mockNotify).toHaveBeenCalledTimes(1);
    });

    test('200 con sent:false si Telegram no está configurado (notify skip)', async () => {
        mockUser(ADMIN_ID);
        mockNotify.mockResolvedValue({ ok: false, skipped: true });

        const res = await request(app)
            .post('/admin/alerts/test')
            .set('Authorization', 'Bearer valid-token');

        expect(res.status).toBe(200);
        expect(res.body.sent).toBe(false);
    });
});
