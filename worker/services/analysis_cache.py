"""
Analysis Cache — cachea el output del análisis Gemini.

Cuando un video se reprocesa con la misma configuración (model + tone +
prompt_version), devolvemos el AnalysisResult guardado en lugar de llamar
al modelo de nuevo. Ahorra ~30-60s + el costo de la API por cache hit.

Invalidación: si cambiás los prompts del worker, bumpeá `PROMPT_VERSION`
de abajo. Eso fuerza re-análisis de todos los videos (las filas viejas
quedan pero no van a matchear el nuevo prompt_version).
"""
import json
from typing import Optional

from services.supabase_client import get_supabase

# ⚠️ Bumpear cuando cambien los prompts (get_dynamic_prompt, schema, etc).
# Forzá invalidación global del cache. Ejemplo de bump: "v1" → "v2".
# v2: pipeline two-pass (pasada A selección sin copy) + model tiers (Fase 1-2)
# v3: clip quality pipeline (sentence snap, hook anchor, whisper vocabulary)
PROMPT_VERSION = "v3"


# ─── Analysis cache (resultado completo del análisis) ───────────────────────
def get_cached_analysis(
    video_id: str,
    model: str,
    tone: str = "profesional",
) -> Optional[dict]:
    """
    Busca un AnalysisResult cacheado. Retorna el dict crudo o None.
    """
    supabase = get_supabase()
    if not supabase:
        return None

    try:
        res = (
            supabase.table("analysis_cache")
            .select("result, category_detected")
            .eq("video_id", video_id)
            .eq("model", model)
            .eq("tone", tone)
            .eq("prompt_version", PROMPT_VERSION)
            .limit(1)
            .execute()
        )
        if not res.data:
            return None

        row = res.data[0]
        result = row["result"]
        if isinstance(result, str):
            result = json.loads(result)
        print(f"   ✅ Cache hit: análisis ya existe para {video_id} (skip Gemini)")
        return result
    except Exception as e:
        print(f"   ⚠️ analysis_cache lookup falló (no fatal): {e}")
        return None


def save_analysis(
    video_id: str,
    model: str,
    result: dict,
    tone: str = "profesional",
    category_detected: Optional[str] = None,
    prompt_chars: Optional[int] = None,
) -> bool:
    """
    Guarda un AnalysisResult al cache. Upsert por la unique key
    (video_id, model, tone, prompt_version).
    """
    supabase = get_supabase()
    if not supabase:
        return False

    try:
        payload = {
            "video_id": video_id,
            "model": model,
            "tone": tone,
            "prompt_version": PROMPT_VERSION,
            "result": json.dumps(result, ensure_ascii=False, default=str),
            "category_detected": category_detected,
            "prompt_chars": prompt_chars,
        }
        # Upsert: si ya existe, actualiza
        supabase.table("analysis_cache").upsert(
            payload,
            on_conflict="video_id,model,tone,prompt_version",
        ).execute()
        return True
    except Exception as e:
        print(f"   ⚠️ analysis_cache save falló (no fatal): {e}")
        return False


# ─── Category cache (más simple, solo string por video+model) ───────────────
def get_cached_category(video_id: str, model: str) -> Optional[str]:
    supabase = get_supabase()
    if not supabase:
        return None

    try:
        res = (
            supabase.table("category_cache")
            .select("category")
            .eq("video_id", video_id)
            .eq("model", model)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]["category"]
    except Exception as e:
        print(f"   ⚠️ category_cache lookup falló: {e}")
    return None


def save_category(video_id: str, model: str, category: str) -> bool:
    supabase = get_supabase()
    if not supabase:
        return False
    try:
        supabase.table("category_cache").upsert(
            {"video_id": video_id, "model": model, "category": category},
            on_conflict="video_id,model",
        ).execute()
        return True
    except Exception as e:
        print(f"   ⚠️ category_cache save falló: {e}")
        return False
