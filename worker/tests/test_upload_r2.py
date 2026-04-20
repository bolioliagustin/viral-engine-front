"""
Sprint 1.1 — Dia 7: Test upload del clip final a R2

Sube el clip generado por el pipeline completo (Dia 6) a Cloudflare R2
y valida que la URL publica sea accesible.

Run desde worker/: python tests/test_upload_r2.py
"""
import sys
import os
import time
from pathlib import Path

# Cargar .env de la raiz del proyecto ANTES de importar modulos que usan env vars
ROOT_DIR = Path(__file__).parent.parent.parent
env_path = ROOT_DIR / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.clip_generator import probe_video
from services.supabase_client import upload_clip_to_storage

import requests


WORKER_DIR = Path(__file__).parent.parent
TEST_CLIP = WORKER_DIR / "clips" / "test_sprint_1_1_final" / "full_pipeline.mp4"

# Usamos un job_id sintetico para no ensuciar data real
TEST_JOB_ID = f"sprint11-test-{int(time.time())}"
TEST_MOMENT = 0


def run_tests():
    print("=" * 80)
    print("TEST: upload a R2 — Sprint 1.1 Dia 7")
    print("=" * 80)

    # Validar credenciales cargadas
    required = ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME", "R2_PUBLIC_URL"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"❌ Faltan variables de entorno: {missing}")
        print(f"   Verifica que {ROOT_DIR / '.env'} las contenga")
        return 1
    print(f"✅ Credenciales R2 cargadas desde .env")
    print(f"   Bucket: {os.getenv('R2_BUCKET_NAME')}")
    print(f"   Public URL base: {os.getenv('R2_PUBLIC_URL')[:60]}...")

    # Validar clip existe
    if not TEST_CLIP.exists():
        print(f"❌ Clip no existe: {TEST_CLIP}")
        print("   Ejecuta primero tests/test_generate_clip.py")
        return 1

    meta = probe_video(str(TEST_CLIP))
    print(f"\n📹 Clip a subir:")
    print(f"   {TEST_CLIP}")
    print(f"   {meta.width}x{meta.height} ({meta.aspect_ratio}), "
          f"{meta.duration_sec:.1f}s, {meta.size_mb:.1f}MB")

    # Subir
    print(f"\n🚀 Subiendo a R2...")
    print(f"   job_id: {TEST_JOB_ID}")
    print(f"   moment: {TEST_MOMENT}")
    t0 = time.time()
    try:
        public_url = upload_clip_to_storage(
            file_path=str(TEST_CLIP),
            job_id=TEST_JOB_ID,
            moment_index=TEST_MOMENT,
        )
        elapsed = time.time() - t0
        if not public_url:
            print(f"❌ Upload fallo (funcion devolvio None)")
            return 1
        print(f"✅ Subido en {elapsed:.1f}s")
        print(f"   URL: {public_url}")
    except Exception as e:
        print(f"❌ Upload fallo: {e}")
        import traceback
        traceback.print_exc()
        return 1

    # Verificar que la URL sea accesible via HTTP
    print(f"\n🔍 Verificando accesibilidad publica...")
    try:
        r = requests.head(public_url, timeout=15, allow_redirects=True)
        content_type = r.headers.get("content-type", "")
        content_length = r.headers.get("content-length", "0")
        print(f"   Status: {r.status_code}")
        print(f"   Content-Type: {content_type}")
        print(f"   Content-Length: {int(content_length) / 1024 / 1024:.1f}MB")

        if r.status_code != 200:
            print(f"⚠️ URL devuelve {r.status_code}, no 200")
            return 1
        if "video" not in content_type.lower() and "octet-stream" not in content_type.lower():
            print(f"⚠️ Content-Type sospechoso: {content_type}")
        print(f"✅ Clip accesible publicamente")
    except Exception as e:
        print(f"❌ No se pudo verificar URL: {e}")
        return 1

    # Resumen
    print("\n" + "=" * 80)
    print("SPRINT 1.1 — DIA 7 COMPLETO")
    print("=" * 80)
    print(f"\n✅ Pipeline local → Upload R2 funciona end-to-end")
    print(f"\nPodes abrir la URL en un navegador para verlo reproducir:")
    print(f"   {public_url}")
    print(f"\n🎯 Proximo: integrar generate_clip + upload al worker/main.py (Dia 8)")
    print(f"   y resolver descarga desde Render (cookies/proxy/RapidAPI)")

    return 0


if __name__ == "__main__":
    sys.exit(run_tests())
