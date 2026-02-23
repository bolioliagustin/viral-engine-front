/**
 * C1: JWT Authentication Middleware
 * Verifies Supabase JWT token from Authorization header.
 * Extracts user identity and attaches it to req.user.
 * Overrides req.body.userId with the verified user ID to prevent spoofing.
 */
const { supabase } = require('../lib/supabase');
const logger = require('../lib/logger');

async function requireAuth(req, res, next) {
    const authHeader = req.headers.authorization;

    if (!authHeader || !authHeader.startsWith('Bearer ')) {
        return res.status(401).json({
            error: 'Authentication required',
            message: 'Missing or invalid Authorization header. Expected: Bearer <token>'
        });
    }

    const token = authHeader.substring(7); // Remove 'Bearer '

    try {
        // Verify token using Supabase service client
        const { data: { user }, error } = await supabase.auth.getUser(token);

        if (error || !user) {
            return res.status(401).json({
                error: 'Invalid token',
                message: 'Your session has expired or is invalid. Please log in again.'
            });
        }

        // Attach verified user to request
        req.user = user;

        // CRITICAL: Override userId with verified identity (prevents spoofing)
        if (req.body) {
            req.body.userId = user.id;
        }

        next();
    } catch (err) {
        console.error('Auth middleware error:', err.message);
        return res.status(401).json({
            error: 'Authentication failed',
            message: 'Could not verify your identity. Please log in again.'
        });
    }
}

/**
 * Optional auth middleware - attaches user if token present, but doesn't block.
 * Useful for endpoints that work for both authenticated and anonymous users.
 */
async function optionalAuth(req, res, next) {
    const authHeader = req.headers.authorization;

    if (!authHeader || !authHeader.startsWith('Bearer ')) {
        return next();
    }

    const token = authHeader.substring(7);

    try {
        const { data: { user }, error } = await supabase.auth.getUser(token);
        if (!error && user) {
            req.user = user;
            if (req.body) {
                req.body.userId = user.id;
            }
        }
    } catch {
        // Silently continue without auth
    }

    next();
}

module.exports = { requireAuth, optionalAuth };
