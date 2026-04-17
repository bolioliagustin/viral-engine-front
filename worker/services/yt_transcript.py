"""
YouTube Transcript Service
Gets transcripts directly from YouTube's subtitle API — no download needed.

Priority:
  1. Supadata API (supadata.ai) — works from any IP, including Render/Railway
  2. youtube_transcript_api — direct scraping, blocked on datacenter IPs
"""
import re
import os
import requests
from typing import Dict, Optional


def get_video_id(video_url: str) -> Optional[str]:
    """Extract video ID from YouTube URL."""
    match = re.search(r'(?:v=|youtu\.be/|shorts/)([a-zA-Z0-9_-]{11})', video_url)
    return match.group(1) if match else None


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
                "duration": 0,  # oEmbed doesn't return duration
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


def _get_transcript_via_supadata(video_url: str, video_id: str) -> tuple[Dict, dict]:
    """
    Fetch transcript via Supadata API (supadata.ai).
    Works from any IP — Render, Railway, Fly.io, etc.
    Requires SUPADATA_API_KEY env var.
    """
    api_key = os.getenv("SUPADATA_API_KEY")
    if not api_key:
        raise Exception("SUPADATA_API_KEY not configured")

    print(f"🌐 Fetching transcript via Supadata API for {video_id}...")

    resp = requests.get(
        "https://api.supadata.ai/v1/youtube/transcript",
        params={"url": video_url, "text": "false"},
        headers={"x-api-key": api_key},
        timeout=30,
    )

    if resp.status_code == 401:
        raise Exception("Invalid SUPADATA_API_KEY — check your key at supadata.ai")
    if resp.status_code == 404:
        raise Exception("No transcript available for this video (Supadata: 404)")
    if resp.status_code == 429:
        raise Exception("Supadata rate limit reached — try again later")
    if not resp.ok:
        raise Exception(f"Supadata API error {resp.status_code}: {resp.text[:200]}")

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

    Strategy:
      1. Supadata API (if SUPADATA_API_KEY is set) — works from any IP
      2. youtube_transcript_api — direct, works on residential IPs only

    Returns:
        (transcript_dict, video_info_dict)
        transcript format: {"text": "...", "segments": [...], "language": "..."}
        segments format:   [{"id": 0, "start": 0.5, "end": 3.2, "text": "..."}, ...]
    """
    video_id = get_video_id(video_url)
    if not video_id:
        raise ValueError(f"Could not extract video ID from URL: {video_url}")

    supadata_key = os.getenv("SUPADATA_API_KEY")
    print(f"🔑 SUPADATA_API_KEY: {'SET (' + str(len(supadata_key)) + ' chars)' if supadata_key else 'NOT SET'}")

    # 1. Try Supadata API first (reliable from datacenter IPs)
    if supadata_key:
        try:
            return _get_transcript_via_supadata(video_url, video_id)
        except Exception as e:
            print(f"⚠️ Supadata failed: {e} — falling back to youtube_transcript_api")
            # If Supadata explicitly says no transcript, don't bother with fallback
            if "404" in str(e) or "No transcript" in str(e):
                raise Exception(f"Este video no tiene transcripción disponible: {e}")

    # 2. Fallback: youtube_transcript_api (may be blocked on Render/Railway)
    return _get_transcript_via_ytapi(video_url, video_id)
