"""
Supabase client for Python worker
"""
import os
from typing import Optional
from supabase import create_client, Client

_supabase: Optional[Client] = None


def get_supabase() -> Optional[Client]:
    """Get Supabase client singleton"""
    global _supabase
    
    if _supabase is not None:
        return _supabase
    
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    
    if not url or not key:
        print("⚠️ Supabase credentials not found. Using SQLite fallback.")
        return None
    
    try:
        print(f"🔌 Connecting to Supabase: {url}")
        _supabase = create_client(url, key)
        print("✅ Supabase client created successfully")
        return _supabase
    except Exception as e:
        print(f"❌ Failed to create Supabase client: {e}")
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

        supabase.table("content_results").insert(data).execute()
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
