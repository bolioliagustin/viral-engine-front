/**
 * Admin allowlist middleware — MVP sin rol is_admin en DB.
 * Configurar ADMIN_USER_IDS (UUIDs) y/o ADMIN_EMAILS en env.
 */
function parseList(envValue) {
    return (envValue || '')
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean);
}

function isAdminUser(user) {
    if (!user) return false;
    const ids = parseList(process.env.ADMIN_USER_IDS);
    const emails = parseList(process.env.ADMIN_EMAILS).map((e) => e.toLowerCase());
    if (ids.includes(user.id)) return true;
    const email = (user.email || '').toLowerCase();
    return email && emails.includes(email);
}

function requireAdmin(req, res, next) {
    if (!req.user) {
        return res.status(401).json({
            error: 'Authentication required',
            message: 'Missing or invalid session',
        });
    }
    if (!isAdminUser(req.user)) {
        return res.status(403).json({
            error: 'Forbidden',
            message: 'Admin access required',
        });
    }
    next();
}

module.exports = { requireAdmin, isAdminUser };
