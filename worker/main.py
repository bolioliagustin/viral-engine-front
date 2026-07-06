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

from services.downloader import (
    download_audio,
    download_video,
    get_stream_urls,
    download_clip_ytdlp,
    download_clip_via_stream_urls,
    download_video_for_clips,
    extract_segment_copy,
    is_ytdlp_drm_error,
    cleanup_all,
)
# _download_video_ytdlp es interno pero lo usamos como primer intento del
# pipeline de descarga: si yt-dlp logra bajar el video completo (audio+video
# mergeados) en un solo MP4, evitamos por completo el problema del proxy
# residencial throttleando audio (30 KB/s) en la descarga parcial split.
from services.downloader import _download_video_ytdlp
from services.processor import analyze_with_gemini, cleanup_uploaded_file
from services.clipper import extract_clip, cleanup_clips
from services.clip_generator import generate_clip, ClipGenerationError
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


def _should_try_full_ytdlp_download(video_duration: float) -> bool:
    """En VPS con RapidAPI o videos largos, saltar yt-dlp full (OOM/bandwidth)."""
    if _prefer_rapidapi_download():
        return False
    if video_duration > FULL_YTDLP_MAX_DURATION_SEC:
        print(
            f"ℹ️ Video >{FULL_YTDLP_MAX_DURATION_SEC // 60}min "
            f"({video_duration:.0f}s) — omitiendo yt-dlp full, usando partial download"
        )
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
    """yt-dlp per-clip está roto en DRM — nunca en prod ni si hay RapidAPI."""
    if _prefer_rapidapi_download():
        return False
    return True


def _log_download_config() -> None:
    rapid = os.getenv("RAPIDAPI_KEY")
    print(
        f"🔧 Download config: ENV={os.getenv('ENVIRONMENT', 'development')} | "
        f"USE_RAPIDAPI={os.getenv('USE_RAPIDAPI_DOWNLOAD', 'false')} | "
        f"RAPIDAPI_KEY={'SET' if rapid else '❌ MISSING'} | "
        f"prefer_rapidapi={_prefer_rapidapi_download()} | "
        f"yt_dlp_clips={_should_use_ytdlp_for_clips()}"
    )


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
        
        # Step 3.5: Quality Filter - Validate durations
        # Phase 1.1: surgical_clipping is now migrated to flat start_time/end_time
        # inside processor.py BEFORE Pydantic validation, so timestamps are
        # guaranteed to be populated here (the workaround that used to live
        # in this step has been removed).
        print("\n🔍 Step 3.5: Quality filter...")
        from services.validation import validate_durations
        result.viral_moments = validate_durations(result.viral_moments, min_duration=10)

        if not result.viral_moments:
            raise Exception("No viral moments passed quality filter (all clips too short)")

        video_duration = _resolve_video_duration(video_info, transcript, result.viral_moments)
        print(f"📏 Duración efectiva para descarga: {video_duration:.0f}s")
        check_timeout()
        # TODO: Implement in Sprint 3
        # print("\n🎯 Step 3.6: Whisper validation (transcript verification)...")
        # from services.validation import validate_against_transcript
        # for moment in result.viral_moments:
        #     validate_against_transcript(moment, transcript)
        
        # Step 4: Preparar para clipping.
        # Estrategia (Fase 1.6 v3):
        #   1. PRIMARY: yt-dlp full download (audio+video mergeados en 1 MP4).
        #      yt-dlp tiene su propio downloader interno, no usa nuestro
        #      _parallel_download. Suele evadir el throttle de audio del proxy
        #      residencial. Con player_client=[tv,ios,web] funciona en 2025.
        #   2. SECONDARY: partial download (stream URLs split video+audio).
        #      Más rápido EN TEORÍA pero el audio se cuelga 30 KB/s vía proxy.
        #      Lo dejamos como fallback por si yt-dlp se rompe en el futuro.
        #   3. BACKUP per-clip: yt-dlp download_ranges (rápido cuando funciona).
        #   4. FALLBACK final: deep-link de YouTube (sin MP4, mantiene el job vivo).
        supabase = get_supabase()
        print("\n📹 Step 4: Preparando descarga del video...")
        _log_download_config()
        if _prefer_rapidapi_download() and not os.getenv("RAPIDAPI_KEY"):
            raise RuntimeError(
                "RAPIDAPI_KEY no configurada en el worker. "
                "Agregala en ~/viralengine/.env y reinicia: "
                "docker compose -f docker-compose.worker.yml up -d --build"
            )
        update_job_progress(job_id, current_step="clipping", progress_percentage=70)
        stream_urls = None
        video_path = None
        muxed_video_path = None
        # Flag para que si la partial falla en el upfront, no la reintentamos
        # per-clip (cada intento puede comer hasta 8 min del watchdog).
        partial_download_failed = False
        ytdlp_blocked = _prefer_rapidapi_download()  # prod: nunca yt-dlp

        # ── PRIMARY: yt-dlp full download (solo videos cortos, sin USE_RAPIDAPI) ─
        if _should_try_full_ytdlp_download(video_duration):
            try:
                print("🎬 Intentando descarga full vía yt-dlp (audio+video merged)...")
                t_ytdlp = time.time()
                muxed_video_path = _download_video_ytdlp(video_url, video_id)
                size_mb = Path(muxed_video_path).stat().st_size / (1 << 20)
                elapsed = time.time() - t_ytdlp
                print(f"✅ yt-dlp full download: {size_mb:.0f}MB en {elapsed:.0f}s ({size_mb/max(elapsed,0.01):.1f}MB/s)")
            except Exception as e_ytdlp_full:
                if is_ytdlp_drm_error(e_ytdlp_full):
                    ytdlp_blocked = True
                    print(f"⚠️ yt-dlp full bloqueado por DRM — saltando a RapidAPI")
                else:
                    print(f"⚠️ yt-dlp full download falló: {str(e_ytdlp_full)[:200]}")
                print(f"🔄 Cayendo a Plan B: partial download con stream URLs split")
                muxed_video_path = None
        else:
            print("ℹ️ Saltando yt-dlp full — usando Plan B (RapidAPI + partial download)")

        # ── SECONDARY: Stream URLs + partial download ──────────────────────
        # Solo si yt-dlp full falló. Esto es lo que tenemos hoy funcionando
        # parcialmente (video rápido, audio lentísimo vía proxy).
        if not muxed_video_path:
            try:
                stream_urls = get_stream_urls(video_url, video_id)
            except Exception as e:
                if is_ytdlp_drm_error(e):
                    ytdlp_blocked = True
                print(f"ℹ️ Stream URLs no disponibles ({e}), se usará fallback per-clip")

        # PARTIAL DOWNLOAD UPFRONT (Fase 1.6):
        # Descargamos una sola vez ahora para todos los clips, en lugar de
        # reintentar el yt-dlp roto y caer al partial download per-clip.
        if stream_urls and not muxed_video_path:
            try:
                import subprocess as _sp, shutil as _sh
                _ffmpeg = _sh.which("ffmpeg") or "ffmpeg"
                max_end = max(
                    float(m.end_time) for m in result.viral_moments
                    if m.end_time is not None
                )
                print(f"\n📥 Partial download del video (1 sola vez, max_end={max_end:.0f}s)...")
                pvid, paud = download_video_for_clips(
                    video_url=stream_urls["video_url"],
                    audio_url=stream_urls["audio_url"],
                    max_end_sec=max_end,
                    video_duration=video_duration,
                    video_id=video_id,
                )
                muxed_video_path = str(DOWNLOADS_DIR / f"{video_id}_muxed.mp4")
                mux_r = _sp.run(
                    [_ffmpeg, "-y", "-loglevel", "warning",
                     "-i", pvid, "-i", paud, "-c", "copy",
                     "-movflags", "+faststart", muxed_video_path],
                    capture_output=True, text=True, timeout=300
                )
                for pp in [pvid, paud]:
                    try: Path(pp).unlink(missing_ok=True)
                    except Exception: pass
                if mux_r.returncode != 0:
                    raise RuntimeError(mux_r.stderr[-300:])
                size_mb = Path(muxed_video_path).stat().st_size // (1 << 20)
                print(f"✅ Video listo para todos los clips: {size_mb}MB")
            except Exception as e_partial:
                print(f"⚠️ Partial download upfront falló: {e_partial}")
                print(f"🔄 Caeremos a RapidAPI per-clip o YouTube deep-link")
                muxed_video_path = None
                partial_download_failed = True
        elif not muxed_video_path:
            print(f"⚠️ Sin stream_urls — se intentará RapidAPI per-clip")

        check_timeout()
        print("\n💾 Step 5: Saving results...")
        update_job_progress(job_id, current_step="generating", progress_percentage=85)
        
        # Summary is now stored in jobs table, not content_results
        # save_content_result() is only for actual content pieces (twitter, tiktok, etc.)

        
        # Process each viral moment
        for i, moment in enumerate(result.viral_moments):
            check_timeout()
            moment_index = i + 1
            clip_url = None
            # Plan C: cache para acelerar re-renders del editor post-clip.
            # Se popula tras yt-dlp + Whisper; queda en None si caemos a paths
            # de fallback (entonces el editor re-descargará desde YouTube).
            raw_clip_url_cache = None
            whisper_words_cache = None

            # Generate clip (Fase 1.6 — orden invertido):
            #   1. PRIMARY: usar muxed_video_path (partial download ya hecha upfront)
            #   2. BACKUP: yt-dlp download_ranges por clip (puede recuperarse con
            #      player_client=[tv,ios,web])
            #   3. FALLBACK final: YouTube deep-link (sin MP4)
            if moment.start_time is not None and moment.end_time is not None:
                try:
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
                    src_path = None

                    # ── PRIMARY: partial download ya disponible ──────────────────
                    if muxed_video_path and Path(muxed_video_path).exists():
                        src_path = muxed_video_path
                        src_start = start_s
                        src_end = end_s
                        src_offset = start_s
                        print(f"   ✓ Usando video muxeado cacheado")
                    else:
                        seg_path = None
                        # ── BACKUP 1: RapidAPI / stream partial (prod default) ────
                        if _prefer_rapidapi_download() or partial_download_failed:
                            try:
                                print(f"   📥 Descargando clip vía RapidAPI streams...")
                                seg_path = download_clip_via_stream_urls(
                                    youtube_url=video_url,
                                    start_sec=start_s,
                                    end_sec=end_s,
                                    video_duration=video_duration,
                                    video_id=video_id,
                                    temp_id=f"{video_id}_m{moment_index}",
                                    force_rapidapi=True,
                                )
                                src_path = seg_path
                                src_start = 0.0
                                src_end = end_s - start_s
                                src_offset = start_s
                            except Exception as e_stream:
                                print(f"   ⚠️ RapidAPI per-clip falló: {e_stream}")
                                if not _should_use_ytdlp_for_clips():
                                    raise RuntimeError(
                                        f"Sin video para clip {moment_index} — "
                                        f"RapidAPI falló y yt-dlp deshabilitado en producción: {e_stream}"
                                    ) from e_stream

                        # ── BACKUP 2: yt-dlp per-clip (solo dev, sin RapidAPI) ──
                        if not seg_path and _should_use_ytdlp_for_clips():
                            try:
                                seg_out = str(DOWNLOADS_DIR / f"{video_id}_seg_{int(start_s)}")
                                seg_path = download_clip_ytdlp(
                                    youtube_url=video_url,
                                    start_sec=start_s,
                                    end_sec=end_s,
                                    output_path=seg_out,
                                )
                                src_path = seg_path
                                src_start = 0.0
                                src_end = end_s - start_s
                                src_offset = start_s
                                print(f"   ✓ yt-dlp per-clip OK (dev fallback)")
                            except Exception as e_ytdlp:
                                if is_ytdlp_drm_error(e_ytdlp):
                                    ytdlp_blocked = True
                                print(f"   ⚠️ yt-dlp per-clip falló: {e_ytdlp}")
                                try:
                                    seg_path = download_clip_via_stream_urls(
                                        youtube_url=video_url,
                                        start_sec=start_s,
                                        end_sec=end_s,
                                        video_duration=video_duration,
                                        video_id=video_id,
                                        temp_id=f"{video_id}_m{moment_index}",
                                        force_rapidapi=True,
                                    )
                                    src_path = seg_path
                                    src_start = 0.0
                                    src_end = end_s - start_s
                                    src_offset = start_s
                                except Exception as e_stream2:
                                    raise RuntimeError(
                                        f"Sin video disponible para clip {moment_index} "
                                        f"(yt-dlp y RapidAPI fallaron: {e_stream2})"
                                    ) from e_stream2

                        if not seg_path:
                            raise RuntimeError(
                                f"Sin video disponible para clip {moment_index} "
                                f"(muxed={'no' if not muxed_video_path else 'sí'}, "
                                f"partial upfront={'falló' if partial_download_failed else 'no'})"
                            )

                    # ── Whisper per-clip: sync perfecto palabra por palabra ────
                    # Extraemos audio del rango del clip y lo transcribimos con
                    # Whisper (word-level timestamps). Cuesta ~$0.006/min de clip.
                    # Fallback a YT Transcript API si Whisper falla.
                    clip_words = None
                    clip_segments_whisper = None
                    clip_audio_path = None
                    try:
                        import subprocess as _sp2, shutil as _sh2
                        from services.transcriber import transcribe_with_whisper_openrouter
                        _ffmpeg2 = _sh2.which("ffmpeg") or "ffmpeg"
                        clip_audio_path = DOWNLOADS_DIR / f"{video_id}_clip_{moment_index}_audio.mp3"

                        # Padding: 0.5s antes y 1.0s después para dar contexto a
                        # Whisper en los bordes (mejora mucho la transcripción
                        # de la primera y última palabra del clip).
                        PRE_PAD = 0.5
                        POST_PAD = 1.0
                        pad_start = max(0.0, src_start - PRE_PAD)
                        actual_pre_pad = src_start - pad_start  # cuánto prepad efectivo
                        pad_end = src_end + POST_PAD  # ffmpeg clampa solo si sobrepasa

                        # ── Audio para Whisper: extraer + normalizar volumen ──
                        # loudnorm: equaliza volumen + comprime dinámica → audio
                        # más consistente para Whisper, mejor accuracy con voz
                        # sobre música/silencios. Single-pass es suficiente.
                        # Bitrate 128k para máxima calidad (Whisper se beneficia).
                        ext_cmd = [
                            _ffmpeg2, "-y", "-loglevel", "error",
                            "-ss", str(pad_start), "-to", str(pad_end),
                            "-i", src_path,
                            "-vn",
                            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                            "-acodec", "libmp3lame",
                            "-ar", "16000", "-ac", "1", "-b:a", "128k",
                            str(clip_audio_path),
                        ]

                        _sp2.run(ext_cmd, check=True, timeout=120,
                                 capture_output=True, text=True)

                        # ── Prompt de contexto para Whisper ──────────────
                        # Whisper usa el prompt como hint para nombres, jerga,
                        # estilo. Construimos:
                        #   1. Título del video (proper nouns, tema general)
                        #   2. Texto del YT transcript en el rango del clip
                        # Cap a ~800 chars (224 tokens). Tomamos el inicio (no
                        # el final) porque el inicio tiende a tener el contexto
                        # más relevante para el clip.
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
                        if ctx_texts:
                            prompt_parts.append(" ".join(ctx_texts).strip())

                        if prompt_parts:
                            whisper_prompt = ". ".join(prompt_parts)
                            # Tomar el INICIO (más relevante que el final)
                            if len(whisper_prompt) > 800:
                                whisper_prompt = whisper_prompt[:800]

                        whisper_lang = transcript.get("language")  # "es", "en", etc.
                        if whisper_lang and len(whisper_lang) > 2:
                            # YT da codigos tipo "es-419" — Whisper quiere "es"
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

                        # ── Filtrar hallucinations conocidas de Whisper ────
                        # Whisper aprendió del dataset de YouTube y suele
                        # alucinar estos textos cuando hay silencio/música:
                        WHISPER_HALLUCINATIONS = {
                            "amara", "amara.org", "subtítulos por la comunidad",
                            "subtitles by the amara.org community",
                            "subtítulos realizados por la comunidad",
                            "thanks for watching", "thank you for watching",
                            "gracias por ver", "like and subscribe",
                            "suscríbete al canal",
                        }

                        def _is_hallucination(w_obj):
                            txt = (w_obj.get("word") or "").strip().lower()
                            return any(h in txt for h in WHISPER_HALLUCINATIONS)

                        # También dropeamos palabras con duración anómala
                        # (>5s una sola palabra es Whisper rindiéndose y
                        # marcando un período de silencio como una palabra).
                        MAX_WORD_DURATION = 5.0

                        # Shift por el prepad: los timestamps de Whisper están
                        # relativos al audio extraído; restamos actual_pre_pad
                        # para que queden relativos al inicio del clip real.
                        # Damos un poco de tolerancia (0.5s) al final para que
                        # palabras dichas justo en el corte no se pierdan.
                        clip_duration = src_end - src_start
                        END_TOLERANCE = 0.5  # palabras al final del clip
                        clip_words = []
                        dropped_for_diag = []
                        n_hallucinations = 0
                        n_long_word = 0
                        for w in raw_words:
                            if _is_hallucination(w):
                                n_hallucinations += 1
                                continue
                            raw_dur = float(w.get("end", 0)) - float(w.get("start", 0))
                            if raw_dur > MAX_WORD_DURATION:
                                n_long_word += 1
                                continue
                            raw_ws = float(w.get("start", 0))
                            raw_we = float(w.get("end", raw_ws + 0.1))
                            ws = raw_ws - actual_pre_pad
                            we = raw_we - actual_pre_pad
                            # Skip SOLO si la palabra está totalmente fuera
                            # del clip (incluyendo tolerancia al final).
                            if we <= 0:
                                dropped_for_diag.append((w.get("word"), raw_ws, raw_we, "before"))
                                continue
                            if ws >= clip_duration + END_TOLERANCE:
                                dropped_for_diag.append((w.get("word"), raw_ws, raw_we, "after"))
                                continue
                            w2 = dict(w)
                            w2["start"] = max(0.0, ws)
                            w2["end"] = min(clip_duration, we)
                            # Skip words with inverted timestamps after clamping
                            # (Whisper bug: occasionally returns start > end).
                            if w2["start"] >= w2["end"]:
                                dropped_for_diag.append((w.get("word"), raw_ws, raw_we, "inverted"))
                                continue
                            # Garantizar duración mínima de 30ms (algunas
                            # palabras tras el clamp pueden quedar en 0).
                            if w2["end"] - w2["start"] < 0.03:
                                w2["end"] = min(clip_duration, w2["start"] + 0.05)
                            clip_words.append(w2)

                        clip_segments_whisper = []
                        for sg in raw_segments:
                            ss = float(sg.get("start", 0)) - actual_pre_pad
                            se = float(sg.get("end", ss + 0.1)) - actual_pre_pad
                            if se <= 0 or ss >= clip_duration + END_TOLERANCE:
                                continue
                            sg2 = dict(sg)
                            sg2["start"] = max(0.0, ss)
                            sg2["end"] = min(clip_duration, se)
                            clip_segments_whisper.append(sg2)

                        n_raw = len(raw_words)
                        n_kept = len(clip_words)
                        retention = (n_kept / n_raw * 100) if n_raw else 0

                        words_per_sec = (n_kept / clip_duration) if clip_duration > 0 else 0

                        print(f"   ✅ Whisper: {n_kept}/{n_raw} words ({retention:.0f}%), "
                              f"{len(clip_segments_whisper)}/{len(raw_segments)} segments "
                              f"(pre_pad={actual_pre_pad:.2f}s, "
                              f"clip_duration={clip_duration:.1f}s, "
                              f"density={words_per_sec:.2f} w/s)")
                        if n_hallucinations or n_long_word:
                            print(f"   🧹 Filtered: {n_hallucinations} hallucinations, "
                                  f"{n_long_word} long words (>5s)")

                        # ⚠️ Filosofía: confiar en Whisper. Si dice "8 palabras
                        # en 30s", el audio realmente tiene 8 palabras (el resto
                        # es música/silencio/transición). Mostrar esas 8 con
                        # su timing exacto > fabricar subs falsos del YT transcript.
                        #
                        # Solo si Whisper devuelve CERO palabras útiles (n_kept=0)
                        # caemos al YT transcript como último recurso.
                        if n_kept == 0:
                            print(f"   🔄 Whisper devolvió 0 words útiles — "
                                  f"fallback a YT transcript")
                            clip_words = None
                            clip_segments_whisper = None
                    except Exception as e_whisper:
                        print(f"   ⚠️ Whisper per-clip falló ({e_whisper}) — "
                              f"fallback a YT Transcript API")
                        clip_words = None
                        clip_segments_whisper = None
                    finally:
                        if clip_audio_path:
                            try: Path(clip_audio_path).unlink(missing_ok=True)
                            except Exception: pass

                    # Si Whisper OK: usamos sus words/segments con offset=0
                    # (ya son relativos al inicio del clip).
                    # Si falló: caemos al transcript global de YouTube (offset=src_offset).
                    if clip_words or clip_segments_whisper:
                        subs_segments = clip_segments_whisper
                        subs_words = clip_words
                        subs_offset = 0.0
                    else:
                        subs_segments = transcript.get("segments")
                        subs_words = None
                        subs_offset = src_offset

                    # ── Plan C: cachear segmento crudo para re-renders del editor ─
                    if not seg_path and src_path and Path(src_path).exists():
                        try:
                            seg_out = str(DOWNLOADS_DIR / f"{video_id}_seg_{int(start_s)}.mp4")
                            seg_path = extract_segment_copy(src_path, src_start, src_end, seg_out)
                            print(f"   💾 Segmento crudo extraído para cache R2")
                        except Exception as e_seg:
                            print(f"   ⚠️ Extracción segmento cache falló (no fatal): {e_seg}")
                            seg_path = None

                    if seg_path:
                        try:
                            from services.supabase_client import upload_raw_clip_to_storage
                            raw_clip_url_cache = upload_raw_clip_to_storage(
                                seg_path, job_id, moment_index
                            )
                        except Exception as e_cache:
                            print(f"   ⚠️ Cache raw clip falló (no fatal): {e_cache}")

                    if clip_words or clip_segments_whisper:
                        whisper_words_cache = {
                            "words": clip_words or [],
                            "segments": clip_segments_whisper or [],
                        }

                    gen_result = generate_clip(
                        video_path=src_path,
                        start_sec=src_start,
                        end_sec=src_end,
                        output_path=str(clip_output),
                        segments=subs_segments,
                        segments_start_offset_sec=subs_offset,
                        words=subs_words,  # word-level sync (Whisper per-clip)
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
                    else:
                        raise RuntimeError("upload_clip_to_storage devolvió None")
                except (ClipGenerationError, Exception) as e:
                    print(f"⚠️ Clip {moment_index} falló: {e}")
                    if moment.start_time is not None:
                        clip_url = f"https://www.youtube.com/watch?v={video_id}&t={int(moment.start_time)}s"
                        print(f"🔗 Fallback a link de YouTube: {clip_url}")
                finally:
                    # Limpiar segmento yt-dlp (pequeño, por clip)
                    if seg_path:
                        try: Path(seg_path).unlink(missing_ok=True)
                        except Exception: pass
                    # Limpiar clip final
                    try:
                        if clip_output.exists():
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
                valid_pillars = ['authority', 'utility', 'connection', 'entertainment']
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
            
            # Phase 1.4: Binary categories — podcast or business (default).
            # Both categories produce the full content package
            # (Twitter + LinkedIn + TikTok caption). We save whatever the
            # prompt actually filled, no category-based gating.
            category = getattr(moment, 'category', None) or 'business'
            category = category.lower().strip()
            if category not in ('podcast', 'business'):
                # Legacy values (entertainment / tech / lifestyle) fall back
                # silently — the business prompt was used anyway in this run.
                category = 'business'

            print(f"\n📊 Category: {category.upper()} — saving full content package")

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
                score_hook=scores.hook if scores else None,
                score_retention=scores.retention if scores else None,
                score_shareability=scores.shareability if scores else None,
                sentiment_detected=sentiment,
                roi_time_saved=roi_time,
                score_justifications=justifications,
                viral_overlay=overlay_text,
                raw_clip_url=raw_clip_url_cache,
                whisper_words=whisper_words_cache,
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
        # Si el error fue una desconexión de Supabase, reseteamos el cliente
        # para que update_job_error reconecte en vez de caer al fallback SQLite.
        err_str = str(e).lower()
        if "server disconnected" in err_str or "connection" in err_str:
            reset_supabase()
        update_job_error(job_id, str(e))
        
    finally:
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
