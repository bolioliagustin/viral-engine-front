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


def _audio_duration_seconds(result: Dict, audio_path: str) -> float:
    """Duración facturable: verbose_json duration o longitud del archivo."""
    dur = result.get("duration")
    if dur is not None:
        try:
            return float(dur)
        except (TypeError, ValueError):
            pass
    try:
        from pydub import AudioSegment
        audio = AudioSegment.from_file(audio_path)
        return len(audio) / 1000.0
    except Exception:
        return 0.0


def _record_whisper_result(
    provider: str,
    model: str,
    result: Dict,
    audio_path: str,
    usage_task: str = "whisper",
) -> None:
    """Registra el evento de uso. `usage_task` distingue la transcripción del
    clip (`whisper`) del transcript completo de W4 (`transcript_full`)."""
    try:
        from services.usage_tracker import record_whisper_usage
        seconds = _audio_duration_seconds(result, audio_path)
        record_whisper_usage(provider, model, seconds, task=usage_task)
    except Exception:
        pass


def transcribe_with_whisper_openrouter(
    audio_path: str,
    prompt: str = None,
    language: str = None,
    provider: str | None = None,
    usage_task: str = "whisper",
) -> Dict:
    """
    Transcribe audio using Whisper (via OpenRouter) with precise timestamps
    For long videos (>8min), automatically splits into chunks to avoid file size limits
    
    Args:
        audio_path: Path to audio file (mp3, wav, etc.)
        provider: "groq" | "openai" | None. None = cascada actual (Groq → OpenAI).
            Un valor fuerza ese proveedor (W1: re-transcribir con el OTRO cuando
            los timestamps del primero son sospechosos). Solo aplica al camino
            single (≤ 20 min); el chunked sigue en OpenAI.
        usage_task: nombre del evento en job_usage_events ("whisper" por
            defecto; "transcript_full" para los tramos del Transcript de W4).
        
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
        return _transcribe_single(
            audio_path, prompt=prompt, language=language, provider=provider,
            usage_task=usage_task,
        )


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


def _transcribe_single(
    audio_path: str,
    prompt: str = None,
    language: str = None,
    provider: str | None = None,
    usage_task: str = "whisper",
) -> Dict:
    """
    Transcribe un audio. Prueba Groq primero (mejor/más rápido/barato),
    cae a OpenAI si Groq no está configurado o falla.

    `provider` fuerza uno solo ("groq" | "openai"); si no está configurado se
    levanta RuntimeError en vez de caer al otro. El dict devuelto incluye
    `provider` con el que efectivamente transcribió.
    """
    if provider not in (None, "groq", "openai"):
        raise ValueError(f"provider inválido: {provider!r}")

    # ── Intento 1: Groq Whisper Large v3 Turbo ──────────────────────────────
    groq = _groq_client() if provider in (None, "groq") else None
    if provider == "groq" and not groq:
        raise RuntimeError("GROQ_API_KEY no configurada: no se puede forzar provider=groq")
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
            _record_whisper_result("groq", "whisper-large-v3-turbo", result, audio_path, usage_task)
            result["provider"] = "groq"
            return result
        except Exception as e:
            if provider == "groq":
                raise
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
        _record_whisper_result("openai", "whisper-1", result, audio_path, usage_task)
        result["provider"] = "openai"
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
            _record_whisper_result("openai", "whisper-1", chunk_result, chunk_path)

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


# ── W4: transcript del audio COMPLETO por tramos ────────────────────────────
FULL_CHUNK_SEC = 600.0        # tramos de 10 min (≈ 4,8 MB de MP3 mono 16 kHz)
FULL_OVERLAP_SEC = 5.0        # solape entre tramos; la costura se resuelve por tiempo
FULL_MAX_PARALLEL = 3         # tramos en paralelo (rate limits de Groq)
FULL_SEAM_BAND_SEC = 0.25     # ± alrededor de la costura: ambos tramos aportan, se deduplica


# Whisper devuelve `language` como nombre ("spanish"), pero el parámetro
# `language` de la API exige ISO-639-1 ("es"). Lista de idiomas de Whisper.
_WHISPER_LANGUAGE_CODES = {
    "english": "en", "chinese": "zh", "german": "de", "spanish": "es", "russian": "ru",
    "korean": "ko", "french": "fr", "japanese": "ja", "portuguese": "pt", "turkish": "tr",
    "polish": "pl", "catalan": "ca", "dutch": "nl", "arabic": "ar", "swedish": "sv",
    "italian": "it", "indonesian": "id", "hindi": "hi", "finnish": "fi", "vietnamese": "vi",
    "hebrew": "he", "ukrainian": "uk", "greek": "el", "malay": "ms", "czech": "cs",
    "romanian": "ro", "danish": "da", "hungarian": "hu", "tamil": "ta", "norwegian": "no",
    "thai": "th", "urdu": "ur", "croatian": "hr", "bulgarian": "bg", "lithuanian": "lt",
    "latin": "la", "maori": "mi", "malayalam": "ml", "welsh": "cy", "slovak": "sk",
    "telugu": "te", "persian": "fa", "latvian": "lv", "bengali": "bn", "serbian": "sr",
    "azerbaijani": "az", "slovenian": "sl", "kannada": "kn", "estonian": "et",
    "macedonian": "mk", "breton": "br", "basque": "eu", "icelandic": "is", "armenian": "hy",
    "nepali": "ne", "mongolian": "mn", "bosnian": "bs", "kazakh": "kk", "albanian": "sq",
    "swahili": "sw", "galician": "gl", "marathi": "mr", "punjabi": "pa", "sinhala": "si",
    "khmer": "km", "shona": "sn", "yoruba": "yo", "somali": "so", "afrikaans": "af",
    "occitan": "oc", "georgian": "ka", "belarusian": "be", "tajik": "tg", "sindhi": "sd",
    "gujarati": "gu", "amharic": "am", "yiddish": "yi", "lao": "lo", "uzbek": "uz",
    "faroese": "fo", "haitian creole": "ht", "pashto": "ps", "turkmen": "tk", "nynorsk": "nn",
    "maltese": "mt", "sanskrit": "sa", "luxembourgish": "lb", "myanmar": "my", "tibetan": "bo",
    "tagalog": "tl", "malagasy": "mg", "assamese": "as", "tatar": "tt", "hawaiian": "haw",
    "lingala": "ln", "hausa": "ha", "bashkir": "ba", "javanese": "jv", "sundanese": "su",
    "cantonese": "yue", "burmese": "my", "valencian": "ca", "flemish": "nl", "haitian": "ht",
    "letzeburgesch": "lb", "pushto": "ps", "panjabi": "pa", "moldavian": "ro", "moldovan": "ro",
    "sinhalese": "si", "castilian": "es",
}


def iso_language_code(lang: str | None) -> str | None:
    """'Spanish' / 'spanish' / 'es-419' / 'es' → 'es'; None si no se reconoce."""
    if not lang:
        return None
    t = str(lang).strip().lower()
    if not t:
        return None
    if len(t) <= 3 and "-" not in t:
        return t
    if "-" in t and len(t.split("-")[0]) <= 3:
        return t.split("-")[0]
    return _WHISPER_LANGUAGE_CODES.get(t)


def full_transcript_model() -> str:
    """Modelo con el que se cachea el Transcript completo: Groq si hay clave, si no whisper-1."""
    return "whisper-large-v3-turbo" if os.getenv("GROQ_API_KEY") else "whisper-1"


def _shift_items(items: List[Dict], offset: float) -> List[Dict]:
    out = []
    for it in items or []:
        it2 = dict(it)
        it2["start"] = round(float(it2.get("start", 0)) + offset, 3)
        it2["end"] = round(float(it2.get("end", it2["start"])) + offset, 3)
        out.append(it2)
    return out


def _dedupe_seam_words(words: List[Dict], seams: List[float], band_sec: float, window: int = 6) -> List[Dict]:
    """
    Saca los duplicados de la banda de la costura SIN reordenar por tiempo
    (los tiempos por palabra de Groq tienen jitter en los bordes de segmento y
    ordenar por `start` cambiaba el orden del texto). Solo mira las palabras
    cuyo centro cae a ≤ `band_sec` + 0,5 s de una costura: una de ellas se
    descarta si repite (sin puntuación ni mayúsculas) a una de las últimas
    `window` palabras con start a < 0,5 s. Lejos de las costuras no se toca
    nada: "qué es lo que" tiene dos "que" a < 0,5 s y son legítimos.
    """
    from services.transcript_lines import _norm
    reach = band_sec + 0.5

    def _near_seam(w: Dict) -> bool:
        mid = (float(w.get("start", 0)) + float(w.get("end", 0))) / 2
        return any(abs(mid - seam) <= reach for seam in seams)

    out: List[Dict] = []
    for w in words:
        if _near_seam(w):
            w_norm = _norm(w.get("word", ""))
            w_start = float(w.get("start", 0))
            if any(
                _near_seam(prev)
                and w_norm == _norm(prev.get("word", ""))
                and abs(w_start - float(prev.get("start", 0))) < 0.5
                for prev in out[-window:]
            ):
                continue
        out.append(w)
    return out


def merge_chunk_transcripts(
    chunks: List[tuple],
    overlap_sec: float = FULL_OVERLAP_SEC,
    seam_band_sec: float = FULL_SEAM_BAND_SEC,
) -> Dict:
    """
    Une los resultados de los tramos (`[(offset_sec, result_local), ...]`, en
    orden) en una sola línea de tiempo, sin duplicar las palabras de las
    costuras.

    La costura entre el tramo k y el k+1 está en la mitad del solape
    (`offset_{k+1} + overlap/2`): el tramo k aporta las palabras cuyo centro
    cae antes de la costura (+ banda) y el k+1 las de después (− banda). La
    banda evita perder una palabra que quedó justo en la costura con tiempos
    distintos en cada tramo; el duplicado que pueda quedar en la banda lo
    saca `_deduplicate_words` (mismo texto, start a < 0,5 s).
    """
    if not chunks:
        return {"words": [], "segments": [], "language": None, "duration": 0.0}

    offsets = [float(off) for off, _ in chunks]
    seams = [offsets[i + 1] + overlap_sec / 2 for i in range(len(chunks) - 1)]

    def _keep(item: Dict, k: int) -> bool:
        mid = (float(item.get("start", 0)) + float(item.get("end", 0))) / 2
        lower = seams[k - 1] - seam_band_sec if k > 0 else float("-inf")
        upper = seams[k] + seam_band_sec if k < len(seams) else float("inf")
        return lower <= mid < upper

    all_words: List[Dict] = []
    all_segments: List[Dict] = []
    language = None
    for k, (offset, result) in enumerate(chunks):
        result = result or {}
        if language is None and result.get("language"):
            language = result["language"]
        for w in _shift_items(result.get("words") or [], float(offset)):
            if _keep(w, k):
                all_words.append(w)
        for sg in _shift_items(result.get("segments") or [], float(offset)):
            if _keep(sg, k):
                all_segments.append(sg)

    all_words = _dedupe_seam_words(all_words, seams, seam_band_sec)
    all_segments = _deduplicate_segments(all_segments)
    for i, sg in enumerate(all_segments):
        sg["id"] = i

    last_off, last_res = chunks[-1]
    duration = float(last_off) + float((last_res or {}).get("duration") or 0)
    if all_words:
        duration = max(duration, float(all_words[-1].get("end", 0)))
    return {
        "words": all_words,
        "segments": all_segments,
        "language": language,
        "duration": round(duration, 3),
    }


def transcribe_full_audio(
    audio_path: str,
    *,
    prompt: str | None = None,
    language: str | None = None,
    provider: str | None = None,
    chunk_sec: float = FULL_CHUNK_SEC,
    overlap_sec: float = FULL_OVERLAP_SEC,
    max_parallel: int = FULL_MAX_PARALLEL,
    usage_task: str = "transcript_full",
    chunks_dir: str | None = None,
) -> Dict:
    """
    Transcript del audio COMPLETO (W4): parte el audio en tramos de
    `chunk_sec` con `overlap_sec` de solape, transcribe cada tramo con
    `transcribe_with_whisper_openrouter` (Groq `whisper-large-v3-turbo` →
    fallback OpenAI `whisper-1`, `verbose_json` con palabras; misma cascada y
    reintentos que la transcripción del clip) hasta `max_parallel` a la vez, y
    une los tramos sin duplicar palabras (`merge_chunk_transcripts`).

    Si no se pasa `language`, el primer tramo se transcribe solo para detectar
    el idioma y fijarlo en los demás (evita que un tramo salga en otro idioma).

    Devuelve `{"words", "segments", "language", "duration", "providers",
    "audio_seconds", "cost_usd", "elapsed_sec", "n_chunks"}` en la línea de
    tiempo del audio. Las palabras vienen SIN puntuación y sin silencios: eso
    lo agrega `transcript_lines.build_full_transcript`.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor
    from services.audio_utils import split_audio_ffmpeg, cleanup_chunks
    from config.pricing import estimate_whisper_cost_usd

    t0 = time.time()
    chunks = split_audio_ffmpeg(
        audio_path, chunk_sec=chunk_sec, overlap_sec=overlap_sec, out_dir=chunks_dir,
    )
    results: List[Dict | None] = [None] * len(chunks)

    def _one(idx: int, lang: str | None) -> Dict:
        path, offset = chunks[idx]
        print(f"🎙️ Tramo {idx + 1}/{len(chunks)} (offset {offset/60:.1f} min)...")
        return transcribe_with_whisper_openrouter(
            path, prompt=prompt, language=lang, provider=provider, usage_task=usage_task,
        )

    try:
        lang = iso_language_code(language)
        start_idx = 0
        if not lang:
            results[0] = _one(0, None)
            detected = results[0].get("language") or None
            lang = iso_language_code(detected)
            start_idx = 1
            print(f"   🌐 Idioma detectado en el tramo 1: {detected!r} → {lang or 'sin fijar'}")

        pending = list(range(start_idx, len(chunks)))
        if pending:
            with ThreadPoolExecutor(max_workers=max(1, min(max_parallel, len(pending)))) as pool:
                for idx, res in zip(pending, pool.map(lambda i: _one(i, lang), pending)):
                    results[idx] = res

        merged = merge_chunk_transcripts(
            [(off, results[i]) for i, (_, off) in enumerate(chunks)],
            overlap_sec=overlap_sec,
        )
    finally:
        for path, _ in chunks:
            tj = path.replace(".mp3", "_transcript.json")  # lo deja _save_transcript
            if os.path.exists(tj):
                try:
                    os.remove(tj)
                except OSError:
                    pass
        cleanup_chunks(chunks)

    providers = sorted({(r or {}).get("provider") or "?" for r in results})
    audio_seconds = sum(float((r or {}).get("duration") or 0) for r in results)
    cost = sum(
        estimate_whisper_cost_usd((r or {}).get("provider") or "groq", float((r or {}).get("duration") or 0))
        for r in results
    )
    merged["language"] = iso_language_code(merged.get("language")) or merged.get("language")
    merged.update({
        "providers": providers,
        "audio_seconds": round(audio_seconds, 1),
        "cost_usd": round(cost, 4),
        "elapsed_sec": round(time.time() - t0, 1),
        "n_chunks": len(chunks),
    })
    print(f"✅ Transcript completo: {len(merged['words'])} palabras, {len(merged['segments'])} segmentos, "
          f"{merged['duration']/60:.1f} min de audio en {merged['elapsed_sec']:.0f} s, "
          f"~US${cost:.4f} ({'/'.join(providers)})")
    return merged


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
    Criterio: si dos palabras tienen el mismo texto (sin puntuación ni
    mayúsculas: las palabras de W4 traen la puntuación pegada) y su start
    difiere <0.5s, se considera duplicado.
    """
    if not words:
        return words
    from services.transcript_lines import _norm
    sorted_words = sorted(words, key=lambda w: float(w.get('start', 0)))
    dedup = []
    for w in sorted_words:
        if dedup:
            last = dedup[-1]
            same_text = _norm(w.get('word', '')) == _norm(last.get('word', ''))
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


