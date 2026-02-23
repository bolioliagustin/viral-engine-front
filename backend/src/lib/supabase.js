const { createClient } = require('@supabase/supabase-js');

const supabaseUrl = process.env.SUPABASE_URL;
const supabaseServiceKey = process.env.SUPABASE_SERVICE_KEY;

if (!supabaseUrl || !supabaseServiceKey) {
    console.warn('⚠️ Supabase credentials not found. Using SQLite fallback.');
}

// Service client (bypasses RLS for backend operations)
const supabase = createClient(supabaseUrl || '', supabaseServiceKey || '', {
    auth: {
        autoRefreshToken: false,
        persistSession: false
    }
});

module.exports = { supabase };
