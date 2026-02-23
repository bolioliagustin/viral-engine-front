/**
 * Server Entry Point
 * Imports the configured Express app and starts listening.
 */
const app = require('./app');

// Validate required environment variables
const requiredEnvVars = ['SUPABASE_URL', 'SUPABASE_SERVICE_KEY'];
const missingVars = requiredEnvVars.filter(varName => !process.env[varName]);
if (missingVars.length > 0) {
    console.error('❌ Missing required environment variables:', missingVars.join(', '));
    console.error('Please set these in your .env file');
    process.exit(1);
}

const PORT = process.env.PORT || 3000;

app.listen(PORT, () => {
    console.log(`🚀 Backend server running on http://localhost:${PORT}`);
    console.log(`📋 Endpoints:`);
    console.log(`   POST /process - Submit a YouTube URL for processing`);
    console.log(`   GET /status/:jobId - Check job status and results`);
    console.log(`   GET /jobs - List all jobs`);
    console.log(`   GET /health - Health check`);
});
