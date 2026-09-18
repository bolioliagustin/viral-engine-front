"""
Audio utilities for chunking and processing long audio files
Enables transcription of long videos by splitting them into smaller segments
"""
from pydub import AudioSegment
from pathlib import Path
from typing import List, Tuple
import os


def split_audio_with_overlap(
    audio_path: str,
    chunk_duration_ms: int = 1200000,  # 20 minutes (safe under 25MB limit)
    overlap_ms: int = 30000  # 30 seconds
) -> List[Tuple[str, float]]:
    """
    Divide audio en chunks con solapamiento para videos largos
    
    Args:
        audio_path: Path to source audio file
        chunk_duration_ms: Duration of each chunk in milliseconds (default 20min)
        overlap_ms: Overlap between chunks in milliseconds (default 30s)
        
    Returns:
        List of (chunk_path, start_offset_seconds) tuples
    """
    print(f"📊 Splitting audio into chunks (20min each with 30s overlap)...")
    
    # Load audio
    audio = AudioSegment.from_file(audio_path)
    total_duration_ms = len(audio)
    total_duration_s = total_duration_ms / 1000
    
    print(f"   Total duration: {total_duration_s:.1f}s ({total_duration_s/60:.1f} min)")
    
    chunks = []
    start = 0
    chunk_index = 0
    
    # Get base path for chunks
    base_path = Path(audio_path)
    chunks_dir = base_path.parent / "chunks"
    chunks_dir.mkdir(exist_ok=True)
    
    while start < total_duration_ms:
        end = min(start + chunk_duration_ms, total_duration_ms)
        
        # Extract chunk
        chunk = audio[start:end]
        
        # Save chunk
        chunk_filename = f"{base_path.stem}_chunk_{chunk_index}.mp3"
        chunk_path = str(chunks_dir / chunk_filename)
        chunk.export(chunk_path, format="mp3")
        
        # Store chunk info (path, start offset in seconds)
        start_offset_s = start / 1000
        chunks.append((chunk_path, start_offset_s))
        
        chunk_duration_s = (end - start) / 1000
        print(f"   ✂️ Chunk {chunk_index}: {start_offset_s:.1f}s - {(end/1000):.1f}s ({chunk_duration_s:.1f}s)")
        
        chunk_index += 1
        
        # If we've reached the end, stop
        if end >= total_duration_ms:
            break
        
        # Next chunk starts before current end (overlap)
        start = end - overlap_ms
    
    print(f"✅ Created {len(chunks)} chunks")
    return chunks


def cleanup_chunks(chunks: List[Tuple[str, float]]):
    """
    Clean up temporary chunk files
    
    Args:
        chunks: List of (chunk_path, offset) tuples from split_audio_with_overlap
    """
    for chunk_path, _ in chunks:
        try:
            if os.path.exists(chunk_path):
                os.remove(chunk_path)
        except Exception as e:
            print(f"⚠️ Could not delete chunk {chunk_path}: {e}")
    
    # Try to remove chunks directory if empty
    try:
        chunks_dir = Path(chunk_path).parent
        if chunks_dir.exists() and not any(chunks_dir.iterdir()):
            chunks_dir.rmdir()
    except:
        pass


# ── W4: tramos del audio completo con ffmpeg (sin cargar el audio en memoria) ──
def probe_audio_duration_sec(audio_path: str) -> float:
    """Duración del archivo vía ffprobe (0.0 si no se puede leer)."""
    import json
    import subprocess
    from services.clip_generator import FFPROBE_PATH
    try:
        r = subprocess.run(
            [FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
             "-of", "json", audio_path],
            capture_output=True, text=True, timeout=60, check=True,
        )
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception as e:
        print(f"⚠️ ffprobe no pudo leer la duración de {audio_path}: {e}")
        return 0.0


def split_audio_ffmpeg(
    audio_path: str,
    chunk_sec: float = 600.0,
    overlap_sec: float = 5.0,
    out_dir: str | None = None,
    prefix: str | None = None,
) -> List[Tuple[str, float]]:
    """
    Parte el audio completo en tramos de `chunk_sec` con `overlap_sec` de
    solapamiento, como MP3 mono 16 kHz 64 kbps (≈ 4,8 MB por 10 min: entra en
    el límite de 25 MB de Groq). Usa ffmpeg con `-ss` antes de `-i` (seek
    rápido) en vez de pydub, que carga todo el audio en memoria.

    Cada tramo k cubre [k·chunk_sec, k·chunk_sec + chunk_sec + overlap_sec).
    Devuelve [(chunk_path, offset_sec)] en orden. El solapamiento se resuelve
    después en `transcriber.merge_chunk_transcripts`.
    """
    import subprocess
    from services.clip_generator import FFMPEG_PATH

    total = probe_audio_duration_sec(audio_path)
    if total <= 0:
        raise RuntimeError(f"No se pudo determinar la duración de {audio_path}")

    base = Path(audio_path)
    chunks_dir = Path(out_dir) if out_dir else base.parent / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    stem = prefix or base.stem

    chunks: List[Tuple[str, float]] = []
    offset = 0.0
    k = 0
    while offset < total:
        if k > 0 and total - offset <= overlap_sec:
            break  # lo que queda ya lo cubrió el solape del tramo anterior
        length = min(chunk_sec + overlap_sec, total - offset)
        out_path = chunks_dir / f"{stem}_full_{k:03d}.mp3"
        cmd = [
            FFMPEG_PATH, "-y", "-loglevel", "error",
            "-ss", f"{offset:.3f}", "-t", f"{length:.3f}",
            "-i", audio_path,
            "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k",
            "-acodec", "libmp3lame",
            str(out_path),
        ]
        subprocess.run(cmd, check=True, timeout=600, capture_output=True, text=True)
        chunks.append((str(out_path), offset))
        k += 1
        offset += chunk_sec
        if length < chunk_sec + overlap_sec:
            break

    print(f"✂️ Audio completo ({total/60:.1f} min) → {len(chunks)} tramos de "
          f"{chunk_sec/60:.0f} min (+{overlap_sec:.0f} s de solape)")
    return chunks
