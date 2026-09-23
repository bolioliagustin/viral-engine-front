"""
YouTube Transcript Service
Gets transcripts directly from YouTube's subtitle API — no download needed.

Priority:
  1. Supadata API (supadata.ai) — works from any IP, including Render/Railway
  2. youtube_transcript_api — direct scraping, blocked on datacenter IPs

W4 (docs/PLAN_CALIDAD.md §4, causa C1): la fuente del Transcript se elige con
`TRANSCRIPT_SOURCE`:
  - `supadata` (default): captions de YouTube, comportamiento de siempre.
  - `whisper_full`: Whisper del audio completo por tramos (Líneas puntuadas,
    palabras con silencios, wpm); si falla, cae a captions.
  - `hybrid`: captions para el clasificador y las validaciones numéricas +
    Whisper completo para la Pasada A (ADR 0007).
"""
import re
import os
import time
import requests
from typing import Dict, Optional

TRANSCRIPT_SOURCES = ("supadata", "whisper_full", "hybrid")


def transcript_source() -> str:
    """Fuente del Transcript según `TRANSCRIPT_SOURCE` (default `supadata`)."""
    raw = (os.getenv("TRANSCRIPT_SOURCE") or "supadata").strip().lower()
    if raw not in TRANSCRIPT_SOURCES:
        print(f"⚠️ TRANSCRIPT_SOURCE={raw!r} no es válido ({'|'.join(TRANSCRIPT_SOURCES)}); uso supadata")
        return "supadata"
    return raw


def get_video_id(video_url: str) -> Optional[str]:
    """Extract video ID from YouTube URL."""
    match = re.search(r'(?:v=|youtu\.be/|shorts/)([a-zA-Z0-9_-]{11})', video_url)
    return match.group(1) if match else None


_LENGTH_SECONDS_RE = re.compile(r'"lengthSeconds":"(\d+)"')
_DURATION_FETCH_TIMEOUT_SEC = 4


def _fetch_length_seconds(video_id: str) -> int:
    """
    Duración real sin API key (W14-B; mismo campo que
    `backend/src/lib/youtube-duration.js`): `lengthSeconds` del JSON embebido
    en el HTML público de la watch page. oEmbed no trae duración, y sin esto
    `main.py` no puede armar el timeout dinámico del job (`JOB_TIMEOUT_*`)
    antes de bajar nada. Fail-open a 0 (duración desconocida): un problema de
    scraping no debe romper el job, solo deja el timeout en el piso.
    """
    try:
        resp = requests.get(
            f"https://www.youtube.com/watch?v={video_id}",
            headers={"User-Agent": "Mozilla/5.0 (compatible; viral-engine/1.0)"},
            timeout=_DURATION_FETCH_TIMEOUT_SEC,
        )
        if not resp.ok:
            return 0
        m = _LENGTH_SECONDS_RE.search(resp.text)
        return int(m.group(1)) if m else 0
    except Exception:
        return 0


def get_video_metadata(video_id: str) -> dict:
    """
    Fetch video title and duration via YouTube oEmbed API (public, no auth needed).
    Falls back to yt-dlp --skip-download as secondary.
    """
    # Primary: oEmbed API (public, always works)
    try:
        resp = requests.get(
            f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json",
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            title = data.get("title", "Unknown")
            print(f"✅ Metadata via oEmbed: {title}")
            return {
                "id": video_id,
                "title": title,
                "duration": _fetch_length_seconds(video_id),  # oEmbed no trae duración (W14-B)
                "uploader": data.get("author_name", "Unknown"),
                "view_count": 0,
            }
    except Exception as e:
        print(f"⚠️ oEmbed failed: {e}")

    # Fallback: yt-dlp skip-download (just metadata)
    try:
        import yt_dlp
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'extract_flat': False,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            return {
                "id": video_id,
                "title": info.get("title", "Unknown"),
                "duration": info.get("duration", 0),
                "uploader": info.get("uploader", "Unknown"),
                "view_count": info.get("view_count", 0),
            }
    except Exception as e:
        print(f"⚠️ yt-dlp metadata failed: {e}")

    # Last resort: return minimal metadata
    return {
        "id": video_id,
        "title": f"Video {video_id}",
        "duration": 0,
        "uploader": "Unknown",
        "view_count": 0,
    }


def _segments_from_raw(raw_transcript: list, language: str) -> tuple[list, str]:
    """Convert raw transcript entries to Whisper-compatible segments."""
    segments = []
    full_text_parts = []

    for i, entry in enumerate(raw_transcript):
        start = entry.get("start", 0)
        duration = entry.get("duration", 2.0)
        text = entry.get("text", "").strip()

        # Remove [Music], [Applause] etc auto-generated noise labels
        if text.startswith("[") and text.endswith("]"):
            continue

        segments.append({
            "id": i,
            "start": round(start, 2),
            "end": round(start + duration, 2),
            "text": text,
        })
        full_text_parts.append(text)

    return segments, " ".join(full_text_parts)


# Errores transitorios: un job concurrente saturando la red/CPU del VPS (yt-dlp
# per-clip + proxies) puede hacer que esta llamada tarde más de lo normal o
# reciba un 429/5xx pasajero. Reintentamos antes de fallar el job entero —
# un timeout de Supadata NO debería tirar abajo un job de 5 clips.
_SUPADATA_MAX_ATTEMPTS = 3
_SUPADATA_BACKOFF_SEC = 4
_SUPADATA_TIMEOUT_SEC = 30


def _supadata_request(video_url: str, api_key: str):
    """Un intento de GET a Supadata. Puede lanzar requests.exceptions.*."""
    return requests.get(
        "https://api.supadata.ai/v1/youtube/transcript",
        params={"url": video_url, "text": "false"},
        headers={"x-api-key": api_key},
        timeout=_SUPADATA_TIMEOUT_SEC,
    )


def _get_transcript_via_supadata(video_url: str, video_id: str) -> tuple[Dict, dict]:
    """
    Fetch transcript via Supadata API (supadata.ai).
    Works from any IP — Render, Railway, Fly.io, etc.
    Requires SUPADATA_API_KEY env var.

    Reintenta ante timeout/conexión caída y 429/5xx (hasta _SUPADATA_MAX_ATTEMPTS,
    con backoff). 401/404 no se reintentan — son errores deterministas.
    """
    api_key = os.getenv("SUPADATA_API_KEY")
    if not api_key:
        raise Exception("SUPADATA_API_KEY not configured")

    print(f"🌐 Fetching transcript via Supadata API for {video_id}...")

    resp = None
    last_error: Optional[Exception] = None
    for attempt in range(1, _SUPADATA_MAX_ATTEMPTS + 1):
        try:
            resp = _supadata_request(video_url, api_key)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_error = e
            if attempt < _SUPADATA_MAX_ATTEMPTS:
                wait = _SUPADATA_BACKOFF_SEC * attempt
                print(f"⚠️ Supadata {type(e).__name__} (intento {attempt}/{_SUPADATA_MAX_ATTEMPTS}) — retry en {wait}s")
                time.sleep(wait)
                continue
            raise Exception(f"Supadata API error: {e} (tras {_SUPADATA_MAX_ATTEMPTS} intentos)") from e

        if resp.status_code == 401:
            raise Exception("Invalid SUPADATA_API_KEY — check your key at supadata.ai")
        if resp.status_code == 404:
            raise Exception("No transcript available for this video (Supadata: 404)")
        if resp.status_code == 429 or resp.status_code >= 500:
            last_error = Exception(f"Supadata {resp.status_code}: {resp.text[:200]}")
            if attempt < _SUPADATA_MAX_ATTEMPTS:
                wait = _SUPADATA_BACKOFF_SEC * attempt
                print(f"⚠️ Supadata {resp.status_code} (intento {attempt}/{_SUPADATA_MAX_ATTEMPTS}) — retry en {wait}s")
                time.sleep(wait)
                continue
            if resp.status_code == 429:
                raise Exception("Supadata rate limit reached — try again later")
            raise Exception(f"Supadata API error {resp.status_code}: {resp.text[:200]}")
        if not resp.ok:
            raise Exception(f"Supadata API error {resp.status_code}: {resp.text[:200]}")
        break  # 2xx — listo
    else:
        raise Exception(f"Supadata API error: {last_error} (tras {_SUPADATA_MAX_ATTEMPTS} intentos)")

    data = resp.json()
    language_used = data.get("lang", "unknown")
    raw_entries = data.get("content", [])

    if not raw_entries:
        raise Exception("Supadata returned empty transcript")

    # Supadata format: [{text, offset (ms), duration (ms), lang}, ...]
    raw_transcript = [
        {
            "text": e.get("text", ""),
            "start": e.get("offset", 0) / 1000.0,      # ms → seconds
            "duration": e.get("duration", 2000) / 1000.0,
        }
        for e in raw_entries
    ]

    segments, full_text = _segments_from_raw(raw_transcript, language_used)

    if not segments:
        raise Exception("Transcript empty after filtering (Supadata)")

    print(f"✅ Supadata transcript: {len(segments)} segments, language: {language_used}")

    transcript = {
        "text": full_text,
        "segments": segments,
        "language": language_used,
    }

    video_info = get_video_metadata(video_id)
    if video_info["duration"] == 0 and segments:
        video_info["duration"] = int(segments[-1]["end"]) + 5

    return transcript, video_info


def _get_transcript_via_ytapi(video_url: str, video_id: str) -> tuple[Dict, dict]:
    """
    Fetch transcript via youtube_transcript_api.
    Works from residential/home IPs; often blocked on datacenters.
    """
    from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled

    print(f"📝 Fetching transcript via youtube_transcript_api for {video_id}...")

    try:
        available = YouTubeTranscriptApi.list_transcripts(video_id)

        try:
            transcript_obj = available.find_manually_created_transcript(
                ['es', 'en', 'pt', 'fr', 'de', 'it', 'ja', 'ko', 'zh']
            )
        except Exception:
            try:
                transcript_obj = available.find_generated_transcript(
                    ['es', 'en', 'pt', 'fr', 'de', 'it', 'ja', 'ko', 'zh']
                )
            except Exception:
                transcript_obj = next(iter(available))

        language_used = transcript_obj.language_code
        raw_transcript_data = transcript_obj.fetch()
        print(f"✅ Transcript found: {len(raw_transcript_data)} entries, language: {language_used}")

    except TranscriptsDisabled:
        raise Exception("This video has transcripts/captions disabled.")
    except NoTranscriptFound:
        raise Exception("No transcript found for this video.")
    except Exception as e:
        raise Exception(f"YouTube is blocking requests from this IP. Please configure SUPADATA_API_KEY (supadata.ai) or a residential proxy.")

    raw_transcript = [
        {
            "text": e.get("text", ""),
            "start": e.get("start", 0),
            "duration": e.get("duration", 2.0),
        }
        for e in raw_transcript_data
    ]

    segments, full_text = _segments_from_raw(raw_transcript, language_used)

    if not segments:
        raise Exception("Transcript is empty after filtering — no usable text found.")

    transcript = {
        "text": full_text,
        "segments": segments,
        "language": language_used,
    }

    video_info = get_video_metadata(video_id)
    if video_info["duration"] == 0 and segments:
        video_info["duration"] = int(segments[-1]["end"]) + 5

    print(f"✅ Transcript ready: {len(segments)} segments, {video_info['duration']}s, '{video_info['title']}'")
    return transcript, video_info


def get_youtube_transcript(video_url: str) -> tuple[Dict, dict]:
    """
    Get transcript from YouTube + video metadata.
    Returns transcript in Whisper-compatible format.

    Strategy (TRANSCRIPT_SOURCE=supadata, default):
      1. Supadata API (if SUPADATA_API_KEY is set) — works from any IP
      2. youtube_transcript_api — direct, works on residential IPs only
    Con `whisper_full` / `hybrid` ver `_get_transcript_via_whisper_full`.

    Returns:
        (transcript_dict, video_info_dict)
        transcript format: {"text": "...", "segments": [...], "language": "..."}
        segments format:   [{"id": 0, "start": 0.5, "end": 3.2, "text": "..."}, ...]
        Con W4 activo, además: "lines", "words" (con `__silence`), "wpm", "source".
    """
    video_id = get_video_id(video_url)
    if not video_id:
        raise ValueError(f"Could not extract video ID from URL: {video_url}")

    source = transcript_source()
    if source == "supadata":
        return _get_captions_transcript(video_url, video_id)

    if source == "whisper_full":
        try:
            return _get_transcript_via_whisper_full(video_url, video_id)
        except Exception as e:
            print(f"⚠️ Transcript whisper_full falló ({type(e).__name__}: {str(e)[:160]}) — fallback a captions")
            transcript, video_info = _get_captions_transcript(video_url, video_id)
            transcript["source_fallback_from"] = "whisper_full"
            return transcript, video_info

    # hybrid: captions (clasificador, validaciones numéricas) + Whisper completo (Pasada A)
    transcript, video_info = _get_captions_transcript(video_url, video_id)
    try:
        full, _ = _get_transcript_via_whisper_full(
            video_url, video_id, language=transcript.get("language"), video_info=video_info,
        )
    except Exception as e:
        print(f"⚠️ Transcript hybrid: Whisper completo falló ({type(e).__name__}: {str(e)[:160]}) — sigo solo con captions")
        transcript["source_fallback_from"] = "hybrid"
        return transcript, video_info
    transcript.update({
        "lines": full["lines"],
        "words": full["words"],
        "wpm": full["wpm"],
        "source": "hybrid",
        "model": full.get("model"),
        "provider": full.get("provider"),
    })
    return transcript, video_info


def _get_transcript_via_whisper_full(
    video_url: str,
    video_id: str,
    *,
    language: str | None = None,
    video_info: dict | None = None,
) -> tuple[Dict, dict]:
    """
    Transcript de alta resolución (W4): cache por (video_id, fuente, modelo)
    → descarga solo audio → Whisper por tramos → puntuación sobre palabras,
    Líneas, silencios y wpm (`transcript_lines.build_full_transcript`) → cache.
    `segments` son las Líneas, así que los consumidores actuales no cambian.
    """
    from services.transcript_cache import get_cached_transcript, save_transcript
    from services.transcriber import full_transcript_model, transcribe_full_audio
    from services.transcript_lines import (
        build_full_transcript, punctuate_unpunctuated_runs,
        lines_punctuation_rate, silence_stats,
    )

    model = full_transcript_model()
    if video_info is None:
        video_info = get_video_metadata(video_id)

    cached = get_cached_transcript(video_id, source="whisper_full", model=model)
    if cached and cached.get("lines"):
        print(f"✅ Transcript whisper_full desde cache ({len(cached['lines'])} líneas, {len(cached.get('words') or [])} tokens)")
        try:
            from services.usage_tracker import record_cache_hit
            from config.pricing import estimate_whisper_cost_usd
            avoided = estimate_whisper_cost_usd(
                "groq" if model != "whisper-1" else "openai", float(cached.get("duration") or 0),
            )
            record_cache_hit(
                "transcript_full", model=model,
                metadata={"source": "transcription_cache", "cost_avoided_usd": avoided},
            )
        except Exception:
            pass
        if not video_info.get("duration") and cached.get("duration"):
            video_info["duration"] = int(cached["duration"]) + 1
        return cached, video_info

    from services.downloader import download_audio_only
    audio_path = download_audio_only(
        video_url, video_id, expected_duration_sec=float(video_info.get("duration") or 0),
    )

    lang = (language or "").strip().lower() or None
    if lang and len(lang) > 2:
        lang = lang.split("-")[0]

    # Sin `prompt`: medido en podcast_general_01 (Groq), con el título como
    # prompt Whisper alucinó el título en 0–5 s, se saltó los primeros 30 s de
    # habla y devolvió segmentos 3× más largos (37 vs 117 en 10 min). El
    # vocabulario de marca sigue entrando en la Transcripción del clip.
    raw = transcribe_full_audio(audio_path, prompt=None, language=lang)
    provider = "/".join(raw.get("providers") or [])
    whisper_lang = raw.get("language") or lang

    # Respaldo: los tramos que Whisper dejó sin puntuar (modo degradado, o un
    # proveedor que no puntúa) se puntúan con el modelo barato. Desactivable
    # con TRANSCRIPT_PUNCTUATE_FALLBACK=false.
    punctuate_runs = None
    if (os.getenv("TRANSCRIPT_PUNCTUATE_FALLBACK") or "true").strip().lower() not in ("false", "0", "no"):
        punctuate_runs = lambda ws: punctuate_unpunctuated_runs(ws, language=whisper_lang)

    transcript = build_full_transcript(
        raw, source="whisper_full", model=model, provider=provider,
        punctuate_runs=punctuate_runs,
    )
    for key in ("audio_seconds", "cost_usd", "elapsed_sec", "n_chunks"):
        if key in raw:
            transcript[key] = raw[key]

    stats = silence_stats(transcript["words"])
    print(f"✅ Transcript whisper_full: {len(transcript['lines'])} líneas "
          f"({lines_punctuation_rate(transcript['lines'])*100:.0f}% terminan en puntuación), "
          f"{stats['count']} silencios ≥0,3 s, {transcript['wpm']} wpm, "
          f"{transcript['duration']/60:.1f} min")

    save_transcript(
        video_id, transcript,
        language=transcript.get("language"),
        duration_seconds=transcript.get("duration"),
        source="whisper_full", model=model,
    )
    if not video_info.get("duration") and transcript.get("duration"):
        video_info["duration"] = int(transcript["duration"]) + 1
    return transcript, video_info


def _get_captions_transcript(video_url: str, video_id: str) -> tuple[Dict, dict]:
    """Captions de YouTube: Supadata → youtube_transcript_api (comportamiento de siempre)."""
    supadata_key = os.getenv("SUPADATA_API_KEY")
    environment = os.getenv("ENVIRONMENT", "development")
    key_status = f"SET ({len(supadata_key)} chars)" if supadata_key else "NOT SET"
    print(f"🔑 SUPADATA_API_KEY: {key_status} | ENVIRONMENT: {environment}")

    # 1. Try Supadata API first (reliable from datacenter IPs)
    if supadata_key:
        try:
            return _get_transcript_via_supadata(video_url, video_id)
        except Exception as e:
            err = str(e)
            # Video has no transcript at all — don't bother with fallback
            if "404" in err or "No transcript" in err:
                raise Exception(f"Este video no tiene transcripción disponible en YouTube.")
            # Supadata failed for another reason
            if environment == "production":
                raise Exception(f"Supadata API error: {err}. Verifica tu SUPADATA_API_KEY en Render.")
            print(f"⚠️ Supadata failed: {err} — falling back to youtube_transcript_api (dev only)")

    # 2. Fallback: youtube_transcript_api
    # In production this will likely be blocked — surface a clear error
    if environment == "production" and not supadata_key:
        raise Exception(
            "SUPADATA_API_KEY no está configurada en las variables de entorno de Render. "
            "Agregala en: Render → viralengine-worker → Environment → SUPADATA_API_KEY"
        )

    return _get_transcript_via_ytapi(video_url, video_id)
