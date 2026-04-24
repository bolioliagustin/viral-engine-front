"""
Audio transcription service using Whisper via OpenRouter
Provides precise timestamps for viral moment detection
"""
import os
from openai import OpenAI
from typing import Dict, List


def transcribe_with_whisper_openrouter(audio_path: str) -> Dict:
    """
    Transcribe audio using Whisper (via OpenRouter) with precise timestamps
    For long videos (>8min), automatically splits into chunks to avoid file size limits
    
    Args:
        audio_path: Path to audio file (mp3, wav, etc.)
        
    Returns:
        {
            "text": "Full transcription",
            "segments": [
                {
                    "id": 0,
                    "start": 0.5,
                    "end": 3.2,
                    "text": "Hola, bienvenidos"
                },
                ...
            ],
            "language": "es"
        }
    """
    # Check audio duration to determine if chunking is needed
    from pydub import AudioSegment
    audio = AudioSegment.from_file(audio_path)
    duration_seconds = len(audio) / 1000
    
    # If video is longer than 20 minutes, use chunking
    if duration_seconds > 1200:  # 20 minutes
        print(f"📝 Long video detected ({duration_seconds/60:.1f} min), using chunked transcription...")
        return _transcribe_chunked(audio_path, audio)
    else:
        print(f"📝 Transcribing audio with OpenAI ({duration_seconds/60:.1f} min)...")
        return _transcribe_single(audio_path)


def _transcribe_single(audio_path: str) -> Dict:
    """Transcribe a single audio file (no chunking needed)"""
    # Use OpenAI directly for Whisper (OpenRouter doesn't support audio transcription)
    client = OpenAI(
        api_key=os.getenv("OPENAI_API_KEY")
    )
    
    try:
        with open(audio_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",  # Supports verbose_json with timestamps
                file=audio_file,
                response_format="verbose_json",  # Incluye segments con timestamps
                timestamp_granularities=["segment", "word"],  # ← timestamps por palabra
            )

        result = transcript.model_dump() if hasattr(transcript, 'model_dump') else dict(transcript)

        # Save transcript to file for review
        _save_transcript(audio_path, result)

        n_words = len(result.get('words', []))
        print(f"✅ Transcription complete: {len(result.get('segments', []))} segments, {n_words} words")
        print(f"   Language: {result.get('language', 'unknown')}")
        print(f"   Duration: {result.get('duration', 'N/A')}s")
        
        return result
        
    except Exception as e:
        print(f"❌ Transcription failed: {e}")
        raise


def _transcribe_chunked(audio_path: str, audio: 'AudioSegment') -> Dict:
    """
    Transcribe long audio by splitting into chunks with overlap
    
    Args:
        audio_path: Path to original audio file
        audio: Loaded AudioSegment object
        
    Returns:
        Combined transcript with adjusted timestamps
    """
    from services.audio_utils import split_audio_with_overlap, cleanup_chunks
    
    # Split audio into 2-minute chunks with 15s overlap
    chunks = split_audio_with_overlap(audio_path)
    
    # Use OpenAI directly for Whisper
    client = OpenAI(
        api_key=os.getenv("OPENAI_API_KEY")
    )
    
    all_segments = []
    all_words = []
    full_text = []
    detected_language = None

    try:
        for chunk_index, (chunk_path, offset_seconds) in enumerate(chunks):
            print(f"\n🔍 Transcribing chunk {chunk_index + 1}/{len(chunks)}...")

            with open(chunk_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",  # Supports verbose_json with timestamps
                    file=audio_file,
                    response_format="verbose_json",
                    timestamp_granularities=["segment", "word"],
                )

            chunk_result = transcript.model_dump() if hasattr(transcript, 'model_dump') else dict(transcript)

            # Detect language from first chunk
            if detected_language is None:
                detected_language = chunk_result.get('language', 'es')

            # Adjust timestamps with offset and add to all_segments
            for segment in chunk_result.get('segments', []):
                adjusted_segment = segment.copy()
                adjusted_segment['start'] += offset_seconds
                adjusted_segment['end'] += offset_seconds
                all_segments.append(adjusted_segment)

            # Adjust word-level timestamps also
            for word in chunk_result.get('words', []) or []:
                adjusted_word = word.copy()
                adjusted_word['start'] = float(adjusted_word.get('start', 0)) + offset_seconds
                adjusted_word['end'] = float(adjusted_word.get('end', 0)) + offset_seconds
                all_words.append(adjusted_word)

            full_text.append(chunk_result.get('text', ''))
            n_words_chunk = len(chunk_result.get('words', []) or [])
            print(f"   ✅ Chunk {chunk_index + 1}: {len(chunk_result.get('segments', []))} segments, {n_words_chunk} words")

        # Deduplicate segments in overlap zones
        all_segments = _deduplicate_segments(all_segments)
        # Deduplicate words (by approximate start time)
        all_words = _deduplicate_words(all_words)

        # Combine results
        combined_result = {
            "text": " ".join(full_text),
            "segments": all_segments,
            "words": all_words,
            "language": detected_language,
            "duration": len(audio) / 1000
        }
        
        # Save combined transcript
        _save_transcript(audio_path, combined_result)
        
        print(f"\n✅ Chunked transcription complete:")
        print(f"   Total segments: {len(all_segments)}")
        print(f"   Language: {detected_language}")
        print(f"   Duration: {combined_result['duration']:.1f}s")
        
        return combined_result
        
    finally:
        # Clean up chunk files
        cleanup_chunks(chunks)


def _deduplicate_segments(segments: List[Dict]) -> List[Dict]:
    """
    Remove duplicate segments that appear in overlap zones
    Keep segments with earlier start times when duplicates are found
    """
    if not segments:
        return segments
    
    # Sort by start time
    sorted_segments = sorted(segments, key=lambda s: s['start'])
    
    deduplicated = []
    overlap_threshold = 5.0  # 5 seconds tolerance for considering segments as duplicates
    
    for segment in sorted_segments:
        # Check if this segment is too similar to the last added one
        if deduplicated:
            last_segment = deduplicated[-1]
            time_diff = abs(segment['start'] - last_segment['start'])
            
            # If segments start within 5 seconds and have similar text, skip duplicate
            if time_diff < overlap_threshold:
                text_similarity = _text_similarity(segment.get('text', ''), last_segment.get('text', ''))
                if text_similarity > 0.7:  # 70% similar
                    continue
        
        deduplicated.append(segment)
    
    print(f"   🔄 Deduplication: {len(sorted_segments)} → {len(deduplicated)} segments")
    return deduplicated


def _deduplicate_words(words: List[Dict]) -> List[Dict]:
    """
    Elimina palabras duplicadas que aparecen en zonas de overlap entre chunks.
    Criterio: si dos palabras tienen el mismo texto y su start difiere <0.5s,
    se considera duplicado.
    """
    if not words:
        return words
    sorted_words = sorted(words, key=lambda w: float(w.get('start', 0)))
    dedup = []
    for w in sorted_words:
        if dedup:
            last = dedup[-1]
            same_text = (w.get('word', '').strip().lower()
                         == last.get('word', '').strip().lower())
            close_time = abs(float(w.get('start', 0)) - float(last.get('start', 0))) < 0.5
            if same_text and close_time:
                continue
        dedup.append(w)
    return dedup


def _text_similarity(text1: str, text2: str) -> float:
    """Calculate simple text similarity (0.0 to 1.0)"""
    if not text1 or not text2:
        return 0.0
    
    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())
    
    if not words1 or not words2:
        return 0.0
    
    intersection = words1.intersection(words2)
    union = words1.union(words2)
    
    return len(intersection) / len(union)


def _save_transcript(audio_path: str, result: Dict):
    """Save transcript to JSON file for review"""
    transcript_file = audio_path.replace('.mp3', '_transcript.json').replace('.wav', '_transcript.json')
    import json
    with open(transcript_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"💾 Transcript saved to: {transcript_file}")


def format_transcript_for_prompt(transcript: Dict) -> str:
    """
    Format transcript segments into readable timeline for AI prompt
    
    Args:
        transcript: Output from transcribe_with_whisper_openrouter
        
    Returns:
        Formatted string with timestamps and text
    """
    if not transcript or 'segments' not in transcript:
        return "No transcript available"
    
    lines = []
    for segment in transcript['segments']:
        start = segment.get('start', 0)
        end = segment.get('end', 0)
        text = segment.get('text', '').strip()
        
        lines.append(f"[{start:.1f}s - {end:.1f}s]: {text}")
    
    return "\n".join(lines)


def find_phrase_in_transcript(phrase: str, transcript: Dict, tolerance_seconds: float = 2.0) -> Dict:
    """
    Find a phrase in the transcript and return its exact timestamps
    
    Args:
        phrase: Text to search for
        transcript: Full transcript data
        tolerance_seconds: How much time difference is acceptable
        
    Returns:
        {
            "found": True/False,
            "start": 123.5,
            "end": 126.8,
            "segment_text": "Full text of matching segment"
        }
    """
    phrase_lower = phrase.lower()
    
    for segment in transcript.get('segments', []):
        segment_text = segment.get('text', '').lower()
        
        if phrase_lower in segment_text:
            return {
                "found": True,
                "start": segment.get('start'),
                "end": segment.get('end'),
                "segment_text": segment.get('text')
            }
    
    return {"found": False}
