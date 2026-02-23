"""
S3: Transcription Cache Service
Caches video transcriptions in Supabase to avoid re-transcribing already processed videos.
Saves ~$0.006/min on Whisper API costs per cache hit.
"""
from services.supabase_client import get_supabase


def get_cached_transcript(video_id: str) -> dict | None:
    """
    Check if a transcript exists in cache for the given video_id.
    Returns the cached transcript dict or None if not found.
    """
    supabase = get_supabase()
    if not supabase:
        return None

    try:
        result = supabase.table("transcription_cache") \
            .select("transcript, language, duration_seconds") \
            .eq("video_id", video_id) \
            .limit(1) \
            .execute()

        if result.data and len(result.data) > 0:
            import json
            cached = result.data[0]
            transcript = cached["transcript"]
            # transcript is stored as JSON string
            if isinstance(transcript, str):
                transcript = json.loads(transcript)
            return transcript
    except Exception as e:
        print(f"⚠️ Cache lookup failed (non-fatal): {e}")

    return None


def save_transcript(video_id: str, transcript: dict, language: str = None, duration_seconds: float = None) -> bool:
    """
    Save a transcript to cache for future reuse.
    Returns True if saved successfully.
    """
    supabase = get_supabase()
    if not supabase:
        return False

    try:
        import json
        supabase.table("transcription_cache").upsert({
            "video_id": video_id,
            "transcript": json.dumps(transcript, ensure_ascii=False),
            "language": language,
            "duration_seconds": duration_seconds,
        }).execute()
        return True
    except Exception as e:
        print(f"⚠️ Cache save failed (non-fatal): {e}")
        return False
