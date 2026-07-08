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
    'MODEL_ANALYSIS': 'Modelo para selección de momentos (default google/gemini-2.5-pro)',
    'MODEL_COPY_WRITING': 'Modelo para copy/threads (alias preferido de MODEL_COPY)',
    'MODEL_COPY': 'Modelo para copy/threads (default google/gemini-2.5-flash)',
    'MODEL_JUDGE': 'Modelo juez de scoring (default google/gemini-2.5-flash-lite)',
    'MODEL_CLASSIFIER': 'Modelo clasificador podcast/business (default google/gemini-2.0-flash-001)',
    'OPENROUTER_MODEL': 'LEGACY — usar MODEL_ANALYSIS. Fallback si MODEL_ANALYSIS no está',
    'GROQ_API_KEY': 'Preferred Whisper provider (faster + better than OpenAI). Get at console.groq.com. Si no está, cae a OpenAI.',
    'SUPADATA_API_KEY': 'REQUIRED on cloud deployments (Render/Railway) — get free key at supadata.ai',
    'R2_ACCOUNT_ID': 'Required for clip uploads to Cloudflare R2',
    'R2_ACCESS_KEY_ID': 'Required for clip uploads to Cloudflare R2',
    'R2_SECRET_ACCESS_KEY': 'Required for clip uploads to Cloudflare R2',
    'R2_BUCKET_NAME': 'Required for clip uploads to Cloudflare R2',
    'R2_PUBLIC_URL': 'Public URL prefix for R2 bucket',
    'USE_RAPIDAPI_DOWNLOAD': 'Set true on VPS for RapidAPI stream URLs (recommended with WEBSHARE_PROXY_FILE)',
    'RAPIDAPI_KEY': 'Required when USE_RAPIDAPI_DOWNLOAD=true',
    'WEBSHARE_PROXY_FILE': 'Residential proxies for YouTube downloads on datacenter VPS',
    'WEBSHARE_PROXY_LIST': 'Comma/newline-separated proxy URLs (alternative to WEBSHARE_PROXY_FILE)',
    'WEBSHARE_PROXY_URL': 'Single proxy URL (legacy)',
    'YOUTUBE_COOKIES': 'Base64 de cookies.txt (Netscape) para yt-dlp anti-bot',
    'MAX_WORKERS': 'Parallel jobs (default 2). Use 2 on 12GB RAM VPS.',
}

PRODUCTION_RECOMMENDED_VARS = [
    'GROQ_API_KEY',
    'SUPADATA_API_KEY',
    'R2_ACCOUNT_ID',
    'R2_ACCESS_KEY_ID',
    'R2_SECRET_ACCESS_KEY',
    'R2_BUCKET_NAME',
    'R2_PUBLIC_URL',
    'USE_RAPIDAPI_DOWNLOAD',
    'RAPIDAPI_KEY',
    'WEBSHARE_PROXY_FILE',
    'YOUTUBE_COOKIES',
]

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

    # Fase 1: warning si algún modelo es free tier + mostrar resolución efectiva
    try:
        from config.model_tiers import get_model, is_free_tier, resolved_models
        print("\n📦 Modelos LLM resueltos:")
        for task, model in resolved_models().items():
            print(f"   {task}: {model}")
        for task in ("analysis", "copy", "judge", "classifier"):
            model = get_model(task)
            if is_free_tier(model):
                print(
                    f"\n⚠️  MODEL_{task.upper()} = '{model}' es FREE TIER de OpenRouter.\n"
                    f"   Rate limits agresivos + peor calidad de análisis.\n"
                    f"   Recomendado: setear MODEL_{task.upper()} a un modelo pago "
                    f"(ej. google/gemini-2.5-{'pro' if task == 'analysis' else 'flash'}).\n"
                )
    except ImportError:
        pass

    env = os.getenv("ENVIRONMENT", "development").lower()
    if env in ("production", "prod"):
        if not os.getenv("RAPIDAPI_KEY"):
            print("\n❌ RAPIDAPI_KEY es OBLIGATORIA en producción (yt-dlp falla con DRM).")
            print("   Agregala en ~/viralengine/.env en el VPS.\n")
            sys.exit(1)
        missing_prod = [v for v in PRODUCTION_RECOMMENDED_VARS if not os.getenv(v)]
        use_rapidapi = os.getenv("USE_RAPIDAPI_DOWNLOAD", "").lower() in ("1", "true", "yes")
        if use_rapidapi and not os.getenv("RAPIDAPI_KEY"):
            if "RAPIDAPI_KEY" not in missing_prod:
                missing_prod.append("RAPIDAPI_KEY")
        has_proxy = any(os.getenv(k) for k in (
            "WEBSHARE_PROXY_FILE", "WEBSHARE_PROXY_LIST", "WEBSHARE_PROXY_URL"
        ))
        if not has_proxy and "WEBSHARE_PROXY_FILE" not in missing_prod:
            missing_prod.append("WEBSHARE_PROXY_FILE (o WEBSHARE_PROXY_LIST/URL)")
        if missing_prod:
            print("\n⚠️  PRODUCTION: variables recomendadas no configuradas:")
            for var in missing_prod:
                print(f"   - {var}")
            print("   El worker puede fallar en transcripts o descargas de YouTube.\n")
        else:
            print("\n✅ Variables de producción recomendadas: OK\n")
    else:
        print()

if __name__ == "__main__":
    validate_env()
