"""
viral-engine — worker
Sondea la tabla `jobs` de Supabase (la cola), procesa cada job (transcript,
IA, descarga, clips, copy) y sube los resultados a R2 y Supabase.
"""
import gc
import os
import sys
import json
import time
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import sentry_sdk

# Load environment variables
load_dotenv(Path(__file__).parent.parent / ".env")

# C4 + F1 (docs/PLAN_CALIDAD.md §9 W9-B): sin DSN, Sentry NO se inicializa —
# antes había un DSN hardcodeado como fallback que mandaba errores de
# cualquier fork/dev al proyecto de Sentry de producción aunque
# SENTRY_DSN_WORKER no estuviera seteada (mismo fix que F1 ya hizo en el
# backend, backend/src/app.js).
if os.getenv("SENTRY_DSN_WORKER"):
    sentry_sdk.init(
        dsn=os.getenv("SENTRY_DSN_WORKER"),
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
    get_stream_urls,
    download_clip_ytdlp,
    download_clip_via_stream_urls,
    _clip_margins_sec,
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
from services.clip_generator import (
    cleanup_clips,
    generate_clip,
    ClipGenerationError,
    cut_clip,
    extract_whisper_audio,
    filter_whisper_words,
    snap_trim_bounds,
    shift_words_timeline,
    fix_ghost_leading_words,
    srt_coverage_metric,
    enforce_min_duration,
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
from services.moment_selector import (
    target_moment_count,
    CandidateEval,
    select_finalists,
    score_candidate,
)

DOWNLOADS_DIR = Path(__file__).parent / "downloads"
CLIPS_DIR = Path(__file__).parent / "clips"
POLL_INTERVAL = 3  # seconds between Supabase polls

# W11 (docs/PLAN_CALIDAD.md §9 Fase 1, gap de integración INT-1b): estilo de
# subtítulos por defecto para clips nuevos. generate_clip() ya default-ea a
# "tiktok_viral_v2" pero main.py lo pisaba con el valor viejo "tiktok_viral"
# — el estilo nuevo nunca se usaba en producción pese a estar mergeado.
# Configurable por env por si hace falta volver atrás sin deploy de código.
SUBTITLE_STYLE_DEFAULT = os.getenv("SUBTITLE_STYLE_DEFAULT", "tiktok_viral_v2")

# W9-B (docs/PLAN_CALIDAD.md §9 W9, docs/adr/0008): el clip que se entrega
# de entrada es un PREVIEW liviano, no el HD de siempre — con más clips
# entregados por job (select_finalists ya no limita a 1/3/5), renderizar
# los 720x1280 de todos de una sería demasiado tiempo/CPU. `content_results
# .clip_url` = este preview (compatibilidad: cualquier código que solo lea
# `clip_url` sigue funcionando, aunque en resolución baja) y también se
# guarda en `preview_url` para que la galería (W9-A) lo distinga del HD.
# El HD real se genera a pedido (`clip_edit_processor.py`, edit_type=
# 'hd_upgrade') a partir del raw clip cacheado en R2 (`raw_clip_url`, ya
# se sube siempre, ver más abajo en `_deliver_moment`).
PREVIEW_WIDTH = int(os.getenv("PREVIEW_WIDTH", "480"))
PREVIEW_HEIGHT = int(os.getenv("PREVIEW_HEIGHT", "854"))
PREVIEW_CRF = int(os.getenv("PREVIEW_CRF", "28"))

# W9-B (docs/PLAN_CALIDAD.md §9 W9): contrato de progreso acordado con P1
# (pantalla de progreso nueva, también sobre integracion/fase-0). Enum de
# `jobs.current_step` — el worker es el único que lo escribe:
JOB_STEPS = ("transcribing", "classifying", "analyzing", "evaluating", "ranking", "delivering", "finalizing")


def compute_progress_percentage(
    phase: str,
    *,
    candidate_index: int = 0,
    candidates_total: int = 0,
    delivered_index: int = 0,
    delivered_total: int = 0,
) -> int:
    """
    Progreso 0-100 del job, función pura (sin I/O, testeada en
    tests/test_w9b.py). Cada valor es el PISO (inicio) de la fase que se
    está por entrar — así que reportar `compute_progress_percentage(fase)`
    justo al pasar a esa fase ya marca el techo de la anterior sin
    necesidad de una llamada separada: transcript 0-15, análisis
    (classifying+analyzing) 15-25, evaluación 25-70 lineal por candidato
    evaluado, entrega 70-98 lineal por clip entregado, 100 al finalizar.
    `ranking` (entre evaluar y entregar, sin trabajo por-ítem) es 70, el
    mismo piso con el que arranca `delivering`.

    `candidate_index`/`delivered_index` son cuántos YA se completaron
    (0 antes del primero); con total 0 devuelve el piso de la fase.
    """
    if phase == "transcribing":
        return 0
    if phase in ("classifying", "analyzing"):
        return 15
    if phase == "evaluating":
        if candidates_total <= 0:
            return 25
        frac = min(1.0, max(0.0, candidate_index / candidates_total))
        return round(25 + frac * (70 - 25))
    if phase == "ranking":
        return 70
    if phase == "delivering":
        if delivered_total <= 0:
            return 70
        frac = min(1.0, max(0.0, delivered_index / delivered_total))
        return round(70 + frac * (98 - 70))
    if phase == "finalizing":
        return 100
    raise ValueError(f"fase de progreso desconocida: {phase!r} (ver JOB_STEPS)")


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


def _should_sync_retry_download(coverage_val: float, bad_segment: bool = False) -> bool:
    """Re-descargar solo si Whisper tiene baja cobertura (desfase real de video)
    o si el segmento no tiene habla plausible (`bad_segment`, guarda W3: audio
    desincronizado o segmento vacío → otro proxy/estrategia puede traer el bueno).

    phrase mismatch indica análisis/cache stale, no un clip mal descargado.
    """
    if bad_segment:
        return True
    return coverage_val is not None and coverage_val < 0.9


# Guarda W3: un segmento sin habla se re-descarga UNA sola vez (no las
# CLIP_SYNC_RETRIES de la baja cobertura): si el segundo intento también
# viene vacío, casi seguro el momento es malo y no la descarga.
_BAD_SEGMENT_MAX_RETRIES = 1


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
    muxed_avail_end: float | None = None,
    extend_after_sec: float = 0.0,
    fallback_source: "_MomentSource | None" = None,
) -> "_MomentSource":
    """Resuelve fuente de video para un clip.

    `extend_after_sec` > 0 (W1: la última frase no apareció) exige que la fuente
    llegue hasta end_s + margen_después + extend; un segmento per-clip cacheado
    que no llega se re-descarga con ese margen. `fallback_source` es la última
    fuente que sí funcionó para este momento (antes de un resync W3 que borra
    el cache): si la re-descarga/extensión falla, se usa en vez de perder el
    clip entero.
    """
    _, m_after = _clip_margins_sec()
    needed_end = min(float(video_duration), end_s + m_after + extend_after_sec) if video_duration else end_s + m_after + extend_after_sec
    muxed_end = float(muxed_avail_end) if muxed_avail_end is not None else float(video_duration)

    cached = clip_paths_cache.get(moment_index)
    if (
        cached and Path(cached.path).exists() and sync_attempt == 0
        and (extend_after_sec <= 0 or cached.download_end >= needed_end - 1.0)
    ):
        print(f"   ✓ Usando segmento per-clip cacheado (paralelo)")
        return _MomentSource(
            cached.path,
            start_s - cached.download_start,
            end_s - cached.download_start,
            start_s,
            cached.path,
            cached.download_start,
            cached.download_end,
            "cached",
        )

    if muxed_video_path and Path(muxed_video_path).exists() and (
        extend_after_sec <= 0
        or muxed_end >= needed_end - 1.0
        or not _should_use_ytdlp_for_clips()
    ):
        print(f"   ✓ Usando video muxeado cacheado")
        return _MomentSource(
            muxed_video_path, start_s, end_s, start_s, None, 0.0, muxed_end, "muxed",
        )

    # yt-dlp también se intenta en un reintento (extensión de margen W1 o resync
    # W3) aunque la política prefiera RapidAPI: la descarga per-clip inicial
    # (`download_clips_parallel`) siempre usa yt-dlp sin mirar esta preferencia,
    # así que si ya funcionó para este video, negárselo al reintento solo
    # pierde el clip entero sin alternativa real (caso real: user_recommended_01
    # m=3/m=5, momentos más allá del primer 20% del video, sin proxies
    # residenciales en la Mac de eval).
    if _should_use_ytdlp_for_clips() or extend_after_sec > 0 or sync_attempt > 0:
        try:
            suffix = ""
            if sync_attempt > 0:
                suffix += f"_a{sync_attempt}"
            if extend_after_sec > 0:
                suffix += f"_ext{int(extend_after_sec)}"
            # Stem distinto por reintento/extensión: yt-dlp no re-descarga sobre
            # un archivo existente y devolvería el segmento viejo con rango nuevo.
            seg_out = str(DOWNLOADS_DIR / f"{video_id}_seg_{moment_index}_{int(start_s)}{suffix}")
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
                margin_after_sec=(m_after + extend_after_sec) if extend_after_sec > 0 else None,
                video_duration=video_duration,
                proxy_url=proxy,
            )
            print(f"   ✓ yt-dlp per-clip OK")
            return _MomentSource(
                dl_result.path,
                start_s - dl_result.download_start,
                end_s - dl_result.download_start,
                start_s,
                dl_result.path,
                dl_result.download_start,
                dl_result.download_end,
                "ytdlp",
            )
        except Exception as e_ytdlp:
            print(f"   ⚠️ yt-dlp per-clip falló: {e_ytdlp}")

    if muxed_video_path and Path(muxed_video_path).exists():
        # No se pudo extender por yt-dlp: el muxeado sigue siendo válido
        print(f"   ✓ Usando video muxeado cacheado (sin extensión)")
        return _MomentSource(
            muxed_video_path, start_s, end_s, start_s, None, 0.0, muxed_end, "muxed",
        )

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
            return _MomentSource(
                dl_result.path, 0.0, end_s - start_s, start_s, dl_result.path,
                start_s, end_s, "apify",
            )
        except Exception as e_apify:
            print(f"   ⚠️ Apify per-clip falló: {e_apify}")

    if (
        sync_attempt == 0
        and should_use_stream_urls_fallback(start_s, video_duration)
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
                return _MomentSource(
                    seg_path, 0.0, end_s - start_s, start_s, seg_path,
                    start_s, end_s, "stream",
                )
            except Exception as e_stream:
                last_stream_err = e_stream
                print(f"   ⚠️ Stream partial per-clip falló: {e_stream}")

    # Último recurso: nunca dejar el momento sin video. La extensión de
    # margen (W1, payoff_not_found) y el resync (W3, bad_segment) son mejoras
    # oportunistas, no un requisito — si la re-descarga no está disponible,
    # seguimos con el mejor segmento que ya tenemos en vez de perder el clip
    # entero (caso real: user_recommended_01 m=3/m=5, 108 min, sin proxies
    # residenciales en la Mac de eval, medición W1 2026-09-18).
    if cached and Path(cached.path).exists():
        print(f"   ⚠️ No se pudo ampliar/reintentar la descarga — sigo con el segmento cacheado ya en mano")
        return _MomentSource(
            cached.path,
            start_s - cached.download_start,
            end_s - cached.download_start,
            start_s,
            cached.path,
            cached.download_start,
            cached.download_end,
            "cached",
            insufficient=True,
        )
    if fallback_source is not None and Path(fallback_source.path).exists():
        print(
            f"   ⚠️ No se pudo re-descargar el segmento — sigo con la última "
            f"fuente {fallback_source.kind} disponible para este momento"
        )
        return replace(fallback_source, insufficient=True)

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


# W1: si la última frase no aparece en el segmento, se re-descarga UNA vez con
# este margen extra al final (el remate suele estar poco después del end_time).
_PAYOFF_EXTEND_AFTER_SEC = 25.0


@dataclass
class _MomentSource:
    """Fuente de video resuelta para un momento.

    `src_start`/`src_end` son start_time/end_time en la línea de tiempo del
    archivo; `avail_start_abs`/`avail_end_abs` el rango absoluto del video que
    ese archivo realmente contiene (para armar el segmento ancho de W1).
    """
    path: str
    src_start: float
    src_end: float
    src_offset: float
    seg_path: str | None
    avail_start_abs: float
    avail_end_abs: float
    kind: str   # cached | muxed | ytdlp | apify | stream
    insufficient: bool = False  # True: no se pudo re-descargar/ampliar; es el mejor segmento disponible


def _unlink_quiet(path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


def _moment_phrases(moment) -> tuple[str, str]:
    """(first_phrase_in_audio, last_phrase_in_audio) de la Verificación, o vacías."""
    verification = getattr(moment, "verification", None)
    if not verification:
        return "", ""
    first = (getattr(verification, "first_phrase_in_audio", "") or "").strip()
    last = (getattr(verification, "last_phrase_in_audio", "") or "").strip()
    return first, last


def _log_whisper_assessment(assessment: dict) -> None:
    if assessment.get("plausible"):
        return
    print(
        f"   🩺 Whisper no plausible: {', '.join(assessment['reasons'])} "
        f"(density={assessment['density']:.2f}, "
        f"effective={assessment['effective_density']:.2f}, "
        f"leading_gap={assessment['leading_gap']:.1f}s, "
        f"unique={assessment['unique_words']}/{assessment['n_words']}, "
        f"repeated_run={assessment['repeated_run']})"
    )


def _whisper_clip_words(
    media_path: str,
    duration: float,
    *,
    video_id: str,
    moment_index: int,
    moment,
    transcript: dict,
    video_info: dict,
    ctx_start_abs: float,
    ctx_end_abs: float,
    provider: str | None = None,
    tag: str = "clip",
) -> dict:
    """
    Transcripción del clip: extrae audio de `media_path`, arma el prompt de
    contexto (vocabulario de marca + título + slice del transcript YT del
    rango absoluto [ctx_start_abs, ctx_end_abs]) y llama a Whisper word-level.
    Las palabras quedan en la línea de tiempo de `media_path` (0..duration),
    filtradas y con correcciones de marca. Levanta la excepción de Whisper.

    Returns {"words", "segments", "n_raw_words", "n_raw_segments", "vocab", "provider"}.
    """
    from services.transcriber import (
        transcribe_with_whisper_openrouter,
        build_whisper_vocabulary,
        format_whisper_vocabulary_prompt,
    )
    from services.clip_generator import apply_whisper_brand_corrections

    audio_path = DOWNLOADS_DIR / f"{video_id}_clip_{moment_index}_audio.mp3"
    try:
        extract_whisper_audio(str(media_path), str(audio_path))

        prompt_parts = []
        video_title = (video_info.get("title") or "").strip()
        if video_title:
            prompt_parts.append(video_title)
        ctx_texts = []
        for sg in transcript.get("segments") or []:
            sg_start = float(sg.get("start", 0))
            sg_end = float(sg.get("end", 0))
            if sg_end >= ctx_start_abs and sg_start <= ctx_end_abs:
                ctx_texts.append((sg.get("text") or "").strip())
        yt_slice = " ".join(ctx_texts).strip()
        if ctx_texts:
            prompt_parts.append(yt_slice)
        whisper_vocab = build_whisper_vocabulary(
            video_title=video_title,
            hook=moment.hook or "",
            yt_slice=yt_slice,
        )
        vocab_prompt = format_whisper_vocabulary_prompt(whisper_vocab)
        if vocab_prompt:
            prompt_parts.insert(0, vocab_prompt)
        whisper_prompt = None
        if prompt_parts:
            whisper_prompt = ". ".join(prompt_parts)[:800]

        whisper_lang = transcript.get("language")
        if whisper_lang and len(whisper_lang) > 2:
            whisper_lang = whisper_lang.split("-")[0].lower()

        print(f"   🎙️ Transcribiendo {tag} {moment_index} con Whisper "
              f"(lang={whisper_lang}, prompt={len(whisper_prompt or '')} chars"
              f"{', provider=' + provider if provider else ''})...")
        clip_tr = transcribe_with_whisper_openrouter(
            str(audio_path),
            prompt=whisper_prompt,
            language=whisper_lang,
            provider=provider,
        )
        raw_words = clip_tr.get("words") or []
        raw_segments = clip_tr.get("segments") or []
        words = filter_whisper_words(raw_words, duration)
        words = fix_ghost_leading_words(words)
        words = apply_whisper_brand_corrections(words, whisper_vocab)

        segments = []
        for sg in raw_segments:
            ss = float(sg.get("start", 0))
            se = float(sg.get("end", ss + 0.1))
            if se <= 0 or ss >= duration + 0.25:
                continue
            sg2 = dict(sg)
            sg2["start"] = max(0.0, ss)
            sg2["end"] = min(duration + 0.10, se)
            segments.append(sg2)

        n_raw = len(raw_words)
        n_kept = len(words)
        retention = (n_kept / n_raw * 100) if n_raw else 0
        words_per_sec = (n_kept / duration) if duration > 0 else 0
        print(f"   ✅ Whisper: {n_kept}/{n_raw} words ({retention:.0f}%), "
              f"{len(segments)}/{len(raw_segments)} segments "
              f"(clip_duration={duration:.1f}s, density={words_per_sec:.2f} w/s)")
        return {
            "words": words,
            "segments": segments,
            "n_raw_words": n_raw,
            "n_raw_segments": len(raw_segments),
            "vocab": whisper_vocab,
            "provider": clip_tr.get("provider"),
        }
    finally:
        _unlink_quiet(audio_path)


def _transcribe_with_guards(
    media_path: str,
    duration: float,
    *,
    video_id: str,
    moment_index: int,
    moment,
    transcript: dict,
    video_info: dict,
    ctx_start_abs: float,
    ctx_end_abs: float,
    tag: str = "clip",
) -> dict:
    """
    Transcripción del clip + guardas W3 (assess_whisper_words). Si los
    timestamps son sospechosos, re-transcribe UNA vez con el OTRO proveedor
    (Groq ↔ OpenAI); si el segundo también es sospechoso, devuelve
    subs_disabled_timestamps=True (decisión: sin subtítulos antes que con
    tiempos falsos). Levanta la excepción si la primera transcripción falla.

    Returns {"words", "segments", "provider", "assessment", "bad_segment",
             "timestamps_suspect", "subs_disabled_timestamps"}.
    """
    from services.validation import assess_whisper_words

    common = dict(
        video_id=video_id, moment_index=moment_index, moment=moment,
        transcript=transcript, video_info=video_info,
        ctx_start_abs=ctx_start_abs, ctx_end_abs=ctx_end_abs,
    )
    wtr = _whisper_clip_words(media_path, duration, tag=tag, **common)
    words, segments = wtr["words"], wtr["segments"]
    provider_used = wtr.get("provider")
    assessment = assess_whisper_words(words, duration)
    _log_whisper_assessment(assessment)
    bad_segment = assessment["bad_segment"]
    timestamps_suspect = assessment["timestamps_suspect"]
    subs_disabled = False

    if timestamps_suspect and not bad_segment:
        first = provider_used or ("groq" if os.getenv("GROQ_API_KEY") else "openai")
        other = "openai" if first == "groq" else "groq"
        print(f"   🔁 Re-transcribiendo con {other} (timestamps sospechosos en {first})")
        try:
            wtr2 = _whisper_clip_words(
                media_path, duration, provider=other, tag=f"{tag}/{other}", **common
            )
            assessment2 = assess_whisper_words(wtr2["words"], duration)
            _log_whisper_assessment(assessment2)
            if not assessment2["timestamps_suspect"]:
                words, segments = wtr2["words"], wtr2["segments"]
                provider_used = other
                assessment = assessment2
                timestamps_suspect = False
                bad_segment = assessment2["bad_segment"]
            else:
                subs_disabled = True
        except Exception as e_re:
            print(f"   ⚠️ Re-transcripción con {other} falló ({e_re})")
            subs_disabled = True
        if subs_disabled:
            print("   🚫 Timestamps sospechosos en ambos proveedores — clip sin subtítulos")

    return {
        "words": words,
        "segments": segments,
        "provider": provider_used,
        "assessment": assessment,
        "bad_segment": bad_segment,
        "timestamps_suspect": timestamps_suspect,
        "subs_disabled_timestamps": subs_disabled,
    }


def _refine_bounds_legacy(
    *,
    clip_words: list[dict],
    clip_duration: float,
    clip_segments_whisper: list[dict],
    moment,
    precut_path,
    video_id: str,
    moment_index: int,
    whisper_timestamps_suspect: bool,
) -> dict:
    """
    Refinamiento numérico (pre-W1): snap por silencio → oraciones → ancla de
    hook → relleno inicial → ancla de primera frase → duración mínima (W3), y
    re-corte del precut si cambió algo. Se usa cuando el momento NO trae
    frases de Verificación (jobs legacy / prompt sin verificación).

    Returns dict con precut_path, clip_duration, clip_words,
    clip_segments_whisper, snap_trim_start, tail_snapped_by_sentence,
    min_duration_reverted.
    """
    snap_trim_start = 0.0
    min_duration_reverted = False
    # Fase A: snap trim silencio + refinamiento a oración
    from services.validation import (
        verify_phrases_after_snap,
        find_phrase_start_in_words,
        find_hook_start_in_words,
        hook_keyword_overlap,
        CLIP_MAX_DURATION_SEC,
    )
    from services.clip_generator import (
        refine_bounds_to_sentences,
        has_incomplete_tail,
        detect_sentence_boundaries,
    )
    # Guarda W3: historial de límites para enforce_min_duration
    bound_candidates = [(0.0, clip_duration)]
    trim_start, trim_end = snap_trim_bounds(clip_words, clip_duration)
    bound_candidates.append((trim_start, trim_end))
    tail_snapped_by_sentence = False

    # Fase 3: límites a boundaries de oración (puntuación +
    # gaps >0.6s + fin de segmentos Whisper)
    s_start, s_end = refine_bounds_to_sentences(
        clip_words, clip_duration,
        segments=clip_segments_whisper,
        max_duration=CLIP_MAX_DURATION_SEC,
    )
    if s_start > trim_start:
        print(f"   📝 Sentence snap start: {trim_start:.2f} → {s_start:.2f}")
        trim_start = s_start
    if s_end < trim_end:
        print(f"   📝 Sentence snap end: {trim_end:.2f} → {s_end:.2f}")
        trim_end = s_end
        tail_snapped_by_sentence = True
    bound_candidates.append((trim_start, trim_end))

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
            bound_candidates.append((trim_start, trim_end))
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
                    bound_candidates.append((trim_start, trim_end))

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
            bound_candidates.append((trim_start, trim_end))

    # Guarda W3: con timestamps sospechosos ningún límite
    # derivado de las palabras es confiable → clip entero.
    if whisper_timestamps_suspect and (trim_start, trim_end) != (0.0, clip_duration):
        print(
            f"   ⚠️ Timestamps Whisper sospechosos — ignoro refinamiento "
            f"(start={trim_start:.2f}, end={trim_end:.2f}) y dejo el clip entero"
        )
        trim_start, trim_end = 0.0, clip_duration

    # Guarda W3: duración mínima post-refinamiento (15 s).
    # Si el refinamiento dejó el clip corto, volver al último
    # conjunto de límites que cumplía (o a los originales).
    bound_candidates.append((trim_start, trim_end))
    (trim_start, trim_end), min_duration_reverted = enforce_min_duration(
        bound_candidates
    )
    if min_duration_reverted:
        print(
            f"   ↩️ Duración mínima: refinamiento dejó "
            f"{bound_candidates[-1][1] - bound_candidates[-1][0]:.1f}s — "
            f"revierto a start={trim_start:.2f}, end={trim_end:.2f} "
            f"({trim_end - trim_start:.1f}s)"
        )
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
        except ClipGenerationError as e_snap:
            print(f"   ⚠️ Snap trim falló ({e_snap}) — continuando sin snap")
            snap_trim_start = 0.0
            try:
                snapped_path.unlink(missing_ok=True)
            except Exception:
                pass

    return {
        "precut_path": precut_path,
        "clip_duration": clip_duration,
        "clip_words": clip_words,
        "clip_segments_whisper": clip_segments_whisper,
        "snap_trim_start": snap_trim_start,
        "tail_snapped_by_sentence": tail_snapped_by_sentence,
        "min_duration_reverted": min_duration_reverted,
    }


# ── W2: el juez elige (docs/PLAN_CALIDAD.md §4) ──────────────────────────────
# El loop por momento pasa a tener dos fases: evaluar (barato, sin Pasada B ni
# render) → rankear con el juez → entregar (Pasada B + render solo para los
# finalistas). `_prepare_moment_clip` es la fase de evaluación: resuelve la
# fuente, ancla las frases (W1), transcribe con las guardas (W3) y deja el
# clip pre-cortado listo — pero NO genera copy ni renderiza. Si el candidato
# resulta finalista, `_deliver_moment` reutiliza este resultado tal cual.
@dataclass
class _PreparedClip:
    ok: bool                                    # False = sin video local (fallback a deep-link)
    precut_path: str | None = None
    clip_duration: float = 0.0
    clip_words: list | None = None
    clip_segments_whisper: list | None = None
    subs_segments: list | None = None
    subs_words: list | None = None
    subs_offset: float = 0.0
    snap_trim_start: float = 0.0
    verification_info: dict | None = None
    coverage_val: float | None = None
    wps_val: float | None = None
    clip_text_final: str | None = None
    overlay_text: str | None = None
    clip_generation_error: str | None = None
    incomplete_tail: bool = False
    late_hook: bool = False
    whisper_bad_segment: bool = False
    whisper_timestamps_suspect: bool = False
    min_duration_reverted: bool = False
    hook_not_found: bool = False
    payoff_not_found: bool = False
    margin_extended: bool = False
    margin_extension_failed: bool = False
    subs_disabled_timestamps: bool = False
    line_aligned: bool = False


def _prepare_moment_clip(
    moment,
    moment_index: int,
    *,
    video_id: str,
    video_url: str,
    video_duration: float,
    muxed_video_path: str | None,
    muxed_avail_end: float | None,
    clip_paths_cache: dict[int, ClipDownloadResult],
    partial_download_failed: bool,
    transcript: dict,
    video_info: dict,
) -> "_PreparedClip":
    """Resuelve fuente + ancla de frases (W1) + Whisper (con guardas W3) para
    UN candidato. No genera copy, no renderiza, no sube nada. `precut_path`
    (si existe) NO se borra acá — el caller decide: lo reutiliza en la
    entrega si el candidato gana, o lo limpia si se descarta."""
    if moment.start_time is None or moment.end_time is None:
        return _PreparedClip(ok=False)

    sync_retries = _clip_sync_retries()
    strict_sync = _strict_sync_validation()
    sync_retry_reason = "baja cobertura Whisper"
    last_good_source: "_MomentSource | None" = None

    for sync_attempt in range(sync_retries + 1):
        try:
            if sync_attempt > 0:
                print(
                    f"   🔄 Reintento sync {sync_attempt}/{sync_retries} "
                    f"para clip {moment_index} ({sync_retry_reason})..."
                )
                clip_paths_cache.pop(moment_index, None)
            whisper_bad_segment = False
            whisper_timestamps_suspect = False
            min_duration_reverted = False

            start_s = float(moment.start_time)
            end_s = float(moment.end_time)
            print(f"\n✂️ Evaluando candidato {moment_index} ({int(start_s)}-{int(end_s)}s)...")

            overlay_text = getattr(moment, 'viral_overlay', None)
            if not overlay_text:
                tp = getattr(moment, 'tiktok_package', None)
                overlay_text = getattr(tp, 'overlay_text', None) if tp else None

            seg_path = None
            # ── W1: cortes anclados a las frases de Verificación ──────────
            first_phrase, last_phrase = _moment_phrases(moment)
            anchored_mode = bool(first_phrase or last_phrase)
            anchored = None
            extend_after_sec = 0.0
            extend_attempted = False
            margin_extended = False
            margin_extension_failed = False
            subs_disabled_timestamps = False
            hook_not_found = False
            payoff_not_found = False
            line_aligned = False
            wide_path = DOWNLOADS_DIR / f"{video_id}_m{moment_index}_wide.mp4"

            while True:
                source = _resolve_moment_video_source(
                    moment_index=moment_index,
                    start_s=start_s,
                    end_s=end_s,
                    video_url=video_url,
                    video_id=video_id,
                    video_duration=video_duration,
                    muxed_video_path=muxed_video_path,
                    muxed_avail_end=muxed_avail_end,
                    clip_paths_cache=clip_paths_cache,
                    partial_download_failed=partial_download_failed,
                    sync_attempt=sync_attempt,
                    extend_after_sec=extend_after_sec,
                    fallback_source=last_good_source,
                )
                if source.insufficient:
                    if extend_after_sec > 0:
                        margin_extension_failed = True
                else:
                    last_good_source = source
                    if extend_after_sec > 0:
                        margin_extended = True
                src_path, src_start, src_end = source.path, source.src_start, source.src_end
                seg_path = source.seg_path
                if not anchored_mode:
                    break

                # Segmento ancho: [start − antes, end + después] ∩ disponible
                m_before, m_after = _clip_margins_sec()
                seg_start_abs = max(source.avail_start_abs, start_s - m_before)
                seg_end_abs = min(source.avail_end_abs, end_s + m_after + extend_after_sec)
                if seg_end_abs - seg_start_abs < (end_s - start_s) - 0.5:
                    print(
                        f"   ⚠️ Fuente {source.kind} no cubre el momento "
                        f"({seg_start_abs:.0f}–{seg_end_abs:.0f}s) — flujo numérico"
                    )
                    anchored_mode = False
                    break
                base_abs = start_s - src_start
                wide_duration = seg_end_abs - seg_start_abs
                print(
                    f"   📐 Segmento ancho {seg_start_abs:.1f}–{seg_end_abs:.1f}s "
                    f"({wide_duration:.0f}s; numérico {start_s:.0f}–{end_s:.0f}s, "
                    f"fuente={source.kind}"
                    f"{', +' + str(int(extend_after_sec)) + 's' if extend_after_sec else ''})"
                )
                cut_clip(
                    video_path=src_path,
                    start_sec=seg_start_abs - base_abs,
                    end_sec=seg_end_abs - base_abs,
                    output_path=str(wide_path),
                )

                try:
                    tw = _transcribe_with_guards(
                        str(wide_path), wide_duration,
                        video_id=video_id, moment_index=moment_index,
                        moment=moment, transcript=transcript, video_info=video_info,
                        ctx_start_abs=seg_start_abs, ctx_end_abs=seg_end_abs,
                        tag="segmento ancho",
                    )
                except Exception as e_wide:
                    print(f"   ⚠️ Whisper del segmento ancho falló ({e_wide}) — flujo numérico")
                    anchored_mode = False
                    _unlink_quiet(wide_path)
                    break
                wide_words, wide_segments = tw["words"], tw["segments"]
                whisper_bad_segment = tw["bad_segment"]
                whisper_timestamps_suspect = tw["timestamps_suspect"]
                subs_disabled_timestamps = tw["subs_disabled_timestamps"]

                if whisper_bad_segment:
                    _unlink_quiet(wide_path)
                    if (
                        strict_sync
                        and sync_attempt < min(sync_retries, _BAD_SEGMENT_MAX_RETRIES)
                        and _should_sync_retry_download(None, bad_segment=True)
                    ):
                        sync_retry_reason = "segmento sin habla plausible"
                        raise _SyncRetryNeeded(
                            "bad_segment: Whisper no devolvió habla plausible — re-download"
                        )
                    print(
                        f"   🚩 bad_segment persiste en clip {moment_index} — "
                        f"corte numérico sin subtítulos"
                    )
                    anchored_mode = False
                    break

                if subs_disabled_timestamps:
                    bounds = {
                        "start_rel": max(0.0, start_s - seg_start_abs),
                        "end_rel": min(wide_duration, end_s - seg_start_abs),
                        "flags": [], "evidence": {},
                    }
                    wide_words, wide_segments = None, None
                else:
                    from services.validation import compute_clip_bounds
                    bounds = compute_clip_bounds(
                        wide_words, first_phrase, last_phrase,
                        seg_start_abs=seg_start_abs, seg_end_abs=seg_end_abs,
                        video_duration=video_duration,
                        hint_start_abs=start_s, hint_end_abs=end_s,
                        segments=wide_segments,
                        hook=moment.hook or "", overlay=overlay_text or "",
                        # W1-C: Líneas del transcript completo (W4) si el job
                        # las tiene (TRANSCRIPT_SOURCE=whisper_full|hybrid) —
                        # mandan sobre las palabras del segmento ancho para
                        # decidir dónde arranca/termina el clip.
                        lines=transcript.get("lines"),
                    )
                    ev = bounds["evidence"]
                    print(
                        f"   ⚓ Frases: first={'✓' if ev.get('first_found') else '✗'}"
                        f"({ev.get('first_score')}) last={'✓' if ev.get('last_found') else '✗'}"
                        f"({ev.get('last_score')}) → start={ev.get('start_source')} "
                        f"end={ev.get('end_source')} flags={bounds['flags']}"
                    )
                    if ev.get("first_matched_text"):
                        print(f"      first: «{first_phrase[:70]}» ≈ «{ev['first_matched_text'][:70]}»")
                    if ev.get("last_matched_text"):
                        print(f"      last:  «{last_phrase[:70]}» ≈ «{ev['last_matched_text'][:70]}»")
                    can_extend = source.kind in ("muxed", "cached", "ytdlp") and not source.insufficient
                    if (
                        ev.get("extend_recommended")
                        and not extend_attempted
                        and can_extend
                        and seg_end_abs < video_duration - 0.5
                    ):
                        extend_attempted = True
                        extend_after_sec = _PAYOFF_EXTEND_AFTER_SEC
                        print(
                            f"   🔎 Última frase no aparece — extiendo el segmento "
                            f"+{extend_after_sec:.0f}s y reintento"
                        )
                        _unlink_quiet(wide_path)
                        continue
                anchored = {
                    "bounds": bounds,
                    "words": wide_words,
                    "segments": wide_segments,
                    "seg_start_abs": seg_start_abs,
                    "seg_end_abs": seg_end_abs,
                    "wide_duration": wide_duration,
                }
                break

            clip_words = None
            clip_segments_whisper = None
            precut_path = DOWNLOADS_DIR / f"{video_id}_m{moment_index}_precut.mp4"

            if anchored_mode and anchored is not None:
                b = anchored["bounds"]
                start_rel, end_rel = float(b["start_rel"]), float(b["end_rel"])
                clip_duration = end_rel - start_rel
                abs_start = anchored["seg_start_abs"] + start_rel
                abs_end = anchored["seg_start_abs"] + end_rel
                print(
                    f"   ✂️ Corte anclado: {abs_start:.1f}–{abs_end:.1f}s "
                    f"({clip_duration:.1f}s; numérico {start_s:.0f}–{end_s:.0f}s, "
                    f"Δstart={abs_start - start_s:+.1f}s Δend={abs_end - end_s:+.1f}s)"
                )
                cut_clip(
                    video_path=str(wide_path),
                    start_sec=start_rel,
                    end_sec=end_rel,
                    output_path=str(precut_path),
                )
                _unlink_quiet(wide_path)
                if not seg_path:
                    seg_path = str(precut_path)
                hook_not_found = "hook_not_found" in b["flags"]
                payoff_not_found = "payoff_not_found" in b["flags"]
                line_aligned = bool(b["evidence"].get("line_aligned"))
                if anchored["words"]:
                    clip_words = shift_words_timeline(
                        anchored["words"], start_rel, clip_duration=clip_duration
                    )
                    clip_words = fix_ghost_leading_words(clip_words)
                    clip_words = filter_whisper_words(clip_words, clip_duration)
                    clip_words = [
                        w for w in clip_words if float(w["start"]) < clip_duration - 0.02
                    ]
                    clip_segments_whisper = [
                        {
                            **sg,
                            "start": max(0.0, float(sg["start"]) - start_rel),
                            "end": min(clip_duration + 0.10, float(sg["end"]) - start_rel),
                        }
                        for sg in (anchored["segments"] or [])
                        if float(sg.get("end", 0)) > start_rel
                        and float(sg.get("start", 0)) < end_rel
                    ]
                    print(f"   📊 Palabras en el clip final: {len(clip_words)} "
                          f"({len(clip_words) / clip_duration:.2f} w/s)")
            else:
                anchored = None
                clip_duration = src_end - src_start
                print(f"   ✂️ Pre-corte preciso ({clip_duration:.1f}s)...")
                cut_clip(
                    video_path=src_path,
                    start_sec=src_start,
                    end_sec=src_end,
                    output_path=str(precut_path),
                )
                if not seg_path:
                    seg_path = str(precut_path)

                if whisper_bad_segment:
                    pass
                else:
                    try:
                        tw = _transcribe_with_guards(
                            str(precut_path), clip_duration,
                            video_id=video_id, moment_index=moment_index,
                            moment=moment, transcript=transcript, video_info=video_info,
                            ctx_start_abs=start_s, ctx_end_abs=end_s,
                        )
                        clip_words, clip_segments_whisper = tw["words"], tw["segments"]
                        whisper_bad_segment = tw["bad_segment"]
                        whisper_timestamps_suspect = tw["timestamps_suspect"]
                        subs_disabled_timestamps = tw["subs_disabled_timestamps"]
                        if subs_disabled_timestamps:
                            clip_words = None
                            clip_segments_whisper = None
                        elif not clip_words:
                            print(f"   🔄 Whisper devolvió 0 words — fallback a YT transcript")
                            clip_words = None
                            clip_segments_whisper = None
                    except Exception as e_whisper:
                        print(f"   ⚠️ Whisper per-clip falló ({e_whisper}) — "
                              f"fallback a YT Transcript API")
                        clip_words = None
                        clip_segments_whisper = None

                    if whisper_bad_segment:
                        if (
                            strict_sync
                            and sync_attempt < min(sync_retries, _BAD_SEGMENT_MAX_RETRIES)
                            and _should_sync_retry_download(None, bad_segment=True)
                        ):
                            sync_retry_reason = "segmento sin habla plausible"
                            raise _SyncRetryNeeded(
                                "bad_segment: Whisper no devolvió habla plausible — re-download"
                            )
                        print(
                            f"   🚩 bad_segment persiste en clip {moment_index} — "
                            f"se renderiza sin subtítulos"
                        )
                        clip_words = None
                        clip_segments_whisper = None

            incomplete_tail = False
            late_hook = False
            verification_info = None
            coverage_val = None
            wps_val = None
            clip_text_final = None
            subs_segments = None
            subs_words = None
            subs_offset = 0.0
            snap_trim_start = 0.0

            if clip_words or clip_segments_whisper:
                subs_segments = clip_segments_whisper
                subs_words = clip_words
                subs_offset = 0.0
                snap_trim_start = 0.0
                from services.validation import (
                    verify_phrases_after_snap,
                    verify_phrases_against_whisper,
                    sync_verification_phrases_from_words,
                )
                from services.clip_generator import has_incomplete_tail

                if anchored is not None:
                    ev = anchored["bounds"]["evidence"]
                    start_rel = float(anchored["bounds"]["start_rel"])
                    snap_trim_start = max(0.0, (anchored["seg_start_abs"] + start_rel) - start_s)
                    incomplete_tail = has_incomplete_tail(
                        clip_words, tail_already_snapped=bool(ev.get("last_found"))
                    )
                    # W2-C: late_hook es informativo (ya no integra
                    # verification_failed) y se mide en palabras además de
                    # segundos — W1 ancla al inicio de ORACIÓN de la primera
                    # frase, así que unas palabras/segundos de setup antes
                    # del hook citado son normales, no un corte tardío.
                    from services.validation import hook_delay_metrics, is_late_hook
                    hook_delay_sec, hook_words_before = hook_delay_metrics(
                        anchored["words"], start_rel, ev.get("first_phrase_rel_start")
                    )
                    late_hook = is_late_hook(hook_delay_sec, hook_words_before)
                    verification_info = {
                        "first_ok": bool(ev.get("first_found")) or not first_phrase,
                        "last_ok": bool(ev.get("last_found")) or not last_phrase,
                    }
                    verification_info["failed"] = not (
                        verification_info["first_ok"] and verification_info["last_ok"]
                    )
                    classic = verify_phrases_against_whisper(moment, clip_words)
                    if classic.get("failed") and not verification_info["failed"]:
                        print(
                            "   ℹ️ Verificación textual clásica discrepa "
                            f"(first_ok={classic.get('first_ok')}, last_ok={classic.get('last_ok')}): "
                            "las frases están dentro del clip pero no en los bordes "
                            "(el clip arranca/termina en oración completa)"
                        )
                    if snap_trim_start >= 0.5 or abs(
                        (anchored["seg_start_abs"] + float(anchored["bounds"]["end_rel"])) - end_s
                    ) >= 0.5:
                        sync_verification_phrases_from_words(moment, clip_words)
                else:
                    refined = _refine_bounds_legacy(
                        clip_words=clip_words,
                        clip_duration=clip_duration,
                        clip_segments_whisper=clip_segments_whisper or [],
                        moment=moment,
                        precut_path=precut_path,
                        video_id=video_id,
                        moment_index=moment_index,
                        whisper_timestamps_suspect=whisper_timestamps_suspect,
                    )
                    precut_path = refined["precut_path"]
                    clip_duration = refined["clip_duration"]
                    clip_words = refined["clip_words"]
                    clip_segments_whisper = refined["clip_segments_whisper"]
                    snap_trim_start = refined["snap_trim_start"]
                    min_duration_reverted = refined["min_duration_reverted"]
                    subs_words = clip_words
                    subs_segments = clip_segments_whisper

                    incomplete_tail = has_incomplete_tail(
                        clip_words, tail_already_snapped=refined["tail_snapped_by_sentence"]
                    )
                    late_hook = snap_trim_start > 3.0

                    verification_info = verify_phrases_after_snap(
                        moment, clip_words, snap_trim_start, clip_duration
                    )
                if anchored is not None:
                    # W2-C: Verificación (CONTEXT.md) = el clip contiene lo
                    # que dice contener. incomplete_tail/late_hook quedan
                    # como flags informativos aparte (ver
                    # services.validation.verification_failed_from_flags).
                    from services.validation import verification_failed_from_flags
                    moment.verification_failed = verification_failed_from_flags(
                        hook_not_found, payoff_not_found
                    )
                    reasons = []
                    if hook_not_found:
                        reasons.append("hook not found")
                    if payoff_not_found:
                        reasons.append("payoff not found")
                else:
                    moment.verification_failed = False
                    reasons = []
                    if verification_info.get("failed"):
                        moment.verification_failed = True
                        reasons.append("phrase mismatch")
                    if incomplete_tail:
                        moment.verification_failed = True
                        reasons.append("incomplete tail")
                    if late_hook:
                        moment.verification_failed = True
                        reasons.append("late hook")
                info_reasons = []
                if incomplete_tail:
                    info_reasons.append("incomplete_tail")
                if late_hook:
                    info_reasons.append("late_hook")
                if reasons:
                    print(f"   🚩 verification_failed: {', '.join(reasons)}")
                if anchored is not None and info_reasons:
                    print(f"   ℹ️ flags informativos (no afectan verification_failed): {', '.join(info_reasons)}")
                coverage_val = srt_coverage_metric(clip_words, clip_duration)
                wps_val = (len(clip_words) / clip_duration) if clip_duration > 0 else 0.0
                print(f"   📊 Sub coverage: {coverage_val:.0%} | densidad: {wps_val:.2f} w/s")

                if strict_sync and _should_sync_retry_download(coverage_val):
                    if sync_attempt < sync_retries:
                        raise _SyncRetryNeeded(
                            f"cobertura Whisper {coverage_val:.0%} — re-download"
                        )
                    raise ClipGenerationError(
                        "Sync validation failed after retries (baja cobertura)"
                    )

                from services.processor import _clip_text_from_words
                clip_text_final = _clip_text_from_words(clip_words)
            else:
                subs_segments = (
                    None if (whisper_bad_segment or subs_disabled_timestamps)
                    else transcript.get("segments")
                )
                subs_words = None
                subs_offset = start_s
                from services.validation import _words_in_range
                clip_text_final = _words_in_range(
                    transcript.get("segments") or [], start_s, end_s
                )

            # Éxito: no se limpia precut_path (lo reutiliza la entrega si gana).
            for tmp in (seg_path, locals().get("wide_path")):
                if tmp and str(tmp) != str(precut_path):
                    try:
                        Path(tmp).unlink(missing_ok=True)
                    except Exception:
                        pass
            return _PreparedClip(
                ok=True,
                precut_path=str(precut_path),
                clip_duration=clip_duration,
                clip_words=clip_words,
                clip_segments_whisper=clip_segments_whisper,
                subs_segments=subs_segments,
                subs_words=subs_words,
                subs_offset=subs_offset,
                snap_trim_start=snap_trim_start,
                verification_info=verification_info,
                coverage_val=coverage_val,
                wps_val=wps_val,
                clip_text_final=clip_text_final,
                overlay_text=overlay_text,
                incomplete_tail=incomplete_tail,
                late_hook=late_hook,
                whisper_bad_segment=whisper_bad_segment,
                whisper_timestamps_suspect=whisper_timestamps_suspect,
                min_duration_reverted=locals().get("min_duration_reverted", False),
                hook_not_found=hook_not_found,
                payoff_not_found=payoff_not_found,
                margin_extended=margin_extended,
                margin_extension_failed=margin_extension_failed,
                subs_disabled_timestamps=subs_disabled_timestamps,
                line_aligned=line_aligned,
            )
        except _SyncRetryNeeded as e_sync:
            print(f"   ⚠️ {e_sync}")
            continue
        except (ClipGenerationError, Exception) as e:
            clip_generation_error = str(e)[:500]
            print(f"⚠️ Evaluación del candidato {moment_index} falló: {e}")
            for tmp in (locals().get("seg_path"), locals().get("precut_path"), locals().get("wide_path")):
                if tmp:
                    try:
                        Path(tmp).unlink(missing_ok=True)
                    except Exception:
                        pass
            return _PreparedClip(ok=False, clip_generation_error=clip_generation_error)

    # sync_retries agotados sin _SyncRetryNeeded relanzado (no debería llegar)
    return _PreparedClip(ok=False, clip_generation_error="sync retries agotados")


def _deliver_moment(
    moment,
    delivery_index: int,
    prepared: "_PreparedClip",
    *,
    job_id: str,
    video_id: str,
    job_tone: str,
    user_name: str | None,
    user_title: str | None,
    transcript: dict,
) -> bool:
    """
    W2 — fase de entrega: SOLO para finalistas. Corre Pasada B con el texto
    ya evaluado (`prepared.clip_text_final`), renderiza, sube y guarda
    `content_results`. No vuelve a descargar ni a transcribir — reutiliza
    `prepared` tal cual salió de `_prepare_moment_clip`.

    Returns:
        True si el clip se renderizó y subió con éxito.
    """
    clip_url = None
    preview_url = None
    raw_clip_url_cache = None
    whisper_words_cache = None
    clip_rendered_ok = False
    clip_generation_error = prepared.clip_generation_error
    judge_scores = None
    roi_clip_duration = prepared.clip_duration
    overlay_text = prepared.overlay_text
    clip_text_final = prepared.clip_text_final
    verification_info = prepared.verification_info
    coverage_val = prepared.coverage_val
    wps_val = prepared.wps_val

    clip_output = CLIPS_DIR / f"{video_id}_moment_{delivery_index}.mp4"
    clip_output.parent.mkdir(parents=True, exist_ok=True)

    if prepared.ok and prepared.precut_path:
        precut_path = prepared.precut_path
        try:
            print(f"\n🎬 Entregando finalista {delivery_index} "
                  f"({int(moment.start_time)}-{int(moment.end_time)}s)...")
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
                if moment.viral_overlay:
                    overlay_text = moment.viral_overlay

                # ── Fase 4: juez independiente sobre el clip final ────
                # (nota final mostrada al usuario; puede diferir levemente
                # de la nota de evaluación de W2 porque corre post-copy,
                # con el overlay/hook ya regenerados)
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
            if precut_path and Path(precut_path).exists():
                try:
                    from services.supabase_client import upload_raw_clip_to_storage
                    raw_clip_url_cache = upload_raw_clip_to_storage(
                        precut_path, job_id, delivery_index
                    )
                    print(f"   💾 Raw cache subido (post-snap)")
                except Exception as e_cache:
                    print(f"   ⚠️ Cache raw clip falló (no fatal): {e_cache}")

            # W9-B: se entrega un PREVIEW liviano (480x854, crf 28), no el
            # HD de siempre — el HD real se genera a pedido desde
            # raw_clip_url_cache (clip_edit_processor.py, edit_type=
            # 'hd_upgrade'). clip_url y preview_url apuntan al mismo archivo.
            gen_result = generate_clip(
                video_path=str(precut_path),
                start_sec=0.0,
                end_sec=prepared.clip_duration,
                output_path=str(clip_output),
                segments=prepared.subs_segments,
                segments_start_offset_sec=prepared.subs_offset,
                words=prepared.subs_words,
                subtitle_style=SUBTITLE_STYLE_DEFAULT,
                keywords=getattr(moment, "keywords", None),
                overlay_text=overlay_text,
                overlay_style="tiktok_viral",
                target_width=PREVIEW_WIDTH,
                target_height=PREVIEW_HEIGHT,
                crf=PREVIEW_CRF,
            )
            print(f"✅ Preview generado en {gen_result.total_time_sec}s, {gen_result.final.size_mb:.1f}MB")
            print(f"📤 Subiendo preview {delivery_index} a R2...")
            clip_url = upload_clip_to_storage(str(clip_output), job_id, delivery_index)
            if clip_url:
                print(f"✅ Preview subido: {clip_url[:70]}...")
                preview_url = clip_url
                clip_rendered_ok = True
                if prepared.clip_words or prepared.clip_segments_whisper:
                    whisper_words_cache = {
                        "words": prepared.clip_words or [],
                        "segments": prepared.clip_segments_whisper or [],
                        "duration_sec": prepared.clip_duration,
                        "snap_trim_start": prepared.snap_trim_start,
                    }
            else:
                raise RuntimeError("upload_clip_to_storage devolvió None")
        except Exception as e:
            clip_generation_error = str(e)[:500]
            print(f"⚠️ Entrega del finalista {delivery_index} falló: {e}")
            whisper_words_cache = None
            if moment.start_time is not None:
                clip_url = f"https://www.youtube.com/watch?v={video_id}&t={int(moment.start_time)}s"
                print(f"🔗 Fallback a link de YouTube: {clip_url}")
        finally:
            for tmp in (precut_path, clip_output):
                if tmp:
                    try:
                        Path(tmp).unlink(missing_ok=True)
                    except Exception:
                        pass
            gc.collect()
    else:
        # Sin video local (evaluación falló) — deep link con timestamp
        if moment.start_time is not None:
            clip_url = f"https://www.youtube.com/watch?v={video_id}&t={int(moment.start_time)}s"
            print(f"🔗 Clip {delivery_index}: YouTube link at {int(moment.start_time)}s → {clip_url}")

    # ── Rescate de copy: solo si no hay copy Y el clip no se renderizó;
    # no enmascarar fallos con scores del análisis como finales.
    from services.validation import is_youtube_clip_fallback, build_clip_quality_issues
    if not clip_rendered_ok and is_youtube_clip_fallback(clip_url):
        clip_quality_issues = build_clip_quality_issues(
            verification_info=verification_info,
            incomplete_tail=prepared.incomplete_tail,
            late_hook=prepared.late_hook,
            clip_not_rendered=True,
            clip_generation_error=clip_generation_error,
            timestamps_suspect=prepared.whisper_timestamps_suspect,
            bad_segment=prepared.whisper_bad_segment,
            min_duration_reverted=prepared.min_duration_reverted,
            hook_not_found=prepared.hook_not_found,
            payoff_not_found=prepared.payoff_not_found,
            margin_extended=prepared.margin_extended,
            margin_extension_failed=prepared.margin_extension_failed,
            subs_disabled_timestamps=prepared.subs_disabled_timestamps,
            line_aligned=prepared.line_aligned,
        )
    elif clip_rendered_ok or verification_info:
        clip_quality_issues = build_clip_quality_issues(
            verification_info=verification_info,
            incomplete_tail=prepared.incomplete_tail,
            late_hook=prepared.late_hook,
            clip_not_rendered=False,
            clip_generation_error=None,
            timestamps_suspect=prepared.whisper_timestamps_suspect,
            bad_segment=prepared.whisper_bad_segment,
            min_duration_reverted=prepared.min_duration_reverted,
            hook_not_found=prepared.hook_not_found,
            payoff_not_found=prepared.payoff_not_found,
            margin_extended=prepared.margin_extended,
            margin_extension_failed=prepared.margin_extension_failed,
            subs_disabled_timestamps=prepared.subs_disabled_timestamps,
            line_aligned=prepared.line_aligned,
        )
    else:
        clip_quality_issues = []

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

    # W6: overlay_no_fiel / hook_no_fiel los setea generate_moment_copy_full
    # en moment.clip_quality_issues (Pasada B arriba, o el rescate recién
    # corrido) — se mergean acá con los flags de W1/W2-C/W3.
    clip_quality_issues = clip_quality_issues + (getattr(moment, "clip_quality_issues", None) or [])

    # Extract scores if available
    scores = moment.scores if hasattr(moment, 'scores') and moment.scores else None
    pillar_raw = moment.pillar_type if hasattr(moment, 'pillar_type') else None

    pillar = None
    if pillar_raw:
        valid_pillars = ['authority', 'utility', 'connection', 'entertainment']
        for p in pillar_raw.lower().replace('|', ' ').split():
            if p.strip() in valid_pillars:
                pillar = p.strip()
                break

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
    if clip_rendered_ok and not judge_scores and scores:
        display_hook = scores.hook
        display_retention = scores.retention
        display_share = scores.shareability

    category = getattr(moment, 'category', None) or 'business'
    category = category.lower().strip()
    _valid_categories = ('podcast', 'business', 'entertainment') if os.getenv(
        "ENABLE_ENTERTAINMENT_CATEGORY", ""
    ).lower() in ("1", "true", "yes") else ('podcast', 'business')
    if category not in _valid_categories:
        category = 'business'

    print(f"\n📊 Category: {category.upper()} — saving full content package")

    _overlay_final = getattr(moment, 'viral_overlay', None)
    if not _overlay_final:
        _tp = getattr(moment, 'tiktok_package', None)
        _overlay_final = getattr(_tp, 'overlay_text', None) if _tp else None

    common_kwargs = dict(
        job_id=job_id,
        clip_url=clip_url,
        start_time=moment.start_time,
        end_time=moment.end_time,
        hook=moment.hook,
        emotional_trigger=moment.emotional_trigger,
        moment_index=delivery_index,
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
        score_llm=score_llm_dict,
        score_judge=judge_scores,
        verification_failed=getattr(moment, 'verification_failed', None),
        sub_coverage=coverage_val,
        words_per_sec=wps_val,
        clip_quality_issues=clip_quality_issues or None,
        clip_generation_error=clip_generation_error,
        # W10 (gap de integración INT-1a): la Pasada B ya genera y guarda
        # title/description/hashtags en el moment (services/processor.py,
        # models/schemas.py), y save_content_result ya los acepta
        # (services/supabase_client.py) — faltaba enchufarlos acá.
        title=getattr(moment, "title", None),
        description=getattr(moment, "description", None),
        hashtags=getattr(moment, "hashtags", None),
        # W9-B (docs/PLAN_CALIDAD.md §9 W9): mismo archivo que clip_url —
        # la columna existe desde W9-A (migración galeria_hd) pero hasta
        # ahora ningún job la llenaba.
        preview_url=preview_url,
    )

    if moment.content_pieces.twitter_thread:
        save_content_result(
            content_type="twitter_thread",
            content=moment.content_pieces.twitter_thread,
            **common_kwargs,
        )

    if moment.content_pieces.linkedin_post:
        save_content_result(
            content_type="linkedin_post",
            content=moment.content_pieces.linkedin_post,
            **common_kwargs,
        )

    tiktok_caption = getattr(moment.content_pieces, 'tiktok_caption', None)
    if not tiktok_caption and getattr(moment, 'tiktok_package', None):
        tiktok_caption = moment.tiktok_package.caption
    if tiktok_caption:
        save_content_result(
            content_type="tiktok_caption",
            content=tiktok_caption,
            **common_kwargs,
        )

    return clip_rendered_ok


def _annotate_candidates_all_with_judge(
    video_id: str,
    tone: str,
    candidates: list,
    selected_idx: set,
) -> None:
    """
    W2 — trazabilidad: guarda la nota del juez de TODOS los candidatos
    evaluados (elegidos y descartados) en `candidates_all` dentro de
    `analysis_cache`, junto al motivo del descarte y si fue seleccionado.
    Sin migración de esquema — `candidates_all` ya existe desde W0. Best
    effort: el match candidato↔entry es por (start_time, end_time) más
    cercano porque `validate_durations` puede haber ajustado los tiempos
    entre la Pasada A cruda y el momento que llegó acá; si no hay match
    razonable o falla el guardado, no rompe el job.
    """
    try:
        from services.analysis_cache import get_cached_analysis_row, save_analysis
        from config.model_tiers import get_model

        model = get_model("analysis")
        row = get_cached_analysis_row(video_id, model, tone)
        if not row or not isinstance((row.get("result") or {}).get("candidates_all"), list):
            return
        result = row["result"]
        all_entries = result["candidates_all"]
        for cand in candidates:
            best_entry, best_dist = None, 8.0
            for entry in all_entries:
                try:
                    dist = (
                        abs(float(entry.get("start_time", 0)) - cand.start_time)
                        + abs(float(entry.get("end_time", 0)) - cand.end_time)
                    )
                except (TypeError, ValueError):
                    continue
                if dist < best_dist:
                    best_dist, best_entry = dist, entry
            if best_entry is not None:
                best_entry["judge_scores"] = cand.judge_scores
                best_entry["w2_score"] = round(score_candidate(cand), 2)
                best_entry["w2_selected"] = cand.index in selected_idx
                best_entry["w2_discard_reason"] = cand.discard_reason
        save_analysis(
            video_id, model, result, tone=tone,
            category_detected=row.get("category_detected"),
        )
    except Exception as e:
        print(f"   ⚠️ No se pudo anotar candidates_all con las notas del juez (no fatal): {e}")


def _finalize_job_outcome(
    *,
    job_id: str,
    video_url: str,
    user_id: str | None,
    supabase,
    clips_rendered_count: int,
    total_moments: int,
) -> None:
    """
    W9-B (docs/PLAN_CALIDAD.md §9 W9): decide si el job termina `completed`
    o `failed`, y descuenta el crédito solo en el primer caso.

    "Job" en CONTEXT.md es exitoso si al menos un momento tiene clip; si
    ninguno lo tiene, falla y devuelve el crédito. Antes de esto, un job
    sin clips reales (pero sin excepción) quedaba `completed` sin devolver
    el crédito reservado — F1 lo dejó como nota pendiente explícita en
    supabase/migrations/20260919040620_creditos_reservados.sql: el trigger
    `release_credit_on_job_failed` solo dispara con `status='failed'`.
    """
    if clips_rendered_count == 0:
        print(f"\n❌ Job {job_id} sin clips viables ({total_moments} momentos evaluados, 0 con clip real)")
        print(
            "   Descarga falló (403 googlevideo). Revisa en Render: "
            "WEBSHARE_PROXY_FILE, RAPIDAPI_KEY, o activa YTDLP_CLIP_FALLBACK=true"
        )
        # No se descuenta crédito: update_job_error dispara el trigger que
        # libera la reserva de F1 (release_credit_on_job_failed).
        update_job_error(job_id, "sin clips viables")
        return

    update_job_status(job_id, "completed")
    print(f"\n✅ Job {job_id} completed successfully!")
    print(f"   {clips_rendered_count}/{total_moments} clips MP4 + content for {total_moments} moments")
    # Deduct credits (Sprint 3) — no-op si ya se descontó por credit_reserved
    # (F1, deduct_user_credit redefinido como idempotente en la migración
    # creditos_reservados).
    if user_id and supabase:
        try:
            print(f"💰 Deducting credit for user {user_id}...")
            from services.supabase_client import deduct_credit
            deduct_credit(user_id, job_id, video_url)
            print(f"✅ Credit deducted successfully")
        except Exception as e:
            print(f"⚠️ Failed to deduct credit: {e}")


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
    video_id = None
    muxed_video_path = None  # se setea solo en path B (partial download fallback)
    muxed_avail_end = None   # hasta qué segundo absoluto llega el muxeado (W1)
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
        update_job_progress(job_id, current_step="transcribing", progress_percentage=0)

        from services.yt_transcript import get_youtube_transcript
        transcript, video_info = get_youtube_transcript(video_url)
        video_id = video_info["id"]

        update_job_status(job_id, "processing", video_info["title"])
        update_job_progress(job_id, current_step="classifying", progress_percentage=compute_progress_percentage("classifying"))
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
        update_job_progress(job_id, current_step="analyzing")

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
        update_job_progress(job_id, current_step="evaluating", progress_percentage=compute_progress_percentage("evaluating"))
        check_timeout()  # C5
        
        # Step 3.5: Quality Filter - Validate durations
        # Phase 1.1: surgical_clipping is now migrated to flat start_time/end_time
        # inside processor.py BEFORE Pydantic validation, so timestamps are
        # guaranteed to be populated here (the workaround that used to live
        # in this step has been removed).
        # Fase 3: el truncado a CLIP_MAX_DURATION_SEC ahora snapea al fin de
        # frase del transcript (W2-B: tope subido a 120s, docs/PLAN_CALIDAD.md
        # §8-9 — con 60s se perdía la idea completa, ver ANALISIS_OPUS_CLIP.md).
        print("\n🔍 Step 3.5: Quality filter...")
        from services.validation import (
            validate_durations,
            filter_overlapping_moments,
            validate_against_transcript,
            CLIP_MIN_DURATION_SEC,
            CLIP_MAX_DURATION_SEC,
        )
        result.viral_moments = validate_durations(
            result.viral_moments,
            min_duration=CLIP_MIN_DURATION_SEC,
            max_duration=CLIP_MAX_DURATION_SEC,
            transcript=transcript,
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
        # W9-B: la descarga del video fuente es preparación de la fase
        # `evaluating` (hace falta el video para evaluar/renderizar
        # candidatos) — no tiene su propio current_step en el contrato de
        # progreso de P1, así que el % se queda en el piso de `evaluating`
        # hasta que arranca el loop por candidato más abajo (Step 5a).
        update_job_progress(job_id, current_step="evaluating", progress_percentage=compute_progress_percentage("evaluating"))

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
                    # W1: el segmento ancho del último momento necesita el
                    # margen posterior (+25 s si hay que buscar el remate)
                    max_end = min(
                        float(video_duration),
                        max_end + _clip_margins_sec()[1] + _PAYOFF_EXTEND_AFTER_SEC,
                    )
                    muxed_avail_end = max_end
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
                # W9-B: sigue en la fase `evaluating` (preparación), el %
                # no avanza acá — el loop por candidato de Step 5a es el
                # que reporta progreso fino dentro de esta fase.
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
        # W9-B: seguimos en `evaluating` (ya seteado más arriba, Step 4) —
        # el % avanza recién con el primer candidato evaluado, abajo.

        # Summary is now stored in jobs table, not content_results
        # save_content_result() is only for actual content pieces (twitter, tiktok, etc.)

        
        # ── W2: el juez elige (docs/PLAN_CALIDAD.md §4) ──────────────────
        # Fase 5a: evaluar TODOS los candidatos del pool (descarga+Whisper+
        # ancla W1 + juez sobre el texto real, sin Pasada B ni render).
        target = target_moment_count(video_duration)
        print(
            f"\n🧪 Step 5a: evaluando {len(result.viral_moments)} candidatos "
            f"(target={target}, W2: el juez elige)..."
        )

        candidates: list[CandidateEval] = []
        prepared_by_index: dict[int, _PreparedClip] = {}
        moment_by_index: dict[int, object] = {}

        for i, moment in enumerate(result.viral_moments):
            check_timeout()
            cand_index = i + 1
            bind_trace(moment_index=cand_index, phase="eval")
            set_moment_index(cand_index)
            moment_by_index[cand_index] = moment

            prepared = _prepare_moment_clip(
                moment, cand_index,
                video_id=video_id, video_url=video_url, video_duration=video_duration,
                muxed_video_path=muxed_video_path, muxed_avail_end=muxed_avail_end,
                clip_paths_cache=clip_paths_cache, partial_download_failed=partial_download_failed,
                transcript=transcript, video_info=video_info,
            )
            prepared_by_index[cand_index] = prepared

            judge_scores = None
            if prepared.ok and prepared.clip_text_final and prepared.clip_text_final.strip():
                from services.scorer import judge_moment_scores
                _category = getattr(moment, 'category', None) or 'business'
                judge_scores = judge_moment_scores(
                    prepared.clip_text_final,
                    hook=moment.hook or "",
                    viral_overlay=getattr(moment, 'viral_overlay', None) or "",
                    category=_category,
                    clip_duration_sec=prepared.clip_duration or 0.0,
                )
                if judge_scores:
                    print(
                        f"   ⚖️ Juez (evaluación): hook={judge_scores['hook']} "
                        f"retention={judge_scores['retention']} "
                        f"share={judge_scores['shareability']}"
                    )

            scores = getattr(moment, 'scores', None)
            self_score = 0.0
            if scores:
                try:
                    self_score = float(scores.hook) + float(scores.retention) + float(scores.shareability)
                except (TypeError, ValueError):
                    self_score = 0.0

            from services.validation import WHISPER_MIN_DENSITY, WHISPER_MAX_EFFECTIVE_DENSITY
            density_out_of_range = bool(
                prepared.wps_val is not None
                and not (WHISPER_MIN_DENSITY <= prepared.wps_val <= WHISPER_MAX_EFFECTIVE_DENSITY)
            )

            candidates.append(CandidateEval(
                index=cand_index,
                start_time=float(moment.start_time) if moment.start_time is not None else 0.0,
                end_time=(
                    float(moment.end_time) if moment.end_time is not None
                    else float(moment.start_time or 0.0)
                ),
                hook=moment.hook or "",
                judge_scores=judge_scores,
                self_score=self_score,
                usable=bool(prepared.ok and prepared.clip_text_final and prepared.clip_text_final.strip()),
                density_out_of_range=density_out_of_range,
                hook_not_found=prepared.hook_not_found,
                payoff_not_found=prepared.payoff_not_found,
                bad_segment=prepared.whisper_bad_segment,
                insufficient_source=prepared.margin_extension_failed,
                timestamps_suspect=prepared.whisper_timestamps_suspect,
                late_hook=prepared.late_hook,
                incomplete_tail=prepared.incomplete_tail,
                min_duration_reverted=prepared.min_duration_reverted,
            ))

            # W9-B: progreso fino por candidato evaluado (contrato con P1).
            update_job_progress(
                job_id,
                current_step="evaluating",
                progress_percentage=compute_progress_percentage(
                    "evaluating", candidate_index=cand_index, candidates_total=len(result.viral_moments),
                ),
                progress_detail={
                    "current": cand_index,
                    "total": len(result.viral_moments),
                    "message": f"Evaluando candidato {cand_index} de {len(result.viral_moments)}",
                    "clips_ready": 0,
                },
            )

        # Fase 5b: rankear por el juez (no por el auto-score, causa C4) y
        # entregar por umbral, con diversidad (W9-B).
        update_job_progress(job_id, current_step="ranking", progress_percentage=compute_progress_percentage("ranking"))
        selected, discarded = select_finalists(candidates, target)
        selected_idx = {c.index for c in selected}
        print(
            f"\n🏆 Ranking del juez (W2): {len(candidates)} evaluados → "
            f"{len(selected)} finalistas ({len(discarded)} descartados)"
        )
        for c in discarded:
            print(f"   ✗ candidato {c.index}: {c.discard_reason or 'sin motivo'} (score={score_candidate(c):.1f})")
        for c in selected:
            print(f"   ✓ candidato {c.index} finalista (score={score_candidate(c):.1f})")

        # Medida directa de si W2 cambia algo: cuántos finalistas NO habría
        # elegido el ranking viejo (auto-score de la Pasada A — lo que ya
        # llegaba pruneado a `target` antes de este cambio, causa C4/C5).
        old_ranking_idx = {
            c.index for c in sorted(candidates, key=lambda c: c.self_score, reverse=True)[:target]
        }
        changed = len(selected_idx - old_ranking_idx)
        print(
            f"   📐 {changed}/{len(selected)} finalistas no coinciden con el "
            f"ranking viejo (auto-score de la Pasada A)"
        )

        # Trazabilidad (W0 candidates_all): notas del juez de todos los
        # candidatos, elegidos y descartados, con el motivo del descarte.
        _annotate_candidates_all_with_judge(video_id, job_tone, candidates, selected_idx)

        # Los descartados no se entregan: limpiar su precut. Los finalistas
        # reutilizan el suyo en _deliver_moment — no se vuelve a descargar
        # ni a transcribir lo que ya evaluamos acá.
        for c in discarded:
            prep = prepared_by_index.get(c.index)
            if prep and prep.precut_path:
                try:
                    Path(prep.precut_path).unlink(missing_ok=True)
                except Exception:
                    pass

        result.viral_moments = [moment_by_index[c.index] for c in selected]

        # Fase 5c: entrega — Pasada B + render + subida + save_content_result
        # SOLO para los finalistas.
        print(f"\n🎬 Step 5b: entregando {len(selected)} finalistas (Pasada B + render)...")
        clips_rendered_count = 0
        for delivery_index, cand in enumerate(selected, start=1):
            check_timeout()
            bind_trace(moment_index=delivery_index, phase="clip")
            set_moment_index(delivery_index)
            moment = moment_by_index[cand.index]
            prepared = prepared_by_index[cand.index]
            if _deliver_moment(
                moment, delivery_index, prepared,
                job_id=job_id, video_id=video_id, job_tone=job_tone,
                user_name=user_name, user_title=user_title, transcript=transcript,
            ):
                clips_rendered_count += 1

            # W9-B: progreso fino por clip entregado (contrato con P1);
            # content_results ya se escribió DENTRO de _deliver_moment, así
            # que clips_ready refleja lo que la galería ya puede mostrar.
            update_job_progress(
                job_id,
                current_step="delivering",
                progress_percentage=compute_progress_percentage(
                    "delivering", delivered_index=delivery_index, delivered_total=len(selected),
                ),
                progress_detail={
                    "current": delivery_index,
                    "total": len(selected),
                    "message": f"Entregando clip {delivery_index} de {len(selected)}",
                    "clips_ready": clips_rendered_count,
                },
            )

        total_moments = len(result.viral_moments)
        update_job_progress(job_id, current_step="finalizing", progress_percentage=compute_progress_percentage("finalizing"))
        _finalize_job_outcome(
            job_id=job_id,
            video_url=video_url,
            user_id=job_data.get("userId"),
            supabase=supabase,
            clips_rendered_count=clips_rendered_count,
            total_moments=total_moments,
        )
        
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
