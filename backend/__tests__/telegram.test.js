/**
 * F1: backend/src/lib/telegram.js — notify().
 */
describe('telegram.notify', () => {
    const ORIGINAL_ENV = process.env;

    beforeEach(() => {
        jest.resetModules();
        process.env = { ...ORIGINAL_ENV };
        global.fetch = jest.fn();
    });

    afterAll(() => {
        process.env = ORIGINAL_ENV;
    });

    test('no-op silencioso sin TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID', async () => {
        delete process.env.TELEGRAM_BOT_TOKEN;
        delete process.env.TELEGRAM_CHAT_ID;
        const { notify } = require('../src/lib/telegram');

        const result = await notify('hola');

        expect(result).toEqual({ ok: false, skipped: true });
        expect(global.fetch).not.toHaveBeenCalled();
    });

    test('llama a la Bot API cuando están las dos variables', async () => {
        process.env.TELEGRAM_BOT_TOKEN = 'test-token';
        process.env.TELEGRAM_CHAT_ID = '12345';
        global.fetch.mockResolvedValue({ ok: true });
        const { notify } = require('../src/lib/telegram');

        const result = await notify('algo se rompió');

        expect(result).toEqual({ ok: true });
        expect(global.fetch).toHaveBeenCalledTimes(1);
        const [url, options] = global.fetch.mock.calls[0];
        expect(url).toBe('https://api.telegram.org/bottest-token/sendMessage');
        expect(JSON.parse(options.body)).toEqual({
            chat_id: '12345',
            text: 'algo se rompió',
            parse_mode: 'HTML',
        });
    });

    test('devuelve ok:false si la Bot API responde con error', async () => {
        process.env.TELEGRAM_BOT_TOKEN = 'test-token';
        process.env.TELEGRAM_CHAT_ID = '12345';
        global.fetch.mockResolvedValue({ ok: false, status: 401, text: async () => 'Unauthorized' });
        const { notify } = require('../src/lib/telegram');

        const result = await notify('x');

        expect(result).toEqual({ ok: false });
    });

    test('devuelve ok:false si fetch tira (red)', async () => {
        process.env.TELEGRAM_BOT_TOKEN = 'test-token';
        process.env.TELEGRAM_CHAT_ID = '12345';
        global.fetch.mockRejectedValue(new Error('network down'));
        const { notify } = require('../src/lib/telegram');

        const result = await notify('x');

        expect(result).toEqual({ ok: false });
    });
});
