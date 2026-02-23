"""
YouTube downloader service using yt-dlp
Downloads both audio (for Gemini) and video (for clipping)
"""
import yt_dlp
import os
import shutil
from pathlib import Path

DOWNLOADS_DIR = Path(__file__).parent.parent / "downloads"

# FFmpeg location - use env var or find in PATH
FFMPEG_LOCATION = os.getenv('FFMPEG_PATH')
if not FFMPEG_LOCATION:
    # Try to find ffmpeg in PATH
    ffmpeg_exe = shutil.which('ffmpeg')
    if ffmpeg_exe:
        # Get directory containing ffmpeg
        FFMPEG_LOCATION = str(Path(ffmpeg_exe).parent)


def download_audio(video_url: str) -> tuple[str, dict]:
    """
    Download audio from YouTube video for Gemini analysis.
    
    Returns:
        Tuple of (audio_path, video_info)
    """
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': str(DOWNLOADS_DIR / '%(id)s_audio.%(ext)s'),
        'ffmpeg_location': FFMPEG_LOCATION,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '128',
        }],
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
        }
    }
    
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
    
    ydl_opts = {
        'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]',
        'outtmpl': str(DOWNLOADS_DIR / '%(id)s_video.%(ext)s'),
        'ffmpeg_location': FFMPEG_LOCATION,
        'merge_output_format': 'mp4',
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
        }
    }
    
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
