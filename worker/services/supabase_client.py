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
        print("⚠️ Supabase credentials not found. Using SQLite fallback.")
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


def update_job_status(job_id: str, status: str, video_title: str = None) -> None:
    """Update job status in Supabase or SQLite fallback"""
    supabase = get_supabase()
    
    if supabase:
        update_data = {"status": status}
        if video_title:
            update_data["video_title"] = video_title
        
        supabase.table("jobs").update(update_data).eq("id", job_id).execute()
    else:
        # SQLite fallback
        from services.database import update_job_status as sqlite_update
        sqlite_update(job_id, status)


def update_job_error(job_id: str, error_message: str) -> None:
    """Mark job as failed"""
    supabase = get_supabase()
    
    if supabase:
        supabase.table("jobs").update({
            "status": "failed",
            "error_message": error_message
        }).eq("id", job_id).execute()
    else:
        from services.database import update_job_error as sqlite_update
        sqlite_update(job_id, error_message)


def update_job_progress(
    job_id: str, 
    current_step: str = None, 
    progress_percentage: int = None
) -> None:
    """Update job processing step and progress for real-time UI updates
    
    Args:
        job_id: The job ID to update
        current_step: Current processing step (e.g., 'downloading', 'transcribing', 'analyzing', 'clipping', 'generating')
        progress_percentage: Progress from 0-100
    """
    supabase = get_supabase()
    
    if supabase:
        update_data = {}
        if current_step is not None:
            update_data["current_step"] = current_step
        if progress_percentage is not None:
            update_data["progress_percentage"] = progress_percentage
        
        if update_data:
            supabase.table("jobs").update(update_data).eq("id", job_id).execute()
    else:
        # SQLite fallback
        from services.database import update_job_progress as sqlite_update
        sqlite_update(job_id, current_step, progress_percentage)


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
) -> str:
    """Save content result to Supabase or SQLite"""
    import uuid
    import json
    result_id = str(uuid.uuid4())
    
    supabase = get_supabase()
    
    if supabase:
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

        try:
            supabase.table("content_results").insert(data).execute()
        except Exception as e:
            if _is_connection_error(e):
                print(f"⚠️ save_content_result: conexión perdida, reconectando y reintentando...")
                reset_supabase()
                supabase = get_supabase()
                if supabase:
                    supabase.table("content_results").insert(data).execute()
                else:
                    raise RuntimeError("No se pudo reconectar a Supabase para guardar resultado")
            else:
                raise
    else:
        from services.database import save_content_result as sqlite_save
        metadata = json.dumps({
            "moment_index": moment_index,
            "start_time": start_time,
            "end_time": end_time,
            "hook": hook,
            "emotional_trigger": emotional_trigger,
            "clip_url": clip_url,
            "pillar_type": pillar_type,
            "scores": {"hook": score_hook, "retention": score_retention, "shareability": score_shareability}
        })
        sqlite_save(job_id, content_type, content, metadata)
    
    return result_id


def upload_clip_to_storage(file_path: str, job_id: str, moment_index: int) -> Optional[str]:
    """Upload clip to Cloudflare R2 and return public URL"""
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
