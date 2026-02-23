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
