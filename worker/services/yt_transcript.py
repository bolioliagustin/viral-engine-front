"""
YouTube Transcript Service
Gets transcripts directly from YouTube's subtitle API — no download needed.
Not blocked by bot detection, works from any datacenter IP.
"""
import re
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


def get_youtube_transcript(video_url: str) -> tuple[Dict, dict]:
    """
    Get transcript from YouTube subtitle API + video metadata.
    Returns transcript in same format as Whisper transcriber.

    Returns:
        (transcript_dict, video_info_dict)
        transcript format: {"text": "...", "segments": [...], "language": "..."}
        segments format:   [{"id": 0, "start": 0.5, "end": 3.2, "text": "..."}, ...]
    """
    from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled

    video_id = get_video_id(video_url)
    if not video_id:
        raise ValueError(f"Could not extract video ID from URL: {video_url}")

    print(f"📝 Getting transcript for video {video_id} via YouTube Transcript API...")

    # Try to get transcript (prefers manual captions, falls back to auto-generated)
    transcript_list = None
    language_used = None

    try:
        # Get available transcripts
        available = YouTubeTranscriptApi.list_transcripts(video_id)

        # Priority: manual > auto-generated, any language
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
                # Take whatever is available
                transcript_obj = next(iter(available))

        language_used = transcript_obj.language_code
        raw_transcript = transcript_obj.fetch()
        print(f"✅ Transcript found: {len(raw_transcript)} entries, language: {language_used}")

    except TranscriptsDisabled:
        raise Exception("This video has transcripts/captions disabled — cannot process without download.")
    except NoTranscriptFound:
        raise Exception("No transcript found for this video. Try a video with captions enabled.")
    except Exception as e:
        raise Exception(f"Failed to fetch transcript: {e}")

    # Convert to Whisper-compatible format
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

    if not segments:
        raise Exception("Transcript is empty after filtering — no usable text found.")

    transcript = {
        "text": " ".join(full_text_parts),
        "segments": segments,
        "language": language_used or "unknown",
    }

    # Get video metadata
    print("📋 Fetching video metadata...")
    video_info = get_video_metadata(video_id)

    # Estimate duration from transcript if yt-dlp metadata failed
    if video_info["duration"] == 0 and segments:
        video_info["duration"] = int(segments[-1]["end"]) + 5

    print(f"✅ Transcript ready: {len(segments)} segments, {video_info['duration']}s, '{video_info['title']}'")
    return transcript, video_info
