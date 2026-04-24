/**
 * Billing Routes — Lemon Squeezy
 *
 * POST /billing/create-checkout   → Redirect to LS Checkout
 * POST /billing/create-portal     → Redirect to LS Customer Portal
 * POST /billing/webhook           → Handle LS events
 *
 * Env vars required:
 *   LEMONSQUEEZY_API_KEY          — API key from LS dashboard
 *   LEMONSQUEEZY_STORE_ID         — numeric Store ID
 *   LEMONSQUEEZY_VARIANT_ID       — numeric Variant ID of the $9/mo plan
 *   LEMONSQUEEZY_WEBHOOK_SECRET   — signing secret set in LS webhooks page
 */

const express = require('express');
const crypto = require('crypto');
const { supabase } = require('../lib/supabase');
const { requireAuth } = require('../middleware/auth');
const logger = require('../lib/logger');

const router = express.Router();

const LS_API = 'https://api.lemonsqueezy.com/v1';

function lsHeaders() {
    return {
        Authorization: `Bearer ${process.env.LEMONSQUEEZY_API_KEY}`,
        Accept: 'application/vnd.api+json',
        'Content-Type': 'application/vnd.api+json',
    };
}

function frontendUrl() {
    return (process.env.FRONTEND_URL || 'http://localhost:3001').replace(/\/+$/, '');
}

// ---------------------------------------------------------------------------
// POST /billing/create-checkout
// ---------------------------------------------------------------------------
router.post('/billing/create-checkout', requireAuth, async (req, res) => {
    try {
        const userId = req.user.id;
        const userEmail = req.user.email;

        const storeId = process.env.LEMONSQUEEZY_STORE_ID;
        const variantId = process.env.LEMONSQUEEZY_VARIANT_ID;

        if (!storeId || !variantId) {
            return res.status(500).json({ error: 'LEMONSQUEEZY_STORE_ID / LEMONSQUEEZY_VARIANT_ID not configured' });
        }

        const body = {
            data: {
                type: 'checkouts',
                attributes: {
                    checkout_data: {
                        email: userEmail,
                        custom: { user_id: userId },
                    },
                    product_options: {
                        redirect_url: `${frontendUrl()}/dashboard?upgrade=success`,
                    },
                    checkout_options: {
                        dark: true,
                        logo: true,
                    },
                },
                relationships: {
                    store: { data: { type: 'stores', id: String(storeId) } },
                    variant: { data: { type: 'variants', id: String(variantId) } },
                },
            },
        };

        const response = await fetch(`${LS_API}/checkouts`, {
            method: 'POST',
            headers: lsHeaders(),
            body: JSON.stringify(body),
        });

        if (!response.ok) {
            const err = await response.text();
            logger.error('LS checkout API error', { status: response.status, body: err });
            return res.status(500).json({ error: 'Failed to create checkout' });
        }

        const data = await response.json();
        const checkoutUrl = data.data?.attributes?.url;

        if (!checkoutUrl) {
            return res.status(500).json({ error: 'No checkout URL returned from Lemon Squeezy' });
        }

        logger.info('LS checkout created', { userId });
        res.json({ url: checkoutUrl });

    } catch (error) {
        logger.error('LS create-checkout error', { error: error.message });
        res.status(500).json({ error: 'Internal server error' });
    }
});

// ---------------------------------------------------------------------------
// POST /billing/create-portal
// ---------------------------------------------------------------------------
router.post('/billing/create-portal', requireAuth, async (req, res) => {
    try {
        const userId = req.user.id;

        const { data: user } = await supabase
            .from('users')
            .select('billing_subscription_id')
            .eq('id', userId)
            .single();

        if (!user?.billing_subscription_id) {
            return res.status(400).json({
                error: 'No subscription found',
                message: 'Todavía no tenés una suscripción activa.',
            });
        }

        // Fetch fresh subscription to get the (time-limited) portal URL
        const response = await fetch(`${LS_API}/subscriptions/${user.billing_subscription_id}`, {
            headers: lsHeaders(),
        });

        if (!response.ok) {
            const err = await response.text();
            logger.error('LS get subscription error', { status: response.status, body: err });
            return res.status(500).json({ error: 'Failed to fetch subscription from Lemon Squeezy' });
        }

        const data = await response.json();
        const portalUrl = data.data?.attributes?.urls?.customer_portal;

        if (!portalUrl) {
            return res.status(500).json({ error: 'No portal URL returned from Lemon Squeezy' });
        }

        logger.info('LS portal URL retrieved', { userId });
        res.json({ url: portalUrl });

    } catch (error) {
        logger.error('LS create-portal error', { error: error.message });
        res.status(500).json({ error: 'Internal server error' });
    }
});

// ---------------------------------------------------------------------------
// POST /billing/webhook
// req.rawBody is set by the verify callback in express.json() (app.js)
// ---------------------------------------------------------------------------
router.post('/billing/webhook', async (req, res) => {
    const webhookSecret = process.env.LEMONSQUEEZY_WEBHOOK_SECRET;
    if (!webhookSecret) {
        logger.error('LEMONSQUEEZY_WEBHOOK_SECRET not configured');
        return res.status(500).json({ error: 'Webhook secret not configured' });
    }

    // Verify HMAC-SHA256 signature
    const signature = req.headers['x-signature'];
    if (!signature) {
        return res.status(400).json({ error: 'Missing x-signature header' });
    }

    const hmac = crypto.createHmac('sha256', webhookSecret);
    const digest = hmac.update(req.rawBody).digest('hex');

    try {
        if (!crypto.timingSafeEqual(Buffer.from(digest, 'utf8'), Buffer.from(signature, 'utf8'))) {
            logger.warn('LS webhook signature mismatch');
            return res.status(400).json({ error: 'Invalid signature' });
        }
    } catch {
        return res.status(400).json({ error: 'Signature comparison failed' });
    }

    let event;
    try {
        event = JSON.parse(req.rawBody.toString('utf8'));
    } catch {
        return res.status(400).json({ error: 'Invalid JSON payload' });
    }

    const eventName = event?.meta?.event_name;
    const customData = event?.meta?.custom_data;
    const subscriptionId = event?.data?.id;
    const attrs = event?.data?.attributes ?? {};

    logger.info(`LS webhook: ${eventName}`, { subscriptionId });

    try {
        // Resolve which user this event belongs to
        const userId = await resolveUserId(customData?.user_id, subscriptionId);

        if (!userId) {
            logger.warn('LS webhook: could not resolve user_id', { eventName, subscriptionId });
            return res.json({ received: true, warning: 'user not found' });
        }

        switch (eventName) {

            // ── New subscription ────────────────────────────────────────────
            case 'subscription_created': {
                await supabase.from('users').update({
                    plan: 'starter',
                    subscription_status: 'active',
                    billing_subscription_id: subscriptionId,
                    credits: 40,
                }).eq('id', userId);
                logger.info('Subscription activated — 40 credits granted', { userId });
                break;
            }

            // ── Monthly renewal ─────────────────────────────────────────────
            case 'subscription_payment_success': {
                // Don't reset on the very first payment (that's covered by subscription_created)
                const isRenewal = attrs.billing_anchor !== undefined && attrs.created_at !== attrs.updated_at;
                if (isRenewal) {
                    await supabase.from('users')
                        .update({ credits: 40 })
                        .eq('id', userId)
                        .eq('subscription_status', 'active');
                    logger.info('Credits reset to 40 for monthly renewal', { userId });
                }
                break;
            }

            // ── Subscription status changed ─────────────────────────────────
            case 'subscription_updated': {
                const status = attrs.status;
                if (status === 'active') {
                    await supabase.from('users').update({
                        plan: 'starter',
                        subscription_status: 'active',
                    }).eq('id', userId);
                } else if (['cancelled', 'expired', 'unpaid', 'paused'].includes(status)) {
                    await supabase.from('users').update({
                        plan: 'free',
                        subscription_status: status,
                    }).eq('id', userId);
                }
                break;
            }

            // ── Explicit cancellation ───────────────────────────────────────
            case 'subscription_cancelled':
            case 'subscription_expired': {
                await supabase.from('users').update({
                    plan: 'free',
                    subscription_status: 'canceled',
                    billing_subscription_id: null,
                }).eq('id', userId);
                logger.info('Subscription canceled — plan reverted to free', { userId });
                break;
            }

            default:
                // Unhandled event — no action needed
                break;
        }

        res.json({ received: true });

    } catch (error) {
        logger.error('Error processing LS webhook', { eventName, error: error.message });
        // Return 200 so LS doesn't retry endlessly — error is logged
        res.status(200).json({ received: true, warning: 'Processing error logged' });
    }
});

// ---------------------------------------------------------------------------
// Helper: resolve user ID from custom_data OR subscription ID lookup
// ---------------------------------------------------------------------------
async function resolveUserId(customUserId, subscriptionId) {
    if (customUserId) return customUserId;

    // Fallback: find by stored subscription ID
    if (subscriptionId) {
        const { data } = await supabase
            .from('users')
            .select('id')
            .eq('billing_subscription_id', subscriptionId)
            .single();
        return data?.id ?? null;
    }

    return null;
}

module.exports = router;
