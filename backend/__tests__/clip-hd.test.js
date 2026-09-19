/**
 * W9-A: POST /api/clips/:contentResultId/hd (docs/adr/0008)
 *
 * `routes/clip-edits.js` crea su propio cliente Supabase con
 * createClient(@supabase/supabase-js) en vez de reusar `src/lib/supabase`,
 * así que mockeamos el paquete completo: eso resuelve tanto el cliente del
 * router como el `supabase` que usa requireAuth (que sale del mismo
 * createClient() en src/lib/supabase.js), con un solo mock controlable.
 */
const request = require('supertest');

const mockSupabase = {
    from: jest.fn(),
    auth: {
        getUser: jest.fn(),
    },
};

jest.mock('@supabase/supabase-js', () => ({
    createClient: jest.fn(() => mockSupabase),
}));

process.env.SUPABASE_URL = process.env.SUPABASE_URL || 'https://test.supabase.co';
process.env.SUPABASE_SERVICE_KEY = process.env.SUPABASE_SERVICE_KEY || 'test-service-key';

const app = require('../src/app');

/** Query builder chainable y awaitable: soporta both .single() y await directo. */
function makeBuilder({ single, list } = {}) {
    const builder = {};
    const chain = () => builder;
    builder.select = jest.fn(chain);
    builder.eq = jest.fn(chain);
    builder.in = jest.fn(chain);
    builder.order = jest.fn(chain);
    builder.limit = jest.fn(chain);
    builder.insert = jest.fn(chain);
    builder.single = jest.fn(() => Promise.resolve(single ?? { data: null, error: null }));
    builder.then = (resolve, reject) =>
        Promise.resolve(list ?? { data: null, error: null }).then(resolve, reject);
    return builder;
}

const OWNER_ID = 'user-owner-1';
const OTHER_ID = 'user-other-2';
const CONTENT_RESULT_ID = 'cr-abc123';

function mockAuthenticatedUser(userId = OWNER_ID) {
    mockSupabase.auth.getUser.mockResolvedValue({
        data: { user: { id: userId, email: `${userId}@test.com` } },
        error: null,
    });
}

function ownershipBuilder(ownerUserId = OWNER_ID) {
    return makeBuilder({
        single: {
            data: { id: CONTENT_RESULT_ID, jobs: { user_id: ownerUserId } },
            error: null,
        },
    });
}

beforeEach(() => {
    mockSupabase.from.mockReset();
    mockSupabase.auth.getUser.mockReset();
});

describe('POST /api/clips/:id/hd — auth', () => {
    test('401 sin token', async () => {
        const res = await request(app).post(`/api/clips/${CONTENT_RESULT_ID}/hd`);
        expect(res.status).toBe(401);
    });

    test('403 si el clip no es del usuario autenticado', async () => {
        mockAuthenticatedUser(OWNER_ID);
        mockSupabase.from.mockImplementationOnce(() => ownershipBuilder(OTHER_ID));

        const res = await request(app)
            .post(`/api/clips/${CONTENT_RESULT_ID}/hd`)
            .set('Authorization', 'Bearer valid-token');

        expect(res.status).toBe(403);
    });

    test('404 si el content_result no existe', async () => {
        mockAuthenticatedUser(OWNER_ID);
        mockSupabase.from.mockImplementationOnce(() =>
            makeBuilder({ single: { data: null, error: { message: 'not found' } } })
        );

        const res = await request(app)
            .post(`/api/clips/${CONTENT_RESULT_ID}/hd`)
            .set('Authorization', 'Bearer valid-token');

        expect(res.status).toBe(404);
    });
});

describe('POST /api/clips/:id/hd — encolado y estado', () => {
    test('202 y encola un clip_edits nuevo la primera vez', async () => {
        mockAuthenticatedUser(OWNER_ID);
        mockSupabase.from
            .mockImplementationOnce(() => ownershipBuilder()) // verifyClipOwnership
            .mockImplementationOnce(() => makeBuilder({ list: { data: [], error: null } })) // sin hd_upgrade previo
            .mockImplementationOnce(() =>
                makeBuilder({ single: { data: { viral_overlay: 'ASÍ SE CONTAGIA' }, error: null } })
            ) // content_results.viral_overlay
            .mockImplementationOnce(() =>
                makeBuilder({
                    single: {
                        data: { id: 'edit-new-1', content_result_id: CONTENT_RESULT_ID, status: 'queued' },
                        error: null,
                    },
                })
            ); // insert clip_edits

        const res = await request(app)
            .post(`/api/clips/${CONTENT_RESULT_ID}/hd`)
            .set('Authorization', 'Bearer valid-token');

        expect(res.status).toBe(202);
        expect(res.body.hd_status).toBe('queued');
        expect(res.body.clip_edit_id).toBe('edit-new-1');
    });

    test('202 sin encolar de nuevo si ya hay un hd_upgrade queued/processing', async () => {
        mockAuthenticatedUser(OWNER_ID);
        mockSupabase.from
            .mockImplementationOnce(() => ownershipBuilder())
            .mockImplementationOnce(() =>
                makeBuilder({
                    list: { data: [{ status: 'processing', rendered_clip_url: null }], error: null },
                })
            );

        const res = await request(app)
            .post(`/api/clips/${CONTENT_RESULT_ID}/hd`)
            .set('Authorization', 'Bearer valid-token');

        expect(res.status).toBe(202);
        expect(res.body.hd_status).toBe('processing');
        // No debe haber llamado a .from() una tercera vez (ni content_results
        // para overlay, ni insert): solo ownership + el chequeo de existing.
        expect(mockSupabase.from).toHaveBeenCalledTimes(2);
    });

    test('200 con la URL si el HD ya está listo', async () => {
        mockAuthenticatedUser(OWNER_ID);
        mockSupabase.from
            .mockImplementationOnce(() => ownershipBuilder())
            .mockImplementationOnce(() =>
                makeBuilder({
                    list: {
                        data: [{ status: 'completed', rendered_clip_url: 'https://r2.example/hd.mp4' }],
                        error: null,
                    },
                })
            );

        const res = await request(app)
            .post(`/api/clips/${CONTENT_RESULT_ID}/hd`)
            .set('Authorization', 'Bearer valid-token');

        expect(res.status).toBe(200);
        expect(res.body.hd_url).toBe('https://r2.example/hd.mp4');
        expect(res.body.hd_status).toBe('ready');
    });
});
