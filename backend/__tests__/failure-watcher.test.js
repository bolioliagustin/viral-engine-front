/**
 * F1: backend/src/lib/failure-watcher.js — checkFailedJobs().
 */
const mockSupabase = {
    from: jest.fn(),
};
jest.mock('../src/lib/supabase', () => ({ supabase: mockSupabase }));

const mockNotify = jest.fn();
jest.mock('../src/lib/telegram', () => ({ notify: (...args) => mockNotify(...args) }));

const { checkFailedJobs } = require('../src/lib/failure-watcher');

function selectBuilder(jobs) {
    const builder = {};
    builder.select = jest.fn(() => builder);
    builder.eq = jest.fn(() => builder);
    builder.limit = jest.fn(() => Promise.resolve({ data: jobs, error: null }));
    return builder;
}

function updateBuilder(onUpdate) {
    return {
        update: jest.fn((payload) => ({
            eq: jest.fn((_column, value) => {
                onUpdate(payload, value);
                return Promise.resolve({ error: null });
            }),
        })),
    };
}

beforeEach(() => {
    mockSupabase.from.mockReset();
    mockNotify.mockReset();
    mockNotify.mockResolvedValue({ ok: true });
});

describe('checkFailedJobs', () => {
    test('sin jobs failed pendientes de alerta, no llama a notify', async () => {
        mockSupabase.from.mockImplementationOnce(() => selectBuilder([]));

        await checkFailedJobs();

        expect(mockNotify).not.toHaveBeenCalled();
    });

    test('notifica cada job failed y lo marca failure_alert_sent', async () => {
        const updates = [];
        mockSupabase.from
            .mockImplementationOnce(() =>
                selectBuilder([
                    { id: 'job-1', user_id: 'user-1', video_url: 'https://youtu.be/a', error_message: 'boom' },
                ])
            )
            .mockImplementationOnce(() => updateBuilder((payload, id) => updates.push({ payload, id })));

        await checkFailedJobs();

        expect(mockNotify).toHaveBeenCalledTimes(1);
        expect(mockNotify.mock.calls[0][0]).toMatch(/job-1/);
        expect(mockNotify.mock.calls[0][0]).toMatch(/boom/);
        expect(updates).toEqual([{ payload: { failure_alert_sent: true }, id: 'job-1' }]);
    });

    test('marca failure_alert_sent aunque notify() falle (best-effort, no reintenta en loop)', async () => {
        mockNotify.mockResolvedValue({ ok: false });
        const updates = [];
        mockSupabase.from
            .mockImplementationOnce(() =>
                selectBuilder([{ id: 'job-2', user_id: null, video_url: 'https://youtu.be/b', error_message: null }])
            )
            .mockImplementationOnce(() => updateBuilder((payload, id) => updates.push({ payload, id })));

        await checkFailedJobs();

        expect(updates).toEqual([{ payload: { failure_alert_sent: true }, id: 'job-2' }]);
    });
});
