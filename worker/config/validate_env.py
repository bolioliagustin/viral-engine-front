"""
Validate required environment variables at startup
"""
import os
import sys

REQUIRED_ENV_VARS = [
    'SUPABASE_URL',
    'SUPABASE_SERVICE_KEY',
    'OPENROUTER_API_KEY',
    'OPENAI_API_KEY',  # Required for Whisper transcription
]

OPTIONAL_ENV_VARS = {
    'FFMPEG_PATH': 'Will use system PATH if not set',
    'OPENROUTER_MODEL': 'Defaults to google/gemini-2.0-flash-exp:free',
    'R2_ACCOUNT_ID': 'Required for clip uploads to Cloudflare R2',
    'R2_ACCESS_KEY_ID': 'Required for clip uploads to Cloudflare R2',
    'R2_SECRET_ACCESS_KEY': 'Required for clip uploads to Cloudflare R2',
    'R2_BUCKET_NAME': 'Required for clip uploads to Cloudflare R2',
    'R2_PUBLIC_URL': 'Public URL prefix for R2 bucket',
}

def validate_env():
    """Validate that all required environment variables are set"""
    missing_vars = []
    
    for var in REQUIRED_ENV_VARS:
        if not os.getenv(var):
            missing_vars.append(var)
    
    if missing_vars:
        print("❌ Missing required environment variables:")
        for var in missing_vars:
            print(f"   - {var}")
        print("\nPlease set these variables in your .env file")
        sys.exit(1)
    
    # Show optional vars status
    print("✅ All required environment variables are set")
    print("\n📋 Optional environment variables:")
    for var, description in OPTIONAL_ENV_VARS.items():
        status = "✓ Set" if os.getenv(var) else f"○ Not set ({description})"
        print(f"   {var}: {status}")
    print()

if __name__ == "__main__":
    validate_env()
