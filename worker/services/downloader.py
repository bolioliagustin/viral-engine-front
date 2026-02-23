"""
YouTube downloader service using yt-dlp
Downloads both audio (for Gemini) and video (for clipping)
"""
import yt_dlp
import os
import base64
import shutil
import tempfile
from pathlib import Path

DOWNLOADS_DIR = Path(__file__).parent.parent / "downloads"
COOKIES_FILE = Path(__file__).parent.parent / "cookies.txt"


def _get_cookies_path():
    """Get YouTube cookies file path for yt-dlp authentication.
    Priority: YOUTUBE_COOKIES env var (base64) > cookies.txt file
    """
    # Option 1: Base64-encoded cookies in env var
    cookies_b64 = os.getenv('YOUTUBE_COOKIES')
    if cookies_b64:
        tmp = Path(tempfile.gettempdir()) / 'yt_cookies.txt'
        try:
            decoded = base64.b64decode(cookies_b64)
            tmp.write_bytes(decoded)
            print(f"🍪 Decoded cookies from env var ({len(decoded)} bytes) -> {tmp}")
            # Print first line to verify format
            first_line = decoded.decode('utf-8', errors='ignore').split('\n')[0]
            print(f"🍪 First line: {first_line[:60]}...")
            return str(tmp)
        except Exception as e:
            print(f"❌ Failed to decode YOUTUBE_COOKIES: {e}")
            return None
    
    # Option 2: cookies.txt file in worker dir
    if COOKIES_FILE.exists():
        print(f"🍪 Using cookies file: {COOKIES_FILE} ({COOKIES_FILE.stat().st_size} bytes)")
        return str(COOKIES_FILE)
    
    print("⚠️ No YouTube cookies found (set YOUTUBE_COOKIES env var)")
    return None


def _build_ydl_opts(base_opts: dict) -> dict:
    """Add cookies and common options to yt-dlp opts."""
    cookies_path = _get_cookies_path()
    if cookies_path:
        base_opts['cookiefile'] = cookies_path
    
    # Use extractor args to help bypass bot detection
    base_opts['extractor_args'] = {'youtube': {'player_client': ['web']}}
    
    return base_opts


# FFmpeg location - use env var or find in PATH
FFMPEG_LOCATION = os.getenv('FFMPEG_PATH')
if not FFMPEG_LOCATION:
    ffmpeg_exe = shutil.which('ffmpeg')
    if ffmpeg_exe:
        FFMPEG_LOCATION = str(Path(ffmpeg_exe).parent)


def download_audio(video_url: str) -> tuple[str, dict]:
    """
    Download audio from YouTube video for Gemini analysis.
    
    Returns:
        Tuple of (audio_path, video_info)
    """
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    
    ydl_opts = _build_ydl_opts({
        'format': 'bestaudio/best',
        'outtmpl': str(DOWNLOADS_DIR / '%(id)s_audio.%(ext)s'),
        'ffmpeg_location': FFMPEG_LOCATION,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '128',
        }],
        'quiet': False,
        'no_warnings': False,
        'nocheckcertificate': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
        }
    })
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        video_id = info['id']
        
        # Find the downloaded audio file
        audio_path = None
        for ext in ['mp3', 'webm', 'm4a', 'opus']:
            candidate = DOWNLOADS_DIR / f"{video_id}_audio.{ext}"
            if candidate.exists():
                audio_path = candidate
                break
        
        if not audio_path:
            raise FileNotFoundError(f"Downloaded audio file not found for {video_id}")
        
        video_info = {
            'id': video_id,
            'title': info.get('title', 'Unknown'),
            'duration': info.get('duration', 0),
            'uploader': info.get('uploader', 'Unknown'),
            'view_count': info.get('view_count', 0),
        }
        
        # Validate video duration (max 2 hours)
        from services.validation import validate_video_duration
        validate_video_duration(video_info['duration'])
        
        return str(audio_path), video_info


def download_video(video_url: str, video_id: str = None) -> str:
    """
    Download video for clipping.
    Uses best quality up to 720p to balance quality and speed.
    
    Returns:
        Path to downloaded video
    """
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    
    ydl_opts = _build_ydl_opts({
        'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]',
        'outtmpl': str(DOWNLOADS_DIR / '%(id)s_video.%(ext)s'),
        'ffmpeg_location': FFMPEG_LOCATION,
        'merge_output_format': 'mp4',
        'quiet': False,
        'no_warnings': False,
        'nocheckcertificate': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
        }
    })
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        if video_id:
            # If we already have id, just download
            info = ydl.extract_info(video_url, download=True)
        else:
            info = ydl.extract_info(video_url, download=True)
            video_id = info['id']
        
        # Find the downloaded video file
        video_path = DOWNLOADS_DIR / f"{video_id}_video.mp4"
        
        if not video_path.exists():
            # Try other extensions
            for ext in ['webm', 'mkv']:
                alt_path = DOWNLOADS_DIR / f"{video_id}_video.{ext}"
                if alt_path.exists():
                    video_path = alt_path
                    break
        
        if not video_path.exists():
            raise FileNotFoundError(f"Downloaded video file not found for {video_id}")
        
        return str(video_path)


def cleanup_audio(file_path: str) -> None:
    """Remove audio file after processing"""
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"🧹 Cleaned up: {file_path}")
    except Exception as e:
        print(f"⚠️ Failed to cleanup {file_path}: {e}")


def cleanup_video(file_path: str) -> None:
    """Remove video file after clipping"""
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"🧹 Cleaned up video: {file_path}")
    except Exception as e:
        print(f"⚠️ Failed to cleanup {file_path}: {e}")


def cleanup_all(video_id: str) -> None:
    """Remove all downloaded files for a video"""
    try:
        for pattern in [f"{video_id}_audio.*", f"{video_id}_video.*"]:
            for file in DOWNLOADS_DIR.glob(pattern):
                file.unlink()
                print(f"🧹 Cleaned up: {file}")
    except Exception as e:
        print(f"⚠️ Cleanup error: {e}")
