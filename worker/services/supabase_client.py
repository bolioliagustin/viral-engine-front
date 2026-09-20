"""
Supabase client for Python worker
"""
import os
import time
import threading
from typing import Optional
from supabase import create_client, Client

_supabase: Optional[Client] = None
_last_connect_attempt: float = 0.0
_RECONNECT_COOLDOWN = 30.0  # seconds between reconnect attempts
_reconnect_lock = threading.Lock()


# ─── Modo dry-run (EVAL_DRY_RUN=1) ──────────────────────────────────────────
# Lo usa el tier `e2e` del golden set (worker/eval): el pipeline corre igual
# (descarga, Whisper, Pasada B, juez, render) pero nada se persiste. Las
# escrituras a `jobs` y `content_results` se acumulan en memoria para que el
# eval las lea; las subidas a R2 devuelven una URL ficticia; el crédito no se
# descuenta. Las lecturas (caches) siguen funcionando normal.
# Sin la variable, el comportamiento es idéntico al de siempre.
DRY_RUN_RESULTS: list[dict] = []          # filas que save_content_result habría insertado
DRY_RUN_JOBS: dict[str, dict] = {}        # job_id → último estado/progreso/error del job


def is_dry_run() -> bool:
    return os.getenv("EVAL_DRY_RUN", "").strip().lower() in ("1", "true", "yes")


def reset_dry_run() -> None:
    """Vacía los acumuladores en memoria (llamar antes de cada job del eval)."""
    DRY_RUN_RESULTS.clear()
    DRY_RUN_JOBS.clear()


def _dry_run_job(job_id: str) -> dict:
    return DRY_RUN_JOBS.setdefault(job_id, {"status": None, "error_message": None,
                                            "current_step": None, "progress_percentage": None,
                                            "video_title": None})


def reset_supabase() -> None:
    """
    Mark the current client as broken so the next get_supabase() call
    triggers a reconnect. Call this when a Supabase query raises an
    unexpected exception (e.g. 'Server disconnected').
    """
    global _supabase
    _supabase = None


def start_keepalive(interval: int = 45) -> None:
    """
    Lanza un thread daemon que pinga Supabase cada `interval` segundos.
    Previene que PostgreSQL cierre la conexión idle (suele ocurrir después
    de ~2 min sin actividad, justo durante análisis largos con Gemini).
    """
    def _ping_loop():
        while True:
            time.sleep(interval)
            try:
                sb = get_supabase()
                if sb:
                    sb.table("jobs").select("id").limit(1).execute()
            except Exception as e:
                print(f"⚠️ Keepalive ping falló ({e}) — reseteando conexión")
                reset_supabase()

    t = threading.Thread(target=_ping_loop, daemon=True, name="supabase-keepalive")
    t.start()
    print(f"💓 Supabase keepalive activo (ping cada {interval}s)")


def get_supabase() -> Optional[Client]:
    """
    Get Supabase client singleton with thread-safe automatic reconnection.

    Fast path: returns the existing client immediately (no ping overhead).
    Slow path: acquires a lock, double-checks, then reconnects. The lock
    prevents multiple threads from hammering Supabase simultaneously when
    the connection drops mid-job.
    """
    global _supabase, _last_connect_attempt

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")

    if not url or not key:
        print("⚠️ SUPABASE_URL / SUPABASE_SERVICE_KEY no configuradas")
        return None

    # Fast path — client is healthy, return immediately
    if _supabase is not None:
        return _supabase

    # Slow path — reconnect, serialized by lock
    with _reconnect_lock:
        # Double-check: another thread may have reconnected while we waited
        if _supabase is not None:
            return _supabase

        now = time.time()
        if now - _last_connect_attempt < _RECONNECT_COOLDOWN:
            remaining = _RECONNECT_COOLDOWN - (now - _last_connect_attempt)
            print(f"⏳ Cooldown activo — esperando {remaining:.1f}s para reconectar...")
            time.sleep(remaining + 0.5)

        _last_connect_attempt = time.time()

        try:
            print(f"🔌 Connecting to Supabase: {url}")
            _supabase = create_client(url, key)
            print("✅ Supabase client created successfully")
            return _supabase
        except Exception as e:
            print(f"❌ Failed to create Supabase client: {e}")
            _supabase = None
            return None


def _require_supabase() -> Client:
    """Cliente Supabase o error explícito (validate_env garantiza las credenciales)."""
    supabase = get_supabase()
    if not supabase:
        raise RuntimeError("Supabase no disponible (credenciales faltantes o conexión caída)")
    return supabase


def update_job_status(job_id: str, status: str, video_title: str = None) -> None:
    """Update job status in Supabase"""
    if is_dry_run():
        job = _dry_run_job(job_id)
        job["status"] = status
        if video_title:
            job["video_title"] = video_title
        return
    supabase = _require_supabase()
    update_data = {"status": status}
    if video_title:
        update_data["video_title"] = video_title
    supabase.table("jobs").update(update_data).eq("id", job_id).execute()


def update_job_error(job_id: str, error_message: str) -> None:
    """Mark job as failed"""
    if is_dry_run():
        job = _dry_run_job(job_id)
        job["status"] = "failed"
        job["error_message"] = error_message
        return
    supabase = _require_supabase()
    supabase.table("jobs").update({
        "status": "failed",
        "error_message": error_message
    }).eq("id", job_id).execute()


def update_job_progress(
    job_id: str,
    current_step: str = None,
    progress_percentage: int = None,
    progress_detail: dict = None,
) -> None:
    """Update job processing step and progress for real-time UI updates

    Args:
        job_id: The job ID to update
        current_step: Fase del pipeline. Enum acordado con P1 (pantalla de
            progreso, docs/PLAN_CALIDAD.md §9 W9-B): 'transcribing' |
            'classifying' | 'analyzing' | 'evaluating' | 'ranking' |
            'delivering' | 'finalizing'.
        progress_percentage: Progreso 0-100 (ver main.py::compute_progress_percentage,
            función pura y testeada que calcula este número).
        progress_detail: W9-B — jsonb con detalle fino para la pantalla de
            progreso: {"current": N, "total": M, "message": "...",
            "clips_ready": K}. La columna la agrega P1 en su migración;
            si todavía no existe, degrada con gracia (mismo patrón que
            save_content_result con las columnas de calidad).
    """
    if is_dry_run():
        job = _dry_run_job(job_id)
        if current_step is not None:
            job["current_step"] = current_step
        if progress_percentage is not None:
            job["progress_percentage"] = progress_percentage
        if progress_detail is not None:
            job["progress_detail"] = progress_detail
        return
    import json
    supabase = _require_supabase()
    update_data = {}
    if current_step is not None:
        update_data["current_step"] = current_step
    if progress_percentage is not None:
        update_data["progress_percentage"] = progress_percentage
    if progress_detail is not None:
        update_data["progress_detail"] = json.dumps(progress_detail)
    if not update_data:
        return
    try:
        supabase.table("jobs").update(update_data).eq("id", job_id).execute()
    except Exception as e:
        if progress_detail is not None and ("column" in str(e).lower() or "pgrst204" in str(e).lower()):
            update_data.pop("progress_detail", None)
            if update_data:
                supabase.table("jobs").update(update_data).eq("id", job_id).execute()
        else:
            raise


def _is_connection_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(k in msg for k in ("server disconnected", "connection", "broken pipe", "eof"))


def save_content_result(
    job_id: str,
    content_type: str,
    content: str,
    clip_url: str = None,
    start_time: int = None,
    end_time: int = None,
    hook: str = None,
    emotional_trigger: str = None,
    moment_index: int = None,
    pillar_type: str = None,
    score_hook: int = None,
    score_retention: int = None,
    score_shareability: int = None,
    sentiment_detected: str = None,  # Phase B
    roi_time_saved: int = None,  # Phase B
    score_justifications: list = None,  # Phase B
    viral_overlay: str = None,  # TikTok burn-in title (hook corto UPPERCASE)
    raw_clip_url: str = None,  # Plan C: cache del segmento crudo en R2
    whisper_words: dict = None,  # Plan C: {"words": [...], "segments": [...]}
    score_llm: dict = None,  # Fase 4: scores autoevaluados del análisis
    score_judge: dict = None,  # Fase 4: scores del juez independiente
    verification_failed: bool = None,  # Fase 4: first Y last phrase fallaron
    sub_coverage: float = None,  # Fase 4: cobertura de subs 0-1
    words_per_sec: float = None,  # Fase 4: densidad de palabras del clip
    clip_quality_issues: list = None,  # flags: incomplete_tail, clip_not_rendered, etc.
    clip_generation_error: str = None,  # error si el MP4 no se generó
    title: str = None,  # W10: título ≤60 chars para publicar
    description: str = None,  # W10: 2 oraciones (qué se ve + invitación)
    hashtags: list = None,  # W10: 10 hashtags, español, CamelCase, con '#'
    preview_url: str = None,  # W9-B: mismo archivo que clip_url (preview 480x854)
) -> str:
    """Save content result to Supabase"""
    import uuid
    import json
    result_id = str(uuid.uuid4())

    if is_dry_run():
        # Misma fila que iría a content_results, pero con los valores sin
        # serializar (el eval los lee como dicts).
        DRY_RUN_RESULTS.append({
            "id": result_id,
            "job_id": job_id,
            "type": content_type,
            "content": content,
            "clip_url": clip_url,
            "start_time": start_time,
            "end_time": end_time,
            "hook": hook,
            "emotional_trigger": emotional_trigger,
            "moment_index": moment_index,
            "pillar_type": pillar_type,
            "score_hook": score_hook,
            "score_retention": score_retention,
            "score_shareability": score_shareability,
            "sentiment_detected": sentiment_detected,
            "roi_time_saved": roi_time_saved,
            "score_justifications": score_justifications,
            "viral_overlay": viral_overlay,
            "raw_clip_url": raw_clip_url,
            "whisper_words": whisper_words,
            "score_llm": score_llm,
            "score_judge": score_judge,
            "verification_failed": verification_failed,
            "sub_coverage": sub_coverage,
            "words_per_sec": words_per_sec,
            "clip_quality_issues": clip_quality_issues,
            "clip_generation_error": clip_generation_error,
            "title": title,
            "description": description,
            "hashtags": hashtags,
            "preview_url": preview_url,
        })
        return result_id

    supabase = _require_supabase()
    data = {
        "id": result_id,
        "job_id": job_id,
        "type": content_type,
        "content": content,
        "clip_url": clip_url,
        "start_time": start_time,
        "end_time": end_time,
        "hook": hook,
        "emotional_trigger": emotional_trigger,
        "moment_index": moment_index,
    }
    # Add new metrics columns if they exist
    if pillar_type:
        data["pillar_type"] = pillar_type
    if score_hook:
        data["score_hook"] = score_hook
    if score_retention:
        data["score_retention"] = score_retention
    if score_shareability:
        data["score_shareability"] = score_shareability
    # Phase B fields
    if sentiment_detected:
        data["sentiment_detected"] = sentiment_detected
    if roi_time_saved:
        data["roi_time_saved"] = roi_time_saved
    if score_justifications:
        data["score_justifications"] = json.dumps(score_justifications)
    if viral_overlay:
        data["viral_overlay"] = viral_overlay
    # Plan C: cache para acelerar re-renders
    if raw_clip_url:
        data["raw_clip_url"] = raw_clip_url
    if whisper_words:
        data["whisper_words"] = json.dumps(whisper_words) if not isinstance(whisper_words, str) else whisper_words
    # Fase 4: scoring calibrado + métricas de calidad.
    # Estos campos requieren supabase_migration_ai_quality.sql — si las
    # columnas no existen todavía, reintentamos el insert sin ellas.
    _quality_keys = []
    if score_llm:
        data["score_llm"] = json.dumps(score_llm)
        _quality_keys.append("score_llm")
    if score_judge:
        data["score_judge"] = json.dumps(score_judge)
        _quality_keys.append("score_judge")
    if verification_failed is not None:
        data["verification_failed"] = verification_failed
        _quality_keys.append("verification_failed")
    if sub_coverage is not None:
        data["sub_coverage"] = round(float(sub_coverage), 4)
        _quality_keys.append("sub_coverage")
    if words_per_sec is not None:
        data["words_per_sec"] = round(float(words_per_sec), 3)
        _quality_keys.append("words_per_sec")
    if clip_quality_issues:
        data["clip_quality_issues"] = json.dumps(clip_quality_issues)
        _quality_keys.append("clip_quality_issues")
    if clip_generation_error:
        data["clip_generation_error"] = clip_generation_error[:500]
        _quality_keys.append("clip_generation_error")
    # W10: copy por clip (requiere supabase/migrations/*_copy_por_clip.sql)
    if title:
        data["title"] = title[:60]
        _quality_keys.append("title")
    if description:
        data["description"] = description
        _quality_keys.append("description")
    if hashtags:
        data["hashtags"] = hashtags
        _quality_keys.append("hashtags")
    # W9-B (docs/PLAN_CALIDAD.md §9 W9, requiere migración galeria_hd)
    if preview_url:
        data["preview_url"] = preview_url
        _quality_keys.append("preview_url")

    def _insert(payload):
        supabase.table("content_results").insert(payload).execute()

    try:
        _insert(data)
    except Exception as e:
        if _is_connection_error(e):
            print(f"⚠️ save_content_result: conexión perdida, reconectando y reintentando...")
            reset_supabase()
            supabase = get_supabase()
            if supabase:
                _insert(data)
            else:
                raise RuntimeError("No se pudo reconectar a Supabase para guardar resultado")
        elif _quality_keys and ("column" in str(e).lower() or "pgrst204" in str(e).lower()):
            print(
                f"⚠️ save_content_result: columnas de calidad no existen aún "
                f"({_quality_keys}) — falta la migración ai_quality (ver supabase/legacy). "
                f"Guardando sin esas columnas."
            )
            for k in _quality_keys:
                data.pop(k, None)
            _insert(data)
        else:
            raise

    return result_id


def upload_clip_to_storage(file_path: str, job_id: str, moment_index: int) -> Optional[str]:
    """Upload clip to Cloudflare R2 and return public URL"""
    if is_dry_run():
        print(f"   🧪 dry-run: no se sube clip {moment_index} a R2")
        return f"dryrun://{job_id}/{moment_index}.mp4"
    try:
        from services.storage_client import upload_file
        
        file_name = f"{job_id}/clip_{moment_index}.mp4"
        public_url = upload_file(file_path, file_name)
        
        if public_url:
            print(f"✅ Uploaded to R2: {public_url}")
            return public_url
        else:
            print("❌ R2 Upload failed (returned None)")
            return None
            
    except Exception as e:
        print(f"❌ Failed to upload clip to R2: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════
# clip_edits — Phase 3: post-clip re-render queue
# ═══════════════════════════════════════════════════════════════════════════
def claim_next_clip_edit() -> Optional[dict]:
    """
    Atomically claim the oldest queued clip_edit and mark it 'processing'.
    Returns the row, or None if there's nothing queued.
    """
    supabase = get_supabase()
    if not supabase:
        return None

    try:
        # Fetch oldest queued
        res = (
            supabase.table("clip_edits")
            .select("*")
            .eq("status", "queued")
            .order("created_at")
            .limit(1)
            .execute()
        )
        if not res.data:
            return None

        edit = res.data[0]

        # Atomically claim (only succeeds if still 'queued')
        upd = (
            supabase.table("clip_edits")
            .update({"status": "processing", "error_message": None})
            .eq("id", edit["id"])
            .eq("status", "queued")
            .execute()
        )
        if not upd.data:
            # Lost the race, another worker claimed it
            return None

        return upd.data[0]
    except Exception as e:
        print(f"⚠️ claim_next_clip_edit failed: {e}")
        # HTTP/2 GOAWAY / "Server disconnected" — fuerza reconexión en la
        # próxima llamada en vez de seguir usando el cliente roto.
        if _is_connection_error(e):
            reset_supabase()
        return None


def mark_clip_edit_completed(edit_id: str, rendered_clip_url: str) -> None:
    supabase = get_supabase()
    if not supabase:
        return
    try:
        supabase.table("clip_edits").update({
            "status": "completed",
            "rendered_clip_url": rendered_clip_url,
            "error_message": None,
        }).eq("id", edit_id).execute()
    except Exception as e:
        print(f"⚠️ mark_clip_edit_completed failed: {e}")


def mark_clip_edit_failed(edit_id: str, error_message: str) -> None:
    supabase = get_supabase()
    if not supabase:
        return
    try:
        supabase.table("clip_edits").update({
            "status": "failed",
            "error_message": (error_message or "")[:500],
        }).eq("id", edit_id).execute()
    except Exception as e:
        print(f"⚠️ mark_clip_edit_failed failed: {e}")


def get_content_result(content_result_id: str) -> Optional[dict]:
    supabase = get_supabase()
    if not supabase:
        return None
    try:
        res = (
            supabase.table("content_results")
            .select("*")
            .eq("id", content_result_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"⚠️ get_content_result failed: {e}")
        return None


def get_job(job_id: str) -> Optional[dict]:
    supabase = get_supabase()
    if not supabase:
        return None
    try:
        res = (
            supabase.table("jobs")
            .select("*")
            .eq("id", job_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        print(f"⚠️ get_job failed: {e}")
        return None


def upload_edited_clip_to_storage(file_path: str, edit_id: str) -> Optional[str]:
    """Upload re-rendered clip to R2 under a stable path keyed by edit_id."""
    try:
        from services.storage_client import upload_file
        object_name = f"clip_edits/{edit_id}.mp4"
        url = upload_file(file_path, object_name)
        if url:
            print(f"✅ Edited clip uploaded: {url[:70]}...")
        return url
    except Exception as e:
        print(f"❌ Failed to upload edited clip: {e}")
        return None


def upload_raw_clip_to_storage(file_path: str, job_id: str, moment_index: int) -> Optional[str]:
    """
    Plan C: sube el segmento CRUDO (sin subs/overlay) a R2 para cachear.
    Usado por el editor post-clip para re-renderizar sin re-descargar de YouTube.

    Path estable: raw_clips/{job_id}_{moment_index}.mp4
    """
    if is_dry_run():
        return f"dryrun://{job_id}/raw_{moment_index}.mp4"
    try:
        from services.storage_client import upload_file
        object_name = f"raw_clips/{job_id}_{moment_index}.mp4"
        url = upload_file(file_path, object_name)
        if url:
            print(f"✅ Raw clip cached: {url[:70]}...")
        return url
    except Exception as e:
        print(f"⚠️ Failed to cache raw clip (non-fatal): {e}")
        return None


def deduct_credit(user_id: str, job_id: str, video_url: str) -> bool:
    """
    Deduct 1 credit from user using atomic SQL function.
    Must be called only after successful processing.
    """
    if is_dry_run():
        print("   🧪 dry-run: no se descuenta crédito")
        return True
    supabase = get_supabase()
    if not supabase:
        print("❌ Cannot deduct credit: Supabase not connected")
        return False
        
    try:
        # Call atomic RPC function
        response = supabase.rpc('deduct_user_credit', {
            'p_user_id': user_id,
            'p_job_id': job_id,
            'p_description': f"Processed video: {video_url}"
        }).execute()
        
        if not response.data or len(response.data) == 0:
            print(f"❌ No response from deduct_user_credit")
            return False
        
        result = response.data[0]
        
        if result['success']:
            print(f"✅ Credit deducted. New balance: {result['new_credits']} credits")
            return True
        else:
            print(f"❌ Failed to deduct credit: {result.get('message', 'Unknown error')}")
            return False
        
    except Exception as e:
        print(f"❌ Error in deduct_credit: {e}")
        return False
