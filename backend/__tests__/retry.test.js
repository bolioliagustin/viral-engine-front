/**
 * F1 (ADR 0005): POST /jobs/:jobId/retry vuelve a reservar 1 crédito.
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
jest.mock('../src/lib/telegram', () => ({ notify: jest.fn().mockResolvedValue({ ok: true }) }));

const app = require('../src/app');

const USER_ID = 'retry-user-1';
const JOB_ID = 'job-to-retry-1';

function mockAuthenticatedUser(userId = USER_ID) {
    mockSupabase.auth.getUser.mockResolvedValue({
        data: { user: { id: userId, email: `${userId}@test.com` } },
        error: null,
    });
}

/** Builder para .from('jobs').select(...).eq(...).single() (fetch + ownership). */
function jobsFetchBuilder(jobRow) {
    return {
        select: jest.fn().mockReturnValue({
            eq: jest.fn().mockReturnValue({
                single: jest.fn().mockResolvedValue({ data: jobRow, error: jobRow ? null : { message: 'not found' } }),
            }),
        }),
    };
}

beforeEach(() => {
    mockSupabase.from.mockReset();
    mockSupabase.rpc.mockReset();
    mockSupabase.auth.getUser.mockReset();
    mockAuthenticatedUser();
});

describe('POST /jobs/:jobId/retry — F1', () => {
    test('401 sin token', async () => {
        const res = await request(app).post(`/jobs/${JOB_ID}/retry`);
        expect(res.status).toBe(401);
    });

    test('403 si el job no es del usuario', async () => {
        mockSupabase.from.mockImplementationOnce(() =>
            jobsFetchBuilder({ id: JOB_ID, user_id: 'otro-user', status: 'failed' })
        );
        const res = await request(app)
            .post(`/jobs/${JOB_ID}/retry`)
            .set('Authorization', 'Bearer valid-token');
        expect(res.status).toBe(403);
    });

    test('409 si el job no está en estado terminal', async () => {
        mockSupabase.from.mockImplementationOnce(() =>
            jobsFetchBuilder({ id: JOB_ID, user_id: USER_ID, status: 'processing' })
        );
        const res = await request(app)
            .post(`/jobs/${JOB_ID}/retry`)
            .set('Authorization', 'Bearer valid-token');
        expect(res.status).toBe(409);
    });

    test('reserva un crédito nuevo y setea credit_reserved=true', async () => {
        let updatePayload = null;
        mockSupabase.from
            .mockImplementationOnce(() => jobsFetchBuilder({ id: JOB_ID, user_id: USER_ID, status: 'failed' }))
            .mockImplementationOnce(() => ({
                update: jest.fn((payload) => {
                    updatePayload = payload;
                    return {
                        eq: jest.fn().mockReturnValue({
                            select: jest.fn().mockReturnValue({
                                single: jest.fn().mockResolvedValue({
                                    data: { id: JOB_ID, ...payload },
                                    error: null,
                                }),
                            }),
                        }),
                    };
                }),
            }));
        mockSupabase.rpc.mockResolvedValue({ data: true, error: null }); // reserve_credit

        const res = await request(app)
            .post(`/jobs/${JOB_ID}/retry`)
            .set('Authorization', 'Bearer valid-token');

        expect(res.status).toBe(200);
        expect(mockSupabase.rpc).toHaveBeenCalledWith('reserve_credit', { p_user_id: USER_ID });
        expect(updatePayload.credit_reserved).toBe(true);
        expect(updatePayload.status).toBe('pending');
    });

    test('402 si reserve_credit devuelve false (sin créditos para reintentar)', async () => {
        mockSupabase.from.mockImplementationOnce(() =>
            jobsFetchBuilder({ id: JOB_ID, user_id: USER_ID, status: 'completed' })
        );
        mockSupabase.rpc.mockResolvedValue({ data: false, error: null });

        const res = await request(app)
            .post(`/jobs/${JOB_ID}/retry`)
            .set('Authorization', 'Bearer valid-token');

        expect(res.status).toBe(402);
    });
});
