/**
 * Express App Configuration
 * Separated from index.js so tests can import the app without starting the server.
 */
require('dotenv').config({ path: require('path').resolve(__dirname, '../../.env') });

const Sentry = require('@sentry/node');
const express = require('express');
const cors = require('cors');
const helmet = require('helmet');
const compression = require('compression');
const jobsRouter = require('./routes/jobs');
const billingRouter = require('./routes/billing');
const clipEditsRouter = require('./routes/clip-edits');
const logger = require('./lib/logger');

// C4: Initialize Sentry error tracking (must be before Express app creation)
Sentry.init({
    dsn: process.env.SENTRY_DSN_BACKEND || "https://6bd5c4e61759dd1d1e269e0d999d56df@o4510909878632448.ingest.us.sentry.io/4510909927063552",
    sendDefaultPii: true,
    environment: process.env.NODE_ENV || 'development',
    tracesSampleRate: 0.2, // Sample 20% of transactions for performance monitoring
});

const app = express();

// Middleware
app.use(helmet());       // Q1: Security headers
app.use(compression());  // Q2: Gzip response compression

// Configure CORS
app.use(cors({
    origin: function (origin, callback) {
        const frontendUrl = (process.env.FRONTEND_URL || '').replace(/\/+$/, '');
        const allowedOrigins = [
            'http://localhost:3001',
            'http://localhost:3000',
            frontendUrl
        ].filter(Boolean);

        // Allow requests with no origin (server-to-server, health checks)
        if (!origin) return callback(null, true);

        // Allow exact match or any *.vercel.app origin (preview deploys)
        if (allowedOrigins.indexOf(origin) !== -1 || origin.endsWith('.vercel.app')) {
            callback(null, true);
        } else {
            logger.warn('CORS blocked origin', { origin, allowedOrigins });
            callback(new Error('Not allowed by CORS'));
        }
    },
    credentials: true
}));
// Save raw body buffer so Stripe webhook can verify signatures
app.use(express.json({
    verify: (req, _res, buf) => {
        req.rawBody = buf;
    },
}));

// Routes
app.use('/', jobsRouter);
app.use('/', billingRouter);
app.use('/', clipEditsRouter);

// Root route
app.get('/', (req, res) => {
    res.json({
        name: 'YouTube Viral Content Engine',
        version: '1.0.0',
        endpoints: {
            'POST /process': 'Submit YouTube URL for processing',
            'GET /status/:jobId': 'Check job status and results',
            'GET /jobs': 'List all jobs',
            'GET /health': 'Health check'
        }
    });
});

// C3: Advanced health check (readiness — includes DB/queue)
app.get('/health', async (req, res) => {
    const checks = {
        api: { status: 'ok' },
        database: { status: 'unknown' },
        queue: { status: 'unknown' },
        timestamp: new Date().toISOString(),
        uptime: process.uptime()
    };

    try {
        if (process.env.SUPABASE_URL) {
            const { createClient } = require('@supabase/supabase-js');
            const supabase = createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_KEY);
            const { error } = await supabase.from('jobs').select('id').limit(1);
            checks.database = error ? { status: 'error', message: error.message } : { status: 'ok' };
        } else {
            checks.database = { status: 'not_configured' };
        }
    } catch (e) {
        checks.database = { status: 'error', message: e.message };
    }

    // S1: Check queue via Supabase (pending jobs count)
    try {
        if (process.env.SUPABASE_URL) {
            const { createClient } = require('@supabase/supabase-js');
            const sb = createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_KEY);
            const { data, error } = await sb
                .from('jobs')
                .select('id')
                .eq('status', 'pending');

            if (error) {
                checks.queue = { status: 'error', message: error.message };
            } else {
                checks.queue = { status: 'ok', pendingJobs: data?.length || 0 };
            }
        } else {
            checks.queue = { status: 'not_configured' };
        }
    } catch (e) {
        checks.queue = { status: 'error', message: e.message };
    }

    const isHealthy = checks.database.status !== 'error' && checks.queue.status !== 'error';
    res.status(isHealthy ? 200 : 503).json(checks);
});

// Liveness probe for Docker — returns 200 if the API process is up.
// Use this for container healthchecks; /health is for full readiness diagnostics.
app.get('/health/live', (req, res) => {
    res.status(200).json({
        status: 'ok',
        timestamp: new Date().toISOString(),
        uptime: process.uptime(),
    });
});

// C4: Sentry error handler (must be before generic error handler)
Sentry.setupExpressErrorHandler(app);

// Error handling
app.use((err, req, res, next) => {
    logger.error('Unhandled error', { error: err.message, stack: err.stack, path: req.path, method: req.method });
    res.status(500).json({ error: 'Internal server error' });
});

// Agregá esto en app.js (temporal, para probar)
app.get('/debug-sentry', (req, res) => {
    throw new Error('Sentry test error!');
});

module.exports = app;
