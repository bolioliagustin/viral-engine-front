"""
Audio transcription service.

Provider order (best → fallback):
  1. Groq Whisper Large v3 Turbo (cuando GROQ_API_KEY existe).
     Modelo más nuevo, ~10× más rápido, ~9× más barato y mejor accuracy
     que whisper-1 de OpenAI. API es OpenAI-compatible.
  2. OpenAI Whisper-1 como fallback.

Ambos soportan word-level timestamps (timestamp_granularities=["word"])
y el parámetro `prompt` para mejorar accuracy con contexto.
"""
import os
import re
from openai import OpenAI
from typing import Dict, List


# ── Provider clients ─────────────────────────────────────────────────────────
def _groq_client() -> 'OpenAI | None':
    """Returns a Groq client (OpenAI-compatible) if GROQ_API_KEY is set."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    return OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")


def _openai_client() -> 'OpenAI | None':
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    return OpenAI(api_key=api_key)


# Términos de marca frecuentemente mal transcritos por Whisper
_BRAND_PHONETIC_CORRECTIONS = {
    "claude": ["coulouse", "cloud", "claud", "clowd"],
    "claude code": ["cloud code", "claud code", "coulouse code"],
}


def build_whisper_vocabulary(
    video_title: str = "",
    hook: str = "",
    yt_slice: str = "",
    extra_terms: list[str] | None = None,
) -> list[str]:
    """
    Extrae términos propios para el prompt de Whisper y correcciones post-hoc.
    """
    terms: list[str] = []
    seen: set[str] = set()

    def _add(term: str):
        t = (term or "").strip()
        if not t or len(t) < 2:
            return
        key = t.lower()
        if key in seen:
            return
        seen.add(key)
        terms.append(t)

    for src in (video_title, hook, yt_slice):
        if not src:
            continue
        # Frases compuestas capitalizadas (Claude Code, Cloudflare, etc.)
        for m in re.finditer(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b", src):
            _add(m.group(0))
        for m in re.finditer(r"\b[A-Z][a-z]{2,}\b", src):
            _add(m.group(0))

    for term in extra_terms or []:
        _add(term)

    # Siempre incluir marcas IA comunes si aparecen en contexto
    combined = f"{video_title} {hook} {yt_slice}".lower()
    if "claude" in combined:
        _add("Claude")
        _add("Claude Code")

    return terms[:20]


def format_whisper_vocabulary_prompt(vocabulary: list[str], max_chars: int = 200) -> str:
    """Fragmento de prompt con vocabulario para Whisper."""
    if not vocabulary:
        return ""
    vocab_str = ", ".join(vocabulary[:15])
    if len(vocab_str) > max_chars:
        vocab_str = vocab_str[: max_chars - 3] + "..."
    return f"Vocabulario del video (transcribir exactamente): {vocab_str}."


def transcribe_with_whisper_openrouter(
    audio_path: str,
    prompt: str = None,
    language: str = None,
) -> Dict:
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
        return _transcribe_chunked(audio_path, audio, prompt=prompt, language=language)
    else:
        print(f"📝 Transcribing audio with OpenAI ({duration_seconds/60:.1f} min)...")
        return _transcribe_single(audio_path, prompt=prompt, language=language)


def _transcribe_with_provider(
    client: 'OpenAI', model: str, provider: str,
    audio_path: str, prompt: str = None, language: str = None,
) -> Dict:
    """Llama a la API de transcripción y retorna dict normalizado."""
    kwargs = {
        "model": model,
        "response_format": "verbose_json",
        "timestamp_granularities": ["segment", "word"],
    }
    if prompt:
        kwargs["prompt"] = prompt[:900]  # ~224 tokens
    if language:
        kwargs["language"] = language

    with open(audio_path, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(file=audio_file, **kwargs)

    result = (transcript.model_dump() if hasattr(transcript, "model_dump")
              else dict(transcript))
    return result


def _transcribe_single(audio_path: str, prompt: str = None, language: str = None) -> Dict:
    """
    Transcribe un audio. Prueba Groq primero (mejor/más rápido/barato),
    cae a OpenAI si Groq no está configurado o falla.
    """
    # ── Intento 1: Groq Whisper Large v3 Turbo ──────────────────────────────
    groq = _groq_client()
    if groq:
        try:
            print(f"📝 Transcribing with Groq (whisper-large-v3-turbo)...")
            result = _transcribe_with_provider(
                client=groq,
                model="whisper-large-v3-turbo",
                provider="groq",
                audio_path=audio_path,
                prompt=prompt,
                language=language,
            )
            _save_transcript(audio_path, result)
            n_words = len(result.get("words", []))
            n_segs = len(result.get("segments", []))
            print(f"✅ Groq transcription: {n_segs} segments, {n_words} words")
            print(f"   Language: {result.get('language', 'unknown')}, "
                  f"Duration: {result.get('duration', 'N/A')}s")
            return result
        except Exception as e:
            print(f"⚠️ Groq falló ({e}) — fallback a OpenAI")

    # ── Intento 2: OpenAI Whisper-1 ─────────────────────────────────────────
    openai = _openai_client()
    if not openai:
        raise RuntimeError(
            "Sin transcriber disponible: ni GROQ_API_KEY ni OPENAI_API_KEY"
        )
    try:
        print(f"📝 Transcribing with OpenAI (whisper-1)...")
        result = _transcribe_with_provider(
            client=openai,
            model="whisper-1",
            provider="openai",
            audio_path=audio_path,
            prompt=prompt,
            language=language,
        )
        _save_transcript(audio_path, result)
        n_words = len(result.get("words", []))
        n_segs = len(result.get("segments", []))
        print(f"✅ OpenAI transcription: {n_segs} segments, {n_words} words")
        print(f"   Language: {result.get('language', 'unknown')}, "
              f"Duration: {result.get('duration', 'N/A')}s")
        return result
    except Exception as e:
        print(f"❌ OpenAI transcription failed: {e}")
        raise


def _transcribe_chunked(audio_path: str, audio: 'AudioSegment', prompt: str = None, language: str = None) -> Dict:
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

            kwargs_c = {
                "model": "whisper-1",
                "response_format": "verbose_json",
                "timestamp_granularities": ["segment", "word"],
            }
            if prompt:
                kwargs_c["prompt"] = prompt[:900]
            if language:
                kwargs_c["language"] = language

            with open(chunk_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    file=audio_file,
                    **kwargs_c,
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


def format_transcript_for_prompt_compact(
    transcript: Dict,
    max_block_duration: float = 30.0,
    max_block_chars: int = 500,
    gap_threshold: float = 1.0,
    min_segment_duration: float = 0.5,
) -> str:
    """
    Versión compacta del formato del transcript para el prompt del AI.

    Mismo contenido pero con ~50% menos chars que `format_transcript_for_prompt`:
      - Mergea segmentos consecutivos cuando el gap entre ellos < gap_threshold
        y el bloque resultante no supera max_block_duration o max_block_chars
      - Timestamps a int seconds: `[5-30]: ...` en vez de `[5.2s - 30.1s]: ...`
      - Filtra labels auto-generados ([Music], [Applause], [silence])
      - Filtra segmentos muy cortos (< min_segment_duration) que suelen ser ruido

    Mantiene resolución temporal donde importa: NO mergea cuando hay pausas
    reales (gap > 1s) — eso preserva los boundaries que necesita el modelo
    para `surgical_clipping`.
    """
    if not transcript or 'segments' not in transcript:
        return "No transcript available"

    blocks = []
    current = None

    for seg in transcript['segments']:
        start = float(seg.get('start', 0))
        end = float(seg.get('end', start))
        text = (seg.get('text') or "").strip()

        # Filtros de ruido
        if not text:
            continue
        if end - start < min_segment_duration:
            continue
        if text.startswith("[") and text.endswith("]"):
            continue  # [Music], [Applause], [silence], etc

        if current is None:
            current = {"start": start, "end": end, "texts": [text]}
            continue

        gap = start - current["end"]
        merged_duration = end - current["start"]
        merged_chars = sum(len(t) for t in current["texts"]) + len(text) + len(current["texts"])

        # Mergear si: gap chico Y duración acumulada razonable Y chars OK
        can_merge = (
            gap < gap_threshold
            and merged_duration <= max_block_duration
            and merged_chars <= max_block_chars
        )
        if can_merge:
            current["end"] = end
            current["texts"].append(text)
        else:
            blocks.append(current)
            current = {"start": start, "end": end, "texts": [text]}

    if current is not None:
        blocks.append(current)

    lines = []
    for b in blocks:
        s = int(round(b["start"]))
        e = int(round(b["end"]))
        text = " ".join(b["texts"])
        lines.append(f"[{s}-{e}]: {text}")

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
