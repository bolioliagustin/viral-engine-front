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
from config.logging import setup_logging, trace, set_phase, bind_trace
from context.job_context import set_job_context, clear_job_context, set_moment_index
from services.usage_tracker import finalize_job_usage, record_download_usage
setup_logging()

from config.validate_env import validate_env
validate_env()

# Add parent to path for imports

from services.downloader import (
    download_audio,
    download_video,
    get_stream_urls,
    download_clip_ytdlp,
    download_clip_via_stream_urls,
    download_clips_parallel,
    download_clip_apify,
    should_use_stream_urls_fallback,
    ClipDownloadResult,
    is_ytdlp_drm_error,
    cleanup_all,
    _use_apify_fallback,
)
# _download_video_ytdlp es interno pero lo usamos como primer intento del
# pipeline de descarga: si yt-dlp logra bajar el video completo (audio+video
# mergeados) en un solo MP4, evitamos por completo el problema del proxy
# residencial throttleando audio (30 KB/s) en la descarga parcial split.
from services.downloader import _download_video_ytdlp
from services.processor import analyze_with_gemini, cleanup_uploaded_file
from services.clipper import extract_clip, cleanup_clips
from services.clip_generator import (
    generate_clip,
    ClipGenerationError,
    cut_clip,
    extract_whisper_audio,
    filter_whisper_words,
    snap_trim_bounds,
    shift_words_timeline,
    fix_ghost_leading_words,
    srt_coverage_metric,
)
from services.supabase_client import (
    update_job_status,
    update_job_error,
    save_content_result,
    upload_clip_to_storage,
    get_supabase,
    claim_next_clip_edit,
    reset_supabase,
    start_keepalive,
)
from services.clip_edit_processor import process_clip_edit

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
FULL_YTDLP_MAX_DURATION_SEC = 3600  # >1h: evitar descarga completa (OOM / bandwidth)


def _resolve_video_duration(video_info: dict, transcript: dict, viral_moments) -> float:
    """
    Duración fiable para el cálculo de bytes en partial download.
    oEmbed devuelve 0; usamos transcript + momentos virales como respaldo.
    """
    candidates: list[float] = []
    base = video_info.get("duration") or 0
    if base > 0:
        candidates.append(float(base))

    segments = transcript.get("segments") or []
    if segments:
        candidates.append(float(segments[-1].get("end", 0)) + 5)

    for moment in viral_moments:
        end = getattr(moment, "end_time", None)
        if end is not None:
            candidates.append(float(end) + 30)

    if candidates:
        return max(candidates)

    # Sin datos: asumir video largo para no subestimar el ratio de bytes
    print("⚠️ Duración desconocida — asumiendo 2h para partial download")
    return 7200.0


JOB_TIMEOUT_SECONDS = 30 * 60  # 30 minutes
FULL_YTDLP_MAX_DURATION_SEC = 3600  # >1h: evitar descarga completa (OOM / bandwidth)
FULL_YTDLP_SHORT_MAX_SEC = 1800  # ≤30 min: candidato a yt-dlp full
VALID_DOWNLOAD_STRATEGIES = frozenset({
    "auto", "full_ytdlp", "upfront_partial", "per_clip_parallel",
})


class _SyncRetryNeeded(Exception):
    """Señal interna para reintentar descarga/corte por fallo de sync."""


def _download_phase_budget_sec() -> float:
    try:
        return max(60.0, float(os.getenv("DOWNLOAD_PHASE_BUDGET_SEC", "600")))
    except ValueError:
        return 600.0


def _clip_sync_retries() -> int:
    try:
        return max(0, int(os.getenv("CLIP_SYNC_RETRIES", "2")))
    except ValueError:
        return 2


def _strict_sync_validation() -> bool:
    return os.getenv("STRICT_SYNC_VALIDATION", "true").lower() not in ("0", "false", "no")


def _select_download_strategy(video_duration: float, viral_moments) -> str:
    """Selector automático de estrategia de descarga (sync-safe + rápido)."""
    override = (os.getenv("DOWNLOAD_STRATEGY") or "auto").strip().lower()
    if override != "auto":
        if override in VALID_DOWNLOAD_STRATEGIES:
            return override
        print(f"⚠️ DOWNLOAD_STRATEGY inválida '{override}' — usando auto")

    timed_moments = [
        m for m in viral_moments
        if m.start_time is not None and m.end_time is not None
    ]
    if not timed_moments:
        return "upfront_partial"

    max_end = max(float(m.end_time) for m in timed_moments)
    total_clip_sec = sum(float(m.end_time) - float(m.start_time) for m in timed_moments)
    coverage_ratio = max_end / max(video_duration, 1)
    clip_density = total_clip_sec / max(max_end, 1)

    if video_duration <= FULL_YTDLP_SHORT_MAX_SEC and _should_try_full_ytdlp_download(video_duration):
        return "full_ytdlp"

    if coverage_ratio < 0.35 and total_clip_sec / max(video_duration, 1) < 0.25:
        return "upfront_partial"

    if coverage_ratio >= 0.35 or clip_density < 0.5:
        return "per_clip_parallel"

    return "upfront_partial"


def _log_download_strategy(
    strategy: str,
    video_duration: float,
    viral_moments,
) -> None:
    timed = [m for m in viral_moments if m.end_time is not None]
    max_end = max((float(m.end_time) for m in timed), default=0.0)
    total_clip = sum(
        float(m.end_time) - float(m.start_time)
        for m in timed
        if m.start_time is not None
    )
    coverage = max_end / max(video_duration, 1)
    print(
        f"📐 Estrategia de descarga: {strategy} | "
        f"coverage={coverage:.0%} | clip_sec={total_clip:.0f}s / "
        f"video={video_duration:.0f}s"
    )


def _resolve_moment_video_source(
    *,
    moment_index: int,
    start_s: float,
    end_s: float,
    video_url: str,
    video_id: str,
    video_duration: float,
    muxed_video_path: str | None,
    clip_paths_cache: dict[int, ClipDownloadResult],
    partial_download_failed: bool,
    sync_attempt: int = 0,
) -> tuple[str, float, float, float, str | None]:
    """Resuelve fuente de video para un clip."""
    cached = clip_paths_cache.get(moment_index)
    if cached and Path(cached.path).exists() and sync_attempt == 0:
        print(f"   ✓ Usando segmento per-clip cacheado (paralelo)")
        return (
            cached.path,
            start_s - cached.download_start,
            end_s - cached.download_start,
            start_s,
            cached.path,
        )

    if muxed_video_path and Path(muxed_video_path).exists():
        print(f"   ✓ Usando video muxeado cacheado")
        return muxed_video_path, start_s, end_s, start_s, None

    if _should_use_ytdlp_for_clips():
        try:
            seg_out = str(DOWNLOADS_DIR / f"{video_id}_seg_{moment_index}_{int(start_s)}")
            proxy = None
            if sync_attempt > 0:
                from services.downloader import _get_proxy_list
                proxy_list = _get_proxy_list()
                if proxy_list:
                    proxy = proxy_list[sync_attempt % len(proxy_list)]
            dl_result = download_clip_ytdlp(
                youtube_url=video_url,
                start_sec=start_s,
                end_sec=end_s,
                output_path=seg_out,
                video_duration=video_duration,
                proxy_url=proxy,
            )
            print(f"   ✓ yt-dlp per-clip OK")
            return (
                dl_result.path,
                start_s - dl_result.download_start,
                end_s - dl_result.download_start,
                start_s,
                dl_result.path,
            )
        except Exception as e_ytdlp:
            print(f"   ⚠️ yt-dlp per-clip falló: {e_ytdlp}")

    if _use_apify_fallback():
        try:
            apify_out = str(DOWNLOADS_DIR / f"{video_id}_apify_{moment_index}.mp4")
            dl_result = download_clip_apify(
                youtube_url=video_url,
                start_sec=start_s,
                end_sec=end_s,
                output_path=apify_out,
            )
            print(f"   ✓ Apify per-clip OK")
            return dl_result.path, 0.0, end_s - start_s, start_s, dl_result.path
        except Exception as e_apify:
            print(f"   ⚠️ Apify per-clip falló: {e_apify}")

    if (
        should_use_stream_urls_fallback(start_s, video_duration)
        and (_prefer_rapidapi_download() or partial_download_failed)
    ):
        _clip_retries = int(os.getenv("CLIP_GEN_RETRIES", "1"))
        last_stream_err = None
        for _attempt in range(_clip_retries + 1):
            try:
                if _attempt > 0:
                    time.sleep(2 ** _attempt)
                print(f"   📥 Descargando clip vía stream partial (early clip)...")
                seg_path = download_clip_via_stream_urls(
                    youtube_url=video_url,
                    start_sec=start_s,
                    end_sec=end_s,
                    video_duration=video_duration,
                    video_id=video_id,
                    temp_id=f"{video_id}_m{moment_index}",
                )
                return seg_path, 0.0, end_s - start_s, start_s, seg_path
            except Exception as e_stream:
                last_stream_err = e_stream
                print(f"   ⚠️ Stream partial per-clip falló: {e_stream}")
        if last_stream_err:
            raise RuntimeError(
                f"Sin video para clip {moment_index} — stream partial falló: {last_stream_err}"
            ) from last_stream_err

    raise RuntimeError(
        f"Sin video disponible para clip {moment_index} "
        f"(muxed={'no' if not muxed_video_path else 'sí'}, "
        f"cached={'no' if not cached else 'sí'})"
    )


def _has_proxies() -> bool:
    """True si hay proxies residenciales configurados (Webshare)."""
    from services.downloader import _get_proxy_list
    return len(_get_proxy_list()) > 0


def _should_try_full_ytdlp_download(video_duration: float) -> bool:
    """En producción el camino primario es RapidAPI + sticky proxy (partial download).

    yt-dlp full queda como intento opcional cuando RapidAPI no está forzado y hay
    proxy residencial (misma IP resuelve+descarga). Muchos videos fallan por
    DRM/PO Token aunque YOUTUBE_COOKIES esté configurado.
    """
    if _prefer_rapidapi_download():
        return False
    if video_duration > FULL_YTDLP_MAX_DURATION_SEC:
        print(
            f"ℹ️ Video >{FULL_YTDLP_MAX_DURATION_SEC // 60}min "
            f"({video_duration:.0f}s) — omitiendo yt-dlp full, usando partial download"
        )
        return False
    if _has_proxies():
        return True
    if _prefer_rapidapi_download():
        return False
    return True


def _prefer_rapidapi_download() -> bool:
    """True cuando debemos usar RapidAPI en lugar de yt-dlp para video."""
    if os.getenv("USE_RAPIDAPI_DOWNLOAD", "").lower() in ("1", "true", "yes"):
        return True
    if os.getenv("ENVIRONMENT", "development").lower() in ("production", "prod"):
        return True
    return bool(os.getenv("RAPIDAPI_KEY"))


def _should_use_ytdlp_for_clips() -> bool:
    """yt-dlp per-clip: fiable con proxy residencial (misma IP resuelve+descarga)."""
    if os.getenv("YTDLP_CLIP_FALLBACK", "").lower() in ("1", "true", "yes"):
        return True
    if _has_proxies():
        return True
    if _prefer_rapidapi_download():
        return False
    return True


def _log_download_config() -> None:
    from services.downloader import _get_proxy_list, cookies_env_status, has_youtube_cookies
    rapid = os.getenv("RAPIDAPI_KEY")
    n_proxies = len(_get_proxy_list())
    print(
        f"🔧 Download config: ENV={os.getenv('ENVIRONMENT', 'development')} | "
        f"USE_RAPIDAPI={os.getenv('USE_RAPIDAPI_DOWNLOAD', 'false')} | "
        f"RAPIDAPI_KEY={'SET' if rapid else '❌ MISSING'} | "
        f"YOUTUBE_COOKIES={cookies_env_status()} | "
        f"proxies={n_proxies} | "
        f"prefer_rapidapi={_prefer_rapidapi_download()} | "
        f"yt_dlp_clips={_should_use_ytdlp_for_clips()}"
    )
    if not has_youtube_cookies():
        print(
            "⚠️ YOUTUBE_COOKIES no llegó al worker. Este worker corre en el VPS "
            "(Docker), NO en Render — agregala en ~/viralengine/.env y reiniciá: "
            "docker compose -f docker-compose.worker.yml up -d --build"
        )
    if _prefer_rapidapi_download() and n_proxies == 0:
        print(
            "⚠️ Sin proxies residenciales — descargas googlevideo desde datacenter "
            "suelen fallar con 403. Configura WEBSHARE_PROXY_FILE en Render."
        )


def process_job(job_data: dict) -> None:
    """
    Process a single job: download, analyze, clip, upload, save results.
    C5: Includes a 30-minute timeout to prevent stuck jobs.
    """
    job_id = job_data["id"]
    with trace(job_id=job_id, phase="start"):
        _process_job_inner(job_data, job_id)


def _process_job_inner(job_data: dict, job_id: str) -> None:
    set_job_context(job_id=job_id, user_id=job_data.get("userId"))
    video_url = job_data["videoUrl"]
    audio_path = None
    video_path = None
    video_id = None
    muxed_video_path = None  # se setea solo en path B (partial download fallback)
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
        set_phase("transcript")
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
        set_phase("analyze")
        print("\n🤖 Step 3: Analyzing transcript for viral moments...")
        update_job_progress(job_id, current_step="analyzing", progress_percentage=50)

        # Fase 5: personalización — tone del job + perfil real del usuario
        job_tone = (job_data.get("tone") or "").strip().lower() or "profesional"
        user_name = None
        user_title = None
        _user_id = job_data.get("userId")
        if _user_id:
            try:
                _sb = get_supabase()
                if _sb:
                    _profile = _sb.table("users") \
                        .select("display_name, professional_title") \
                        .eq("id", _user_id).limit(1).execute()
                    if _profile.data:
                        user_name = _profile.data[0].get("display_name")
                        user_title = _profile.data[0].get("professional_title")
                        if user_name or user_title:
                            print(f"👤 Perfil: {user_name or 'Creador'} ({user_title or 'Experto'}) | tono={job_tone}")
            except Exception as e_profile:
                print(f"   ⚠️ Perfil de usuario no disponible (no fatal): {str(e_profile)[:80]}")

        from services.processor import analyze_with_openrouter
        result = analyze_with_openrouter(
            transcript, video_info,
            tone=job_tone, user_name=user_name, user_title=user_title,
        )
        update_job_progress(job_id, progress_percentage=65)
        check_timeout()  # C5
        
        # Step 3.5: Quality Filter - Validate durations
        # Phase 1.1: surgical_clipping is now migrated to flat start_time/end_time
        # inside processor.py BEFORE Pydantic validation, so timestamps are
        # guaranteed to be populated here (the workaround that used to live
        # in this step has been removed).
        # Fase 3: el truncado a 60s ahora snapea al fin de frase del transcript.
        print("\n🔍 Step 3.5: Quality filter...")
        from services.validation import (
            validate_durations,
            filter_overlapping_moments,
            validate_against_transcript,
        )
        result.viral_moments = validate_durations(
            result.viral_moments, min_duration=10, max_duration=60, transcript=transcript
        )
        result.viral_moments = filter_overlapping_moments(result.viral_moments, max_overlap_ratio=0.5)

        if not result.viral_moments:
            raise Exception("No viral moments passed quality filter (all clips too short)")

        print("\n🎯 Step 3.6: Transcript verification (first/last phrase)...")
        for moment in result.viral_moments:
            validate_against_transcript(moment, transcript)

        video_duration = _resolve_video_duration(video_info, transcript, result.viral_moments)
        print(f"📏 Duración efectiva para descarga: {video_duration:.0f}s")
        check_timeout()
        
        # Step 4: Preparar para clipping (estrategias híbridas sync-safe).
        supabase = get_supabase()
        print("\n📹 Step 4: Preparando descarga del video...")
        _log_download_config()
        if _prefer_rapidapi_download() and not os.getenv("RAPIDAPI_KEY"):
            raise RuntimeError(
                "RAPIDAPI_KEY no configurada en el worker. "
                "Agregala en ~/viralengine/.env y reinicia: "
                "docker compose -f docker-compose.worker.yml up -d --build"
            )

        download_strategy = _select_download_strategy(video_duration, result.viral_moments)
        _log_download_strategy(download_strategy, video_duration, result.viral_moments)
        update_job_progress(job_id, current_step="downloading", progress_percentage=70)

        stream_urls = None
        muxed_video_path = None
        clip_paths_cache: dict[int, ClipDownloadResult] = {}
        partial_download_failed = False
        download_t0 = time.time()
        download_mb = 0.0
        download_failed_clips = 0

        if download_strategy == "full_ytdlp":
            try:
                print("🎬 Estrategia full_ytdlp: descarga completa vía yt-dlp...")
                muxed_video_path = _download_video_ytdlp(video_url, video_id)
                download_mb = Path(muxed_video_path).stat().st_size / (1 << 20)
                print(f"✅ yt-dlp full: {download_mb:.0f}MB en {time.time() - download_t0:.0f}s")
            except Exception as e_ytdlp_full:
                print(f"⚠️ full_ytdlp falló: {str(e_ytdlp_full)[:200]} — cayendo a upfront_partial")
                download_strategy = "upfront_partial"

        if download_strategy == "upfront_partial" and not muxed_video_path:
            try:
                stream_urls = get_stream_urls(video_url, video_id)
            except Exception as e:
                print(f"ℹ️ Stream URLs no disponibles ({e}) — fallback per-clip en Step 5")
                partial_download_failed = True

            if stream_urls:
                try:
                    from services.downloader import prepare_muxed_video_from_streams
                    max_end = max(
                        float(m.end_time) for m in result.viral_moments
                        if m.end_time is not None
                    )
                    print(f"\n📥 upfront_partial: 0→{max_end:.0f}s (1 descarga)...")
                    muxed_video_path = prepare_muxed_video_from_streams(
                        video_url=stream_urls["video_url"],
                        audio_url=stream_urls["audio_url"],
                        max_end_sec=max_end,
                        video_duration=video_duration,
                        video_id=video_id,
                        resolve_proxy=stream_urls.get("resolve_proxy"),
                    )
                    download_mb = Path(muxed_video_path).stat().st_size / (1 << 20)
                    print(f"✅ Video muxeado listo: {download_mb:.0f}MB")
                except Exception as e_partial:
                    print(f"⚠️ upfront_partial falló: {e_partial}")
                    muxed_video_path = None
                    partial_download_failed = True

        elif download_strategy == "per_clip_parallel":
            muxed_video_path = None
            total_clips = sum(
                1 for m in result.viral_moments
                if m.start_time is not None and m.end_time is not None
            )

            def _on_parallel_clip_done(done: int, total: int) -> None:
                pct = 70 + int((done / max(total, 1)) * 10)
                update_job_progress(
                    job_id,
                    current_step="downloading",
                    progress_percentage=min(80, pct),
                )
                print(f"   📥 Descargando clip {done}/{total}...")

            clip_paths_cache = download_clips_parallel(
                youtube_url=video_url,
                moments=result.viral_moments,
                video_id=video_id,
                video_duration=video_duration,
                deadline=time.time() + _download_phase_budget_sec(),
                on_clip_done=_on_parallel_clip_done,
            )
            download_failed_clips = total_clips - len(clip_paths_cache)
            download_mb = sum(
                Path(r.path).stat().st_size for r in clip_paths_cache.values()
            ) / (1 << 20)
            if not clip_paths_cache:
                print("⚠️ per_clip_parallel no descargó ningún clip — Step 5 reintentará individual")
                partial_download_failed = True

        record_download_usage(
            strategy=download_strategy,
            download_seconds=time.time() - download_t0,
            download_mb=download_mb,
            clips_count=len(result.viral_moments),
            failed_clips=download_failed_clips,
            metadata={
                "muxed": bool(muxed_video_path),
                "parallel_cached": len(clip_paths_cache),
            },
        )
        check_timeout()
        print("\n💾 Step 5: Saving results...")
        update_job_progress(job_id, current_step="generating", progress_percentage=85)
        
        # Summary is now stored in jobs table, not content_results
        # save_content_result() is only for actual content pieces (twitter, tiktok, etc.)

        
        clips_rendered_count = 0

        # Process each viral moment
        for i, moment in enumerate(result.viral_moments):
            check_timeout()
            moment_index = i + 1
            bind_trace(moment_index=moment_index, phase="clip")
            set_moment_index(moment_index)
            clip_url = None
            # Plan C: cache para acelerar re-renders del editor post-clip.
            # Se popula tras yt-dlp + Whisper; queda en None si caemos a paths
            # de fallback (entonces el editor re-descargará desde YouTube).
            raw_clip_url_cache = None
            whisper_words_cache = None
            # Fase 4: métricas de calidad del clip (persisten en content_results)
            clip_text_final = None      # texto real del clip (whisper o slice YT)
            verification_info = None    # dict de verify_phrases_against_whisper
            coverage_val = None         # cobertura de subs 0-1
            wps_val = None              # words per second
            judge_scores = None         # scores del juez independiente
            roi_clip_duration = None    # duración usada para el ROI determinístico
            clip_quality_issues = []    # flags de calidad (incomplete_tail, etc.)
            clip_generation_error = None
            clip_rendered_ok = False

            # Generate clip (Fase 1.6 — orden invertido):
            #   1. PRIMARY: usar muxed_video_path (partial download ya hecha upfront)
            #   2. BACKUP: yt-dlp download_ranges por clip (puede recuperarse con
            #      player_client=[tv,ios,web])
            #   3. FALLBACK final: YouTube deep-link (sin MP4)
            if moment.start_time is not None and moment.end_time is not None:
                sync_retries = _clip_sync_retries()
                strict_sync = _strict_sync_validation()
                for sync_attempt in range(sync_retries + 1):
                    try:
                        if sync_attempt > 0:
                            print(
                                f"   🔄 Reintento sync {sync_attempt}/{sync_retries} "
                                f"para clip {moment_index}..."
                            )
                            clip_paths_cache.pop(moment_index, None)

                        start_s = float(moment.start_time)
                        end_s = float(moment.end_time)
                        print(f"\n✂️ Generando clip MP4 {moment_index} ({int(start_s)}-{int(end_s)}s)...")

                        clip_output = CLIPS_DIR / f"{video_id}_moment_{moment_index}.mp4"
                        clip_output.parent.mkdir(parents=True, exist_ok=True)
                        overlay_text = getattr(moment, 'viral_overlay', None)
                        if not overlay_text:
                            tp = getattr(moment, 'tiktok_package', None)
                            overlay_text = getattr(tp, 'overlay_text', None) if tp else None

                        seg_path = None
                        src_path, src_start, src_end, src_offset, seg_path = _resolve_moment_video_source(
                            moment_index=moment_index,
                            start_s=start_s,
                            end_s=end_s,
                            video_url=video_url,
                            video_id=video_id,
                            video_duration=video_duration,
                            muxed_video_path=muxed_video_path,
                            clip_paths_cache=clip_paths_cache,
                            partial_download_failed=partial_download_failed,
                            sync_attempt=sync_attempt,
                        )

                        # ── Pre-corte frame-accurate (seek DESPUÉS de -i) ─────────────
                        # Whisper y el encode final usan el MISMO archivo → subs en sync.
                        clip_duration = src_end - src_start
                        precut_path = DOWNLOADS_DIR / f"{video_id}_m{moment_index}_precut.mp4"
                        print(f"   ✂️ Pre-corte preciso ({clip_duration:.1f}s)...")
                        cut_clip(
                            video_path=src_path,
                            start_sec=src_start,
                            end_sec=src_end,
                            output_path=str(precut_path),
                        )
                        if not seg_path:
                            seg_path = str(precut_path)

                        # ── Whisper sobre el clip ya cortado (timestamps 0..duration) ─
                        clip_words = None
                        clip_segments_whisper = None
                        clip_audio_path = None
                        whisper_vocab: list[str] = []
                        try:
                            from services.transcriber import transcribe_with_whisper_openrouter
                            clip_audio_path = DOWNLOADS_DIR / f"{video_id}_clip_{moment_index}_audio.mp3"
                            extract_whisper_audio(str(precut_path), str(clip_audio_path))

                            whisper_prompt = None
                            prompt_parts = []
                            video_title = (video_info.get("title") or "").strip()
                            if video_title:
                                prompt_parts.append(video_title)
                            yt_segments = transcript.get("segments") or []
                            ctx_texts = []
                            for sg in yt_segments:
                                sg_start = float(sg.get("start", 0))
                                sg_end = float(sg.get("end", 0))
                                if sg_end >= start_s and sg_start <= end_s:
                                    ctx_texts.append(sg.get("text", "").strip())
                            yt_slice = " ".join(ctx_texts).strip()
                            if ctx_texts:
                                prompt_parts.append(yt_slice)
                            from services.transcriber import (
                                build_whisper_vocabulary,
                                format_whisper_vocabulary_prompt,
                            )
                            whisper_vocab = build_whisper_vocabulary(
                                video_title=video_title,
                                hook=moment.hook or "",
                                yt_slice=yt_slice,
                            )
                            vocab_prompt = format_whisper_vocabulary_prompt(whisper_vocab)
                            if vocab_prompt:
                                prompt_parts.insert(0, vocab_prompt)
                            if prompt_parts:
                                whisper_prompt = ". ".join(prompt_parts)
                                if len(whisper_prompt) > 800:
                                    whisper_prompt = whisper_prompt[:800]

                            whisper_lang = transcript.get("language")
                            if whisper_lang and len(whisper_lang) > 2:
                                whisper_lang = whisper_lang.split("-")[0].lower()

                            print(f"   🎙️ Transcribiendo clip {moment_index} con Whisper "
                                  f"(lang={whisper_lang}, prompt={len(whisper_prompt or '')} chars)...")
                            clip_tr = transcribe_with_whisper_openrouter(
                                str(clip_audio_path),
                                prompt=whisper_prompt,
                                language=whisper_lang,
                            )
                            raw_words = clip_tr.get("words") or []
                            raw_segments = clip_tr.get("segments") or []
                            clip_words = filter_whisper_words(raw_words, clip_duration)
                            clip_words = fix_ghost_leading_words(clip_words)
                            from services.clip_generator import apply_whisper_brand_corrections
                            clip_words = apply_whisper_brand_corrections(clip_words, whisper_vocab)

                            clip_segments_whisper = []
                            for sg in raw_segments:
                                ss = float(sg.get("start", 0))
                                se = float(sg.get("end", ss + 0.1))
                                if se <= 0 or ss >= clip_duration + 0.25:
                                    continue
                                sg2 = dict(sg)
                                sg2["start"] = max(0.0, ss)
                                sg2["end"] = min(clip_duration + 0.10, se)
                                clip_segments_whisper.append(sg2)

                            n_raw = len(raw_words)
                            n_kept = len(clip_words)
                            retention = (n_kept / n_raw * 100) if n_raw else 0
                            words_per_sec = (n_kept / clip_duration) if clip_duration > 0 else 0
                            print(f"   ✅ Whisper: {n_kept}/{n_raw} words ({retention:.0f}%), "
                                  f"{len(clip_segments_whisper)}/{len(raw_segments)} segments "
                                  f"(clip_duration={clip_duration:.1f}s, density={words_per_sec:.2f} w/s)")

                            if n_kept == 0:
                                print(f"   🔄 Whisper devolvió 0 words — fallback a YT transcript")
                                clip_words = None
                                clip_segments_whisper = None
                        except Exception as e_whisper:
                            print(f"   ⚠️ Whisper per-clip falló ({e_whisper}) — "
                                  f"fallback a YT Transcript API")
                            clip_words = None
                            clip_segments_whisper = None
                        finally:
                            if clip_audio_path:
                                try:
                                    Path(clip_audio_path).unlink(missing_ok=True)
                                except Exception:
                                    pass

                        if clip_words or clip_segments_whisper:
                            subs_segments = clip_segments_whisper
                            subs_words = clip_words
                            subs_offset = 0.0
                            snap_trim_start = 0.0
                            incomplete_tail = False
                            late_hook = False

                            # Fase A: snap trim silencio + refinamiento a oración
                            from services.validation import (
                                verify_phrases_after_snap,
                                find_phrase_start_in_words,
                                find_hook_start_in_words,
                                hook_keyword_overlap,
                            )
                            from services.clip_generator import (
                                refine_bounds_to_sentences,
                                has_incomplete_tail,
                                detect_sentence_boundaries,
                            )
                            trim_start, trim_end = snap_trim_bounds(clip_words, clip_duration)
                            tail_snapped_by_sentence = False

                            # Fase 3: límites a boundaries de oración (puntuación +
                            # gaps >0.6s + fin de segmentos Whisper)
                            s_start, s_end = refine_bounds_to_sentences(
                                clip_words, clip_duration,
                                segments=clip_segments_whisper,
                                max_duration=60.0,
                            )
                            if s_start > trim_start:
                                print(f"   📝 Sentence snap start: {trim_start:.2f} → {s_start:.2f}")
                                trim_start = s_start
                            if s_end < trim_end:
                                print(f"   📝 Sentence snap end: {trim_end:.2f} → {s_end:.2f}")
                                trim_end = s_end
                                tail_snapped_by_sentence = True

                            # Hook anchor: overlay > hook > first_phrase (después de sentence snap)
                            _overlay = getattr(moment, "viral_overlay", None) or ""
                            _first_phrase = getattr(
                                getattr(moment, "verification", None),
                                "first_phrase_in_audio", None,
                            )
                            hook_anchor = find_hook_start_in_words(
                                clip_words,
                                hook=moment.hook or "",
                                overlay=_overlay,
                                first_phrase=_first_phrase or "",
                                clip_duration=clip_duration,
                            )
                            if hook_anchor is not None:
                                new_start = max(0.0, hook_anchor - 0.2)
                                if (
                                    0.5 <= hook_anchor <= clip_duration * 0.4
                                    and (trim_end - new_start) >= 8.0
                                    and new_start > trim_start
                                ):
                                    print(
                                        f"   🎯 Hook anchor: {hook_anchor:.2f}s — "
                                        f"inicio {trim_start:.2f} → {new_start:.2f}"
                                    )
                                    trim_start = new_start
                            elif moment.hook and len(clip_words) >= 3:
                                head_tokens = [
                                    (w.get("word") or "").strip()
                                    for w in clip_words[:3]
                                ]
                                if hook_keyword_overlap(head_tokens, moment.hook) < 0.2:
                                    bounds = detect_sentence_boundaries(
                                        clip_words, clip_segments_whisper
                                    )
                                    alt = [b for b in bounds if 2.0 < b <= clip_duration * 0.35]
                                    if alt:
                                        remaining = [
                                            w for w in clip_words
                                            if float(w.get("start", 0)) > alt[0]
                                        ]
                                        if remaining and (trim_end - float(remaining[0]["start"])) >= 8.0:
                                            new_start = max(0.0, float(remaining[0]["start"]) - 0.15)
                                            print(
                                                f"   🧹 Head filler trim: {trim_start:.2f} → {new_start:.2f}"
                                            )
                                            trim_start = new_start

                            # First-phrase anchor (fallback si hook anchor no corrió)
                            if _first_phrase and trim_start < 0.5:
                                anchor_t = find_phrase_start_in_words(clip_words, _first_phrase)
                                if (
                                    anchor_t is not None
                                    and anchor_t - trim_start > 0.8
                                    and anchor_t < clip_duration * 0.5
                                    and (trim_end - anchor_t) >= 8.0
                                ):
                                    print(
                                        f"   ⚓ First-phrase anchor: frase en "
                                        f"{anchor_t:.2f}s — inicio "
                                        f"{trim_start:.2f} → {max(0.0, anchor_t - 0.35):.2f}"
                                    )
                                    trim_start = max(0.0, anchor_t - 0.35)

                            if trim_end - trim_start < 3.0:
                                trim_start, trim_end = 0.0, clip_duration

                            if trim_start > 0.05 or trim_end < clip_duration - 0.05:
                                snap_trim_start = trim_start
                                words_before_snap = len(clip_words)
                                print(
                                    f"   ✂️ Snap trim: {clip_duration:.1f}s → "
                                    f"{trim_end - trim_start:.1f}s "
                                    f"(start={trim_start:.2f}, end={trim_end:.2f})"
                                )
                                snapped_path = DOWNLOADS_DIR / f"{video_id}_m{moment_index}_snapped.mp4"
                                try:
                                    cut_clip(
                                        video_path=str(precut_path),
                                        start_sec=trim_start,
                                        end_sec=trim_end,
                                        output_path=str(snapped_path),
                                    )
                                    precut_path = snapped_path
                                    clip_duration = trim_end - trim_start
                                    clip_words = shift_words_timeline(
                                        clip_words, trim_start, clip_duration=clip_duration
                                    )
                                    clip_words = fix_ghost_leading_words(clip_words)
                                    clip_words = filter_whisper_words(clip_words, clip_duration)
                                    print(
                                        f"   📊 Snap words: {words_before_snap} → "
                                        f"{len(clip_words)} (after shift+filter)"
                                    )
                                    clip_segments_whisper = [
                                        {
                                            **sg,
                                            "start": max(0.0, float(sg["start"]) - trim_start),
                                            "end": max(0.0, float(sg["end"]) - trim_start),
                                        }
                                        for sg in clip_segments_whisper
                                        if float(sg.get("end", 0)) > trim_start
                                        and float(sg.get("start", 0)) < trim_end
                                    ]
                                    subs_words = clip_words
                                    subs_segments = clip_segments_whisper
                                except ClipGenerationError as e_snap:
                                    print(f"   ⚠️ Snap trim falló ({e_snap}) — continuando sin snap")
                                    snap_trim_start = 0.0
                                    try:
                                        snapped_path.unlink(missing_ok=True)
                                    except Exception:
                                        pass

                            # Post-snap: cola incompleta (omitir si sentence snap ya recortó tail)
                            incomplete_tail = has_incomplete_tail(
                                clip_words, tail_already_snapped=tail_snapped_by_sentence
                            )
                            late_hook = snap_trim_start > 3.0

                            # Fase 4: verificación anti-alucinación (frases post-snap)
                            verification_info = verify_phrases_after_snap(
                                moment, clip_words, snap_trim_start, clip_duration
                            )
                            if (
                                verification_info.get("failed")
                                or incomplete_tail
                                or late_hook
                            ):
                                moment.verification_failed = True
                                reasons = []
                                if verification_info.get("failed"):
                                    reasons.append("phrase mismatch")
                                if incomplete_tail:
                                    reasons.append("incomplete tail")
                                if late_hook:
                                    reasons.append("late hook")
                                print(f"   🚩 verification_failed: {', '.join(reasons)}")
                            coverage_val = srt_coverage_metric(clip_words, clip_duration)
                            wps_val = (len(clip_words) / clip_duration) if clip_duration > 0 else 0.0
                            print(f"   📊 Sub coverage: {coverage_val:.0%} | densidad: {wps_val:.2f} w/s")

                            if strict_sync and (
                                verification_info.get("failed")
                                or coverage_val < 0.9
                            ):
                                if sync_attempt < sync_retries:
                                    raise _SyncRetryNeeded(
                                        "phrase/coverage mismatch — re-download"
                                    )
                                raise ClipGenerationError(
                                    "Sync validation failed after retries"
                                )

                            from services.processor import _clip_text_from_words
                            clip_text_final = _clip_text_from_words(clip_words)
                        else:
                            subs_segments = transcript.get("segments")
                            subs_words = None
                            subs_offset = start_s
                            # Sin Whisper: el texto real del clip es el slice del
                            # transcript YT (para pasada B y juez)
                            from services.validation import _words_in_range
                            clip_text_final = _words_in_range(
                                transcript.get("segments") or [], start_s, end_s
                            )

                        roi_clip_duration = clip_duration

                        # ── Pasada B (Fase 2): copy completo desde el texto real ──
                        # Corre ANTES de generate_clip para que el viral_overlay
                        # final (regenerado) sea el que se quema en el video.
                        if clip_text_final and clip_text_final.strip():
                            from services.processor import generate_moment_copy_full
                            from services.content_validators import clean_moment
                            category = getattr(moment, 'category', None) or 'business'
                            _lang = transcript.get("language")
                            copy_ok = generate_moment_copy_full(
                                moment,
                                clip_text_final,
                                category=category,
                                tone=job_tone,
                                language=_lang,
                                user_name=user_name or "Creador",
                                user_title=user_title or "Experto",
                            )
                            moment_dict = moment.model_dump()
                            copy_stats = clean_moment(moment_dict)
                            if copy_ok and (copy_stats.wrong_tweet_count or copy_stats.linkedin_out_of_range):
                                print("   🔄 Copy validation retry (tweet count / LinkedIn length)...")
                                generate_moment_copy_full(
                                    moment, clip_text_final,
                                    category=category, tone=job_tone, language=_lang,
                                    user_name=user_name or "Creador",
                                    user_title=user_title or "Experto",
                                )
                                moment_dict = moment.model_dump()
                                clean_moment(moment_dict)
                            # Sync de vuelta los campos limpiados por clean_moment
                            if moment_dict.get("content_pieces"):
                                cp = moment_dict["content_pieces"]
                                if cp.get("twitter_thread"):
                                    moment.content_pieces.twitter_thread = cp["twitter_thread"]
                                if cp.get("linkedin_post"):
                                    moment.content_pieces.linkedin_post = cp["linkedin_post"]
                                if cp.get("tiktok_caption"):
                                    moment.content_pieces.tiktok_caption = cp["tiktok_caption"]
                            if moment_dict.get("viral_overlay"):
                                moment.viral_overlay = moment_dict["viral_overlay"]
                            # El overlay que se quema es el final (post-pasada B)
                            if moment.viral_overlay:
                                overlay_text = moment.viral_overlay

                            # ── Fase 4: juez independiente sobre el clip final ────
                            from services.scorer import judge_moment_scores
                            judge_scores = judge_moment_scores(
                                clip_text_final,
                                hook=moment.hook or "",
                                viral_overlay=moment.viral_overlay or "",
                                category=category,
                                clip_duration_sec=roi_clip_duration or 0.0,
                            )
                            if judge_scores:
                                print(
                                    f"   ⚖️ Judge: hook={judge_scores['hook']} "
                                    f"retention={judge_scores['retention']} "
                                    f"share={judge_scores['shareability']}"
                                )

                        # Cache post-snap precut so re-edits align with whisper_words (0-based).
                        raw_cache_path = str(precut_path) if precut_path else seg_path
                        if raw_cache_path and Path(raw_cache_path).exists():
                            try:
                                from services.supabase_client import upload_raw_clip_to_storage
                                raw_clip_url_cache = upload_raw_clip_to_storage(
                                    raw_cache_path, job_id, moment_index
                                )
                                print(
                                    f"   💾 Raw cache subido "
                                    f"({'post-snap' if raw_cache_path == str(precut_path) else 'segment'})"
                                )
                            except Exception as e_cache:
                                print(f"   ⚠️ Cache raw clip falló (no fatal): {e_cache}")

                        gen_result = generate_clip(
                            video_path=str(precut_path),
                            start_sec=0.0,
                            end_sec=clip_duration,
                            output_path=str(clip_output),
                            segments=subs_segments,
                            segments_start_offset_sec=subs_offset,
                            words=subs_words,
                            subtitle_style="tiktok_viral",
                            overlay_text=overlay_text,
                            overlay_style="tiktok_viral",
                            target_width=720,
                            target_height=1280,
                        )
                        print(f"✅ Clip generado en {gen_result.total_time_sec}s, {gen_result.final.size_mb:.1f}MB")
                        print(f"📤 Subiendo clip {moment_index} a R2...")
                        clip_url = upload_clip_to_storage(str(clip_output), job_id, moment_index)
                        if clip_url:
                            print(f"✅ Clip subido: {clip_url[:70]}...")
                            clip_rendered_ok = True
                            clips_rendered_count += 1
                            if clip_words or clip_segments_whisper:
                                whisper_words_cache = {
                                    "words": clip_words or [],
                                    "segments": clip_segments_whisper or [],
                                    "duration_sec": clip_duration,
                                    "snap_trim_start": snap_trim_start,
                                }
                        else:
                            raise RuntimeError("upload_clip_to_storage devolvió None")
                        break
                    except _SyncRetryNeeded as e_sync:
                        print(f"   ⚠️ {e_sync}")
                        continue
                    except (ClipGenerationError, Exception) as e:
                        clip_generation_error = str(e)[:500]
                        print(f"⚠️ Clip {moment_index} falló: {e}")
                        whisper_words_cache = None
                        if moment.start_time is not None:
                            clip_url = f"https://www.youtube.com/watch?v={video_id}&t={int(moment.start_time)}s"
                            print(f"🔗 Fallback a link de YouTube: {clip_url}")
                        break
                for tmp in (locals().get("seg_path"), locals().get("precut_path")):
                    if tmp:
                        try:
                            Path(tmp).unlink(missing_ok=True)
                        except Exception:
                            pass
                try:
                    _clip_out = locals().get("clip_output")
                    if _clip_out and Path(_clip_out).exists():
                        Path(_clip_out).unlink()
                except Exception:
                    pass
                gc.collect()
            else:
                # Sin video local — deep link con timestamp
                if moment.start_time is not None:
                    clip_url = f"https://www.youtube.com/watch?v={video_id}&t={int(moment.start_time)}s"
                    print(f"🔗 Clip {moment_index}: YouTube link at {int(moment.start_time)}s → {clip_url}")
            
            # ── Rescate de copy: solo si no hay copy Y el clip no se renderizó;
            # no enmascarar fallos con scores del análisis como finales.
            from services.validation import is_youtube_clip_fallback, build_clip_quality_issues
            if not clip_rendered_ok and is_youtube_clip_fallback(clip_url):
                clip_quality_issues = build_clip_quality_issues(
                    verification_info=verification_info,
                    incomplete_tail=locals().get("incomplete_tail", False),
                    late_hook=locals().get("late_hook", False),
                    clip_not_rendered=True,
                    clip_generation_error=clip_generation_error,
                )
            elif clip_rendered_ok or verification_info:
                clip_quality_issues = build_clip_quality_issues(
                    verification_info=verification_info,
                    incomplete_tail=locals().get("incomplete_tail", False),
                    late_hook=locals().get("late_hook", False),
                    clip_not_rendered=False,
                    clip_generation_error=None,
                )

            if (
                not (moment.content_pieces.twitter_thread or "").strip()
                and moment.start_time is not None
                and moment.end_time is not None
                and not clip_rendered_ok
            ):
                try:
                    from services.validation import _words_in_range
                    from services.processor import generate_moment_copy_full
                    _rescue_text = clip_text_final or _words_in_range(
                        transcript.get("segments") or [],
                        float(moment.start_time), float(moment.end_time),
                    )
                    if _rescue_text and _rescue_text.strip():
                        print("   🛟 Copy rescue: generando copy desde transcript YT (pasada B no corrió)")
                        generate_moment_copy_full(
                            moment, _rescue_text,
                            category=getattr(moment, 'category', None) or 'business',
                            tone=job_tone,
                            language=transcript.get("language"),
                            user_name=user_name or "Creador",
                            user_title=user_title or "Experto",
                        )
                        if not clip_text_final:
                            clip_text_final = _rescue_text
                except Exception as e_rescue:
                    print(f"   ⚠️ Copy rescue falló (no fatal): {str(e_rescue)[:100]}")

            # Extract scores if available
            scores = moment.scores if hasattr(moment, 'scores') and moment.scores else None
            pillar_raw = moment.pillar_type if hasattr(moment, 'pillar_type') else None
            
            # Sanitize pillar_type - extract first valid value
            pillar = None
            if pillar_raw:
                valid_pillars = ['authority', 'utility', 'connection', 'entertainment']
                for p in pillar_raw.lower().replace('|', ' ').split():
                    if p.strip() in valid_pillars:
                        pillar = p.strip()
                        break
            
            # Extract Phase B fields
            sentiment = getattr(moment, 'sentiment_detected', None)
            justifications = None
            if hasattr(moment, 'score_justifications') and moment.score_justifications:
                justifications = [j.model_dump() if hasattr(j, 'model_dump') else j for j in moment.score_justifications]

            # ── Fase 4: ROI determinístico (reemplaza el número alucinado) ──
            from services.scorer import deterministic_roi
            _copy_pieces = sum(
                1 for piece in (
                    moment.content_pieces.twitter_thread,
                    moment.content_pieces.linkedin_post,
                    getattr(moment.content_pieces, 'tiktok_caption', None)
                    or (moment.tiktok_package.caption if getattr(moment, 'tiktok_package', None) else None),
                ) if piece and str(piece).strip()
            )
            _roi_duration = roi_clip_duration
            if _roi_duration is None and moment.start_time is not None and moment.end_time is not None:
                _roi_duration = float(moment.end_time - moment.start_time)
            roi_time = deterministic_roi(_roi_duration or 0.0, _copy_pieces)
            moment.roi_time_saved = roi_time

            # ── Fase 4: scores — el juez (si corrió) es la nota mostrada;
            # los scores del análisis se guardan como score_llm para calibrar.
            score_llm_dict = None
            if scores:
                score_llm_dict = {
                    "hook": scores.hook,
                    "retention": scores.retention,
                    "shareability": scores.shareability,
                }
            display_hook = judge_scores["hook"] if judge_scores else None
            display_retention = judge_scores["retention"] if judge_scores else None
            display_share = judge_scores["shareability"] if judge_scores else None
            # Sin clip renderizado: no mostrar scores LLM inflados como finales
            if clip_rendered_ok and not judge_scores and scores:
                display_hook = scores.hook
                display_retention = scores.retention
                display_share = scores.shareability

            # Phase 1.4: Binary categories — podcast or business (default).
            # Both categories produce the full content package
            # (Twitter + LinkedIn + TikTok caption). We save whatever the
            # prompt actually filled, no category-based gating.
            # Fase 5: entertainment es válida si está habilitada por env.
            category = getattr(moment, 'category', None) or 'business'
            category = category.lower().strip()
            _valid_categories = ('podcast', 'business', 'entertainment') if os.getenv(
                "ENABLE_ENTERTAINMENT_CATEGORY", ""
            ).lower() in ("1", "true", "yes") else ('podcast', 'business')
            if category not in _valid_categories:
                # Legacy values (tech / lifestyle) fall back silently —
                # the business prompt was used anyway in this run.
                category = 'business'

            print(f"\n📊 Category: {category.upper()} — saving full content package")

            # Overlay final: pasada B puede haberlo regenerado — releer del
            # moment (overlay_text solo existe si el path de clip corrió).
            _overlay_final = getattr(moment, 'viral_overlay', None)
            if not _overlay_final:
                _tp = getattr(moment, 'tiktok_package', None)
                _overlay_final = getattr(_tp, 'overlay_text', None) if _tp else None

            # Common kwargs reused for every content_result row
            common_kwargs = dict(
                job_id=job_id,
                clip_url=clip_url,
                start_time=moment.start_time,
                end_time=moment.end_time,
                hook=moment.hook,
                emotional_trigger=moment.emotional_trigger,
                moment_index=moment_index,
                pillar_type=pillar,
                score_hook=display_hook,
                score_retention=display_retention,
                score_shareability=display_share,
                sentiment_detected=sentiment,
                roi_time_saved=roi_time,
                score_justifications=justifications,
                viral_overlay=_overlay_final,
                raw_clip_url=raw_clip_url_cache,
                whisper_words=whisper_words_cache,
                # Fase 4: scoring calibrado + métricas de calidad
                score_llm=score_llm_dict,
                score_judge=judge_scores,
                verification_failed=getattr(moment, 'verification_failed', None),
                sub_coverage=coverage_val,
                words_per_sec=wps_val,
                clip_quality_issues=clip_quality_issues or None,
                clip_generation_error=clip_generation_error,
            )

            # Twitter thread — always saved (universal)
            if moment.content_pieces.twitter_thread:
                save_content_result(
                    content_type="twitter_thread",
                    content=moment.content_pieces.twitter_thread,
                    **common_kwargs,
                )

            # LinkedIn post — saved for BOTH categories (podcasters publish there too)
            if moment.content_pieces.linkedin_post:
                save_content_result(
                    content_type="linkedin_post",
                    content=moment.content_pieces.linkedin_post,
                    **common_kwargs,
                )

            # TikTok caption — saved for BOTH categories (universal short-form)
            tiktok_caption = getattr(moment.content_pieces, 'tiktok_caption', None)
            if not tiktok_caption and getattr(moment, 'tiktok_package', None):
                # Fallback: pull from tiktok_package if content_pieces didn't have it
                tiktok_caption = moment.tiktok_package.caption
            if tiktok_caption:
                save_content_result(
                    content_type="tiktok_caption",
                    content=tiktok_caption,
                    **common_kwargs,
                )
        
        # Update status to completed
        update_job_progress(job_id, current_step="completed", progress_percentage=100)
        update_job_status(job_id, "completed")
        total_moments = len(result.viral_moments)
        if clips_rendered_count == 0:
            print(f"\n⚠️ Job {job_id} completado SIN clips MP4 ({total_moments} momentos → fallback YouTube)")
            print(
                "   Descarga falló (403 googlevideo). Revisa en Render: "
                "WEBSHARE_PROXY_FILE, RAPIDAPI_KEY, o activa YTDLP_CLIP_FALLBACK=true"
            )
        else:
            print(f"\n✅ Job {job_id} completed successfully!")
            print(f"   {clips_rendered_count}/{total_moments} clips MP4 + content for {total_moments} moments")
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
        # Si el error fue una desconexión de Supabase, reseteamos el cliente
        # para que update_job_error reconecte en vez de caer al fallback SQLite.
        err_str = str(e).lower()
        if "server disconnected" in err_str or "connection" in err_str:
            reset_supabase()
        update_job_error(job_id, str(e))
        
    finally:
        finalize_job_usage(job_id)
        clear_job_context()
        # C5: Cancel timeout timer
        timeout_timer.cancel()

        # Limpiar video muxeado parcial
        if muxed_video_path:
            try:
                Path(muxed_video_path).unlink(missing_ok=True)
            except Exception:
                pass

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
║     YouTube Viral Content Engine - AI Worker v3.1         ║
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

    # Keepalive: evita que Supabase corte la conexión idle durante análisis largos
    start_keepalive(interval=45)
    
    active_jobs = set()  # Track job IDs currently being processed
    active_edits = set()  # Track clip_edit IDs currently being processed

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}  # future -> ("job", id) or ("edit", id)

        while True:
            try:
                # S2: Determine how many slots are available
                available_slots = max_workers - len(futures)

                if available_slots > 0:
                    # S1: Poll Supabase for pending jobs
                    # Fase 5: tone puede no existir si la migración ai_quality
                    # no corrió — fallback al select viejo.
                    try:
                        result = supabase.table("jobs") \
                            .select("id, video_url, user_id, tone") \
                            .eq("status", "pending") \
                            .order("created_at") \
                            .limit(available_slots) \
                            .execute()
                    except Exception:
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

                            # Atomically claim the job — the WHERE status='pending'
                            # prevents double-claiming in multi-instance deployments.
                            # We verify the response actually modified a row before
                            # submitting to the executor.
                            claim = supabase.table("jobs") \
                                .update({"status": "processing"}) \
                                .eq("id", job["id"]) \
                                .eq("status", "pending") \
                                .execute()

                            if not claim.data:
                                # Another worker claimed it first — skip silently
                                print(f"⚠️ Job {job['id']} ya fue reclamado por otra instancia, saltando")
                                continue

                            job_data = {
                                "id": job["id"],
                                "videoUrl": job["video_url"],
                                "userId": job.get("user_id"),
                                "tone": job.get("tone"),
                            }

                            active_jobs.add(job["id"])
                            future = executor.submit(process_job, job_data)
                            futures[future] = ("job", job["id"])

                # ── Phase 3: Poll clip_edits queue (post-clip re-render) ─────
                # Procesamos como máximo 1 edit por iteración para no monopolizar
                # los slots cuando hay jobs principales pendientes.
                if (max_workers - len(futures)) > 0:
                    edit = claim_next_clip_edit()
                    if edit and edit["id"] not in active_edits:
                        active_edits.add(edit["id"])
                        future = executor.submit(process_clip_edit, edit)
                        futures[future] = ("edit", edit["id"])

                # Check for completed futures
                done_futures = [f for f in futures if f.done()]
                for future in done_futures:
                    kind, fid = futures.pop(future)
                    if kind == "job":
                        active_jobs.discard(fid)
                    else:
                        active_edits.discard(fid)
                    try:
                        future.result()  # Raise any exceptions
                    except Exception as e:
                        print(f"❌ {kind} {fid} failed: {e}")

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
