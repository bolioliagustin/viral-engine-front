"""
Video clipper service using FFmpeg
Extracts short clips from videos based on timestamps
"""
import subprocess
import os
import shutil
from pathlib import Path

# FFmpeg path - use env var or find in PATH
FFMPEG_PATH = os.getenv('FFMPEG_PATH', shutil.which('ffmpeg') or 'ffmpeg')

CLIPS_DIR = Path(__file__).parent.parent / "clips"


def extract_clip(
    input_path: str,
    start_time: int,
    end_time: int,
    output_path: str = None,
    video_id: str = None,
    moment_index: int = 0
) -> str:
    """
    Extract a clip from video using FFmpeg.
    Uses -c copy for instant extraction (no re-encoding).
    
    Args:
        input_path: Path to source video file
        start_time: Start time in seconds
        end_time: End time in seconds
        output_path: Optional output path
        video_id: Video ID for naming
        moment_index: Index of the viral moment
        
    Returns:
        Path to the extracted clip
    """
    CLIPS_DIR.mkdir(exist_ok=True)
    
    if output_path is None:
        output_path = str(CLIPS_DIR / f"{video_id}_clip_{moment_index}.mp4")
    
    # Calculate duration
    duration = end_time - start_time
    
    cmd = [
        FFMPEG_PATH,
        '-y',  # Overwrite output
        '-ss', str(start_time),  # Start time
        '-i', input_path,  # Input file
        '-t', str(duration),  # Duration
        '-c', 'copy',  # No re-encoding = fast
        '-avoid_negative_ts', 'make_zero',
        output_path
    ]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        print(f"✂️ Clip extracted: {output_path}")
        return output_path
    except subprocess.CalledProcessError as e:
        print(f"❌ FFmpeg error: {e.stderr}")
        # Try with re-encoding if copy fails (keyframe issues)
        return extract_clip_precise(input_path, start_time, end_time, output_path)


def extract_clip_precise(
    input_path: str,
    start_time: int,
    end_time: int,
    output_path: str
) -> str:
    """
    Extract clip with re-encoding for precise timing.
    Slower but frame-accurate.
    """
    duration = end_time - start_time
    
    cmd = [
        FFMPEG_PATH,
        '-y',
        '-ss', str(start_time),
        '-i', input_path,
        '-t', str(duration),
        '-c:v', 'libx264',  # Re-encode video
        '-c:a', 'aac',  # Re-encode audio
        '-preset', 'fast',
        '-crf', '23',
        output_path
    ]
    
    try:
        subprocess.run(cmd, capture_output=True, check=True)
        print(f"✂️ Clip extracted (precise): {output_path}")
        return output_path
    except subprocess.CalledProcessError as e:
        print(f"❌ FFmpeg precise extract failed: {e}")
        raise


def cleanup_clips(job_id: str) -> None:
    """Remove local clips after upload"""
    try:
        for clip_file in CLIPS_DIR.glob(f"{job_id}_clip_*.mp4"):
            clip_file.unlink()
            print(f"🧹 Cleaned up: {clip_file}")
    except Exception as e:
        print(f"⚠️ Cleanup error: {e}")
