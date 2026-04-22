"""
YouTube Viral Content Engine - AI Worker
Watches the queue folder and processes video analysis jobs.
Now with video clipping and Supabase integration.
"""
import gc
import os
import sys
import json
import time
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import sentry_sdk

# Load environment variables
load_dotenv(Path(__file__).parent.parent / ".env")

# C4: Initialize Sentry error tracking
sentry_sdk.init(
    dsn=os.getenv("SENTRY_DSN_WORKER", "https://fa1819fcd1c305c1966bc49de239c99b@o4510909878632448.ingest.us.sentry.io/4510909933092864"),
    send_default_pii=True,
    environment=os.getenv("ENVIRONMENT", "development"),
    traces_sample_rate=0.2,
)

# Validate environment variables before proceeding
sys.path.insert(0, str(Path(__file__).parent))
from config.validate_env import validate_env
validate_env()

# Add parent to path for imports

from services.downloader import download_audio, download_video, cleanup_all
from services.processor import analyze_with_gemini, cleanup_uploaded_file
from services.clipper import extract_clip, cleanup_clips
from services.clip_generator import generate_clip, ClipGenerationError
from services.supabase_client import (
    update_job_status,
    update_job_error,
    save_content_result,
    upload_clip_to_storage,
    get_supabase
)

DOWNLOADS_DIR = Path(__file__).parent / "downloads"
CLIPS_DIR = Path(__file__).parent / "clips"
POLL_INTERVAL = 3  # seconds between Supabase polls


def cleanup_old_files(max_age_hours: int = 24) -> None:
    """
    Q3: Remove files older than max_age_hours from downloads/ and clips/.
    Prevents disk from filling up with leftover media files.
    """
    import time as _time
    cutoff = _time.time() - (max_age_hours * 3600)
    cleaned = 0
    
    for directory in [DOWNLOADS_DIR, CLIPS_DIR]:
        if not directory.exists():
            continue
        for file_path in directory.iterdir():
            if file_path.is_file() and file_path.stat().st_mtime < cutoff:
                try:
                    file_path.unlink()
                    cleaned += 1
                except Exception as e:
                    print(f"⚠️ Could not delete {file_path.name}: {e}")
    
    if cleaned > 0:
        print(f"🧹 Cleanup: removed {cleaned} file(s) older than {max_age_hours}h")


def recover_stale_jobs(max_age_minutes: int = 20) -> None:
    """
    C5: Marca como 'failed' los jobs que quedaron en 'processing' por más de
    max_age_minutes (causado por OOM-kill del worker u otros crashes).

    Antes reseteaba a 'pending', pero eso causaba loops infinitos si el job
    seguía fallando por OOM. Ahora los marca failed con mensaje descriptivo
    para que el usuario pueda re-intentar manualmente.
    """
    supabase = get_supabase()
    if not supabase:
        return

    try:
        threshold = (datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)).isoformat()
        result = supabase.table("jobs").select("id") \
            .eq("status", "processing") \
            .lt("updated_at", threshold) \
            .execute()

        if not result.data:
            return

        for job in result.data:
            supabase.table("jobs").update({
                "status": "failed",
                "error_message": "Worker crashed while processing (likely out of memory). Please retry the job.",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", job["id"]).execute()
            print(f"🪦 Zombie job marcado failed: {job['id']}")

        print(f"🧹 {len(result.data)} zombie job(s) limpiados al arrancar")
    except Exception as e:
        print(f"⚠️ Stale job recovery failed: {e}")


JOB_TIMEOUT_SECONDS = 30 * 60  # 30 minutes


def process_job(job_data: dict) -> None:
    """
    Process a single job: download, analyze, clip, upload, save results.
    C5: Includes a 30-minute timeout to prevent stuck jobs.
    """
    job_id = job_data["id"]
    video_url = job_data["videoUrl"]
    audio_path = None
    video_path = None
    video_id = None
    timed_out = threading.Event()  # C5: timeout flag
    
    # C5: Start a timeout timer (Windows-compatible, using threading instead of signal)
    def _on_timeout():
        timed_out.set()
        print(f"⏰ Job {job_id} exceeded {JOB_TIMEOUT_SECONDS // 60} minute timeout!")
    
    timeout_timer = threading.Timer(JOB_TIMEOUT_SECONDS, _on_timeout)
    timeout_timer.daemon = True
    timeout_timer.start()
    
    print(f"\n{'='*60}")
    print(f"🎬 Processing job: {job_id}")
    print(f"📺 Video URL: {video_url}")
    print(f"{'='*60}\n")
    
    try:
        # Update status to processing
        update_job_status(job_id, "processing")
        
        # C5: Check timeout between each major step
        def check_timeout():
            if timed_out.is_set():
                raise TimeoutError(f"Job exceeded {JOB_TIMEOUT_SECONDS // 60} minute limit")
        
        # Steps 1+2: Get transcript via YouTube Transcript API (no download needed)
        # This bypasses yt-dlp bot detection entirely.
        print("\n📝 Steps 1+2: Fetching transcript from YouTube...")
        from services.supabase_client import update_job_progress
        update_job_progress(job_id, current_step="downloading", progress_percentage=10)

        from services.yt_transcript import get_youtube_transcript
        transcript, video_info = get_youtube_transcript(video_url)
        video_id = video_info["id"]

        update_job_status(job_id, "processing", video_info["title"])
        update_job_progress(job_id, progress_percentage=40)
        print(f"✅ Transcript ready: {len(transcript.get('segments', []))} segments")
        check_timeout()  # C5

        # S3: Save transcript to cache
        from services.transcript_cache import save_transcript
        save_transcript(
            video_id=video_id,
            transcript=transcript,
            language=transcript.get("language"),
            duration_seconds=video_info.get("duration"),
        )
        print(f"💾 Transcript cached for video {video_id}")
        
        # Step 3: Analyze transcript with AI (via OpenRouter)
        print("\n🤖 Step 3: Analyzing transcript for viral moments...")
        update_job_progress(job_id, current_step="analyzing", progress_percentage=50)
        
        from services.processor import analyze_with_openrouter
        result = analyze_with_openrouter(transcript, video_info)
        update_job_progress(job_id, progress_percentage=65)
        check_timeout()  # C5
        
        # Step 3.5: Quality Filter - Validate durations (Sprint 1)
        # IMPORTANT: Populate start_time/end_time from surgical_clipping FIRST.
        # Podcast/entertainment prompts use surgical_clipping instead of direct timestamps.
        # If we validate before populating, all moments appear to have None timestamps and get dropped.
        print("\n🔍 Step 3.5: Populating timestamps + quality filter...")
        for moment in result.viral_moments:
            if moment.start_time is None and hasattr(moment, 'surgical_clipping') and moment.surgical_clipping:
                moment.start_time = int(moment.surgical_clipping.start_time)
                moment.end_time = int(moment.surgical_clipping.end_time)
                print(f"📋 Pre-populated timestamps from surgical_clipping: {moment.start_time}s - {moment.end_time}s")

        from services.validation import validate_durations
        result.viral_moments = validate_durations(result.viral_moments, min_duration=10)

        if not result.viral_moments:
            raise Exception("No viral moments passed quality filter (all clips too short)")
        
        # Step 3.6: Whisper Validation - Verify against transcript (Sprint 3)
        # TODO: Implement in Sprint 3
        # print("\n🎯 Step 3.6: Whisper validation (transcript verification)...")
        # from services.validation import validate_against_transcript
        # for moment in result.viral_moments:
        #     validate_against_transcript(moment, transcript)
        
        # Step 4: Download full video for real MP4 clipping (Sprint 1.1 Día 8).
        # If download fails (e.g. yt-dlp blocked on cloud IP), fall back to YouTube timestamp links.
        supabase = get_supabase()
        print("\n📹 Step 4: Downloading full video for MP4 clipping...")
        update_job_progress(job_id, current_step="clipping", progress_percentage=70)
        video_path = None
        try:
            video_path = download_video(video_url, video_id)
            print(f"✅ Video descargado: {video_path}")
        except Exception as e:
            print(f"⚠️ Video download falló ({e}) — fallback a links de YouTube")
            video_path = None
        
        # Step 5: Save results
        print("\n💾 Step 5: Saving results...")
        update_job_progress(job_id, current_step="generating", progress_percentage=85)
        
        # Summary is now stored in jobs table, not content_results
        # save_content_result() is only for actual content pieces (twitter, tiktok, etc.)

        
        # Process each viral moment
        for i, moment in enumerate(result.viral_moments):
            moment_index = i + 1
            clip_url = None
            
            # Generate clip (Sprint 1.1 Día 8):
            #  - Si hay video local: generate_clip → 9:16 + subs + overlay → upload a R2.
            #  - Fallback a YouTube timestamp link si algo falla o no hay video.
            if video_path:
                try:
                    print(f"\n✂️ Generando clip MP4 {moment_index} ({moment.start_time}-{moment.end_time}s)...")
                    clip_output = CLIPS_DIR / f"{video_id}_moment_{moment_index}.mp4"
                    clip_output.parent.mkdir(parents=True, exist_ok=True)
                    # Preferencia: viral_overlay (corto, viral) > tiktok_package.overlay_text > None.
                    # Si no hay overlay corto, dejamos el clip SIN overlay (no usamos hook largo).
                    overlay_text = getattr(moment, 'viral_overlay', None)
                    if not overlay_text:
                        tp = getattr(moment, 'tiktok_package', None)
                        overlay_text = getattr(tp, 'overlay_text', None) if tp else None
                    gen_result = generate_clip(
                        video_path=video_path,
                        start_sec=float(moment.start_time),
                        end_sec=float(moment.end_time),
                        output_path=str(clip_output),
                        segments=transcript.get("segments"),
                        subtitle_style="tiktok_viral",
                        overlay_text=overlay_text,
                        overlay_style="tiktok_viral",
                        target_width=720,   # 720x1280 (9:16) — 55% menos RAM vs 1080x1920
                        target_height=1280, # sube bien a TikTok/Reels/Shorts
                    )
                    print(f"✅ Clip generado en {gen_result.total_time_sec}s, {gen_result.final.size_mb:.1f}MB")
                    print(f"📤 Subiendo clip {moment_index} a R2...")
                    clip_url = upload_clip_to_storage(str(clip_output), job_id, moment_index)
                    if clip_url:
                        print(f"✅ Clip subido: {clip_url[:70]}...")
                    else:
                        raise RuntimeError("upload_clip_to_storage devolvió None")
                except (ClipGenerationError, Exception) as e:
                    print(f"⚠️ Clip {moment_index} falló: {e}")
                    if moment.start_time is not None:
                        clip_url = f"https://www.youtube.com/watch?v={video_id}&t={int(moment.start_time)}s"
                        print(f"🔗 Fallback a link de YouTube: {clip_url}")
                finally:
                    # Liberar el clip local y forzar GC entre momentos para
                    # mantener el pico de RAM bajo en el free tier (512MB).
                    if clip_output.exists():
                        try:
                            clip_output.unlink()
                        except Exception:
                            pass
                    gc.collect()
            else:
                # Sin video local — deep link con timestamp
                if moment.start_time is not None:
                    clip_url = f"https://www.youtube.com/watch?v={video_id}&t={int(moment.start_time)}s"
                    print(f"🔗 Clip {moment_index}: YouTube link at {int(moment.start_time)}s → {clip_url}")
            
            # Extract scores if available
            scores = moment.scores if hasattr(moment, 'scores') and moment.scores else None
            pillar_raw = moment.pillar_type if hasattr(moment, 'pillar_type') else None
            
            # Sanitize pillar_type - extract first valid value
            pillar = None
            if pillar_raw:
                valid_pillars = ['authority', 'utility', 'connection']
                for p in pillar_raw.lower().replace('|', ' ').split():
                    if p.strip() in valid_pillars:
                        pillar = p.strip()
                        break
            
            # Extract Phase B fields
            sentiment = getattr(moment, 'sentiment_detected', None)
            roi_time = getattr(moment, 'roi_time_saved', None)
            justifications = None
            if hasattr(moment, 'score_justifications') and moment.score_justifications:
                justifications = [j.model_dump() if hasattr(j, 'model_dump') else j for j in moment.score_justifications]
            
            # Get category for routing (default to entertainment if not set)
            category = getattr(moment, 'category', 'entertainment')
            if category is None:
                category = 'entertainment'
            
            # ═══════════════════════════════════════════════════════
            # 📂 CATEGORY NORMALIZATION & MAPPING
            # ═══════════════════════════════════════════════════════
            # Normalize to lowercase
            category = category.lower().strip()
            
            # Category aliases (map variations to canonical categories)
            CATEGORY_ALIASES = {
                'finance': 'business',
                'finanzas': 'business',
                'inversiones': 'business',
                'startups': 'tech',
                'tecnología': 'tech',
                'comedy': 'entertainment',
                'humor': 'entertainment',
                'interview': 'podcast',
                'entrevista': 'podcast',
            }
            
            # Apply alias mapping
            if category in CATEGORY_ALIASES:
                original = category
                category = CATEGORY_ALIASES[category]
                print(f"   📝 Category mapped: {original} → {category}")
            
            # ═══════════════════════════════════════════════════════
            # 🎯 CATEGORY ROUTER (Sprint 2 - Phase 4B)
            # ═══════════════════════════════════════════════════════
            print(f"\n📊 Category Router: {category.upper()}")
            
            # ALWAYS save Twitter thread (universal)
            save_content_result(
                job_id=job_id,
                content_type="twitter_thread",
                content=moment.content_pieces.twitter_thread,
                clip_url=clip_url,
                start_time=moment.start_time,
                end_time=moment.end_time,
                hook=moment.hook,
                emotional_trigger=moment.emotional_trigger,
                moment_index=moment_index,
                pillar_type=pillar,
                score_hook=scores.hook if scores else None,
                score_retention=scores.retention if scores else None,
                score_shareability=scores.shareability if scores else None,
                sentiment_detected=sentiment,
                roi_time_saved=roi_time,
                score_justifications=justifications
            )
            
            # Route platform-specific content based on category
            if category in ['business', 'tech']:
                # B2B Strategy: LinkedIn + Twitter
                print(f"   → Routing to LinkedIn (B2B)")
                if moment.content_pieces.linkedin_post:
                    save_content_result(
                        job_id=job_id,
                        content_type="linkedin_post",
                        content=moment.content_pieces.linkedin_post,
                        clip_url=clip_url,
                        start_time=moment.start_time,
                        end_time=moment.end_time,
                        hook=moment.hook,
                        moment_index=moment_index,
                        pillar_type=pillar,
                        score_hook=scores.hook if scores else None,
                        score_retention=scores.retention if scores else None,
                        score_shareability=scores.shareability if scores else None,
                        sentiment_detected=sentiment,
                        roi_time_saved=roi_time,
                        score_justifications=justifications
                    )
                    
            elif category in ['entertainment', 'podcast', 'lifestyle']:
                # Viral Strategy: TikTok + Twitter
                print(f"   → Routing to TikTok (Viral)")
                if hasattr(moment.content_pieces, 'tiktok_caption') and moment.content_pieces.tiktok_caption:
                    save_content_result(
                        job_id=job_id,
                        content_type="tiktok_caption",
                        content=moment.content_pieces.tiktok_caption,
                        clip_url=clip_url,
                        start_time=moment.start_time,
                        end_time=moment.end_time,
                        hook=moment.hook,
                        moment_index=moment_index,
                        pillar_type=pillar,
                        score_hook=scores.hook if scores else None,
                        score_retention=scores.retention if scores else None,
                        score_shareability=scores.shareability if scores else None,
                        sentiment_detected=sentiment,
                        roi_time_saved=roi_time,
                        score_justifications=justifications
                    )

            
            # Save short script ONLY if not deprecated
            if moment.content_pieces.short_video_script:
                if "DEPRECATED" not in moment.content_pieces.short_video_script.upper():
                    save_content_result(
                        job_id=job_id,
                        content_type="short_video_script",
                        content=moment.content_pieces.short_video_script,
                        clip_url=clip_url,
                        start_time=moment.start_time,
                        end_time=moment.end_time,
                        moment_index=moment_index,
                        pillar_type=pillar,
                        score_hook=scores.hook if scores else None,
                        score_retention=scores.retention if scores else None,
                        score_shareability=scores.shareability if scores else None,
                        sentiment_detected=sentiment,
                        roi_time_saved=roi_time,
                        score_justifications=justifications
                    )
        
        # Update status to completed
        update_job_progress(job_id, current_step="completed", progress_percentage=100)
        update_job_status(job_id, "completed")
        print(f"\n✅ Job {job_id} completed successfully!")
        print(f"   Generated content for {len(result.viral_moments)} viral moments")
        # Deduct credits (Sprint 3)
        user_id = job_data.get("userId")
        if user_id and supabase:
            try:
                print(f"💰 Deducting credit for user {user_id}...")
                from services.supabase_client import deduct_credit
                deduct_credit(user_id, job_id, video_url)
                print(f"✅ Credit deducted successfully")
            except Exception as e:
                print(f"⚠️ Failed to deduct credit: {e}")
        
    except Exception as e:
        print(f"\n❌ Job {job_id} failed: {str(e)}")
        update_job_error(job_id, str(e))
        
    finally:
        # C5: Cancel timeout timer
        timeout_timer.cancel()
        
        # Cleanup all files
        if video_id:
            cleanup_all(video_id)
            cleanup_clips(video_id)
        
        # Cleanup Gemini uploaded files
        cleanup_uploaded_file(audio_path if audio_path else "")


def watch_queue():
    """
    S1: Poll Supabase for pending jobs.
    S2: Process up to MAX_WORKERS jobs in parallel using ThreadPoolExecutor.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    supabase = get_supabase()
    supabase_status = "✅ Connected" if supabase else "❌ Not configured"
    max_workers = int(os.getenv("MAX_WORKERS", "2"))
    
    print(f"""
╔════════════════════════════════════════════════════════════╗
║     YouTube Viral Content Engine - AI Worker v3.0         ║
╠════════════════════════════════════════════════════════════╣
║  Queue: Supabase (jobs table)                             ║
║  Workers: {max_workers} parallel                                      ║
║  Supabase: {supabase_status:<46} ║
╚════════════════════════════════════════════════════════════╝
    """)
    
    if not supabase:
        print("❌ Supabase not configured. Worker cannot start.")
        sys.exit(1)
    
    # Q3: Cleanup old files on startup
    cleanup_old_files()
    
    # C5: Recover stale jobs on startup
    recover_stale_jobs()
    
    active_jobs = set()  # Track job IDs currently being processed
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        
        while True:
            try:
                # S2: Determine how many slots are available
                available_slots = max_workers - len(futures)
                
                if available_slots > 0:
                    # S1: Poll Supabase for pending jobs
                    result = supabase.table("jobs") \
                        .select("id, video_url, user_id") \
                        .eq("status", "pending") \
                        .order("created_at") \
                        .limit(available_slots) \
                        .execute()
                    
                    if result.data:
                        for job in result.data:
                            if job["id"] in active_jobs:
                                continue
                            
                            # Atomically claim the job
                            supabase.table("jobs") \
                                .update({"status": "processing"}) \
                                .eq("id", job["id"]) \
                                .eq("status", "pending") \
                                .execute()
                            
                            job_data = {
                                "id": job["id"],
                                "videoUrl": job["video_url"],
                                "userId": job.get("user_id"),
                            }
                            
                            active_jobs.add(job["id"])
                            future = executor.submit(process_job, job_data)
                            futures[future] = job["id"]
                
                # Check for completed futures
                done_futures = [f for f in futures if f.done()]
                for future in done_futures:
                    job_id = futures.pop(future)
                    active_jobs.discard(job_id)
                    try:
                        future.result()  # Raise any exceptions
                    except Exception as e:
                        print(f"❌ Job {job_id} failed: {e}")
                
                time.sleep(POLL_INTERVAL)
                
            except KeyboardInterrupt:
                print("\n\n👋 Worker stopped by user")
                executor.shutdown(wait=False)
                break
            except Exception as e:
                print(f"❌ Unexpected error: {e}")
                time.sleep(5)


if __name__ == "__main__":
    if not os.getenv("OPENROUTER_API_KEY"):
        print("❌ Error: OPENROUTER_API_KEY not found")
        print("   Please add it to your .env file")
        sys.exit(1)
    
    # Start health server only if PORT is explicitly set (e.g. Render)
    # On Hetzner/systemd, PORT is not set so we skip it
    port = os.getenv("PORT")
    if port:
        import threading
        from http.server import HTTPServer, BaseHTTPRequestHandler

        class HealthHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok","service":"viralengine-worker"}')

            def log_message(self, format, *args):
                pass  # Silence request logs

        health_server = HTTPServer(("0.0.0.0", int(port)), HealthHandler)
        health_thread = threading.Thread(target=health_server.serve_forever, daemon=True)
        health_thread.start()
        print(f"🏥 Health server listening on port {port}")
    else:
        print("ℹ️  No PORT set — skipping health server (systemd mode)")

    watch_queue()
