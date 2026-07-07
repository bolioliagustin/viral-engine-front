"""
Clip Generator — Sprint 1.1 (nuevo pipeline de clips verticales)

Pipeline completo: video_full.mp4 + timestamps → MP4 vertical 9:16 con subtitulos.

Funciones (por dia del sprint):
  - cut_clip()          ← Dia 2: recorte preciso por timestamps
  - to_vertical_9_16()  ← Dia 3: conversion 9:16 con fondo blur
  - burn_subtitles()    ← Dia 4: subtitulos quemados desde SRT
  - burn_overlay_text() ← Dia 5: hook text en primeros segundos
  - generate_clip()     ← Dia 6: pipeline completo (orquesta todo)

Este modulo reemplaza a clipper.py (legacy, usa -c copy sin re-encode).
El nuevo pipeline siempre re-encodea para frame accuracy.
"""
import subprocess
import os
import shutil
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Optional


def _resolve_bin(env_var: str, name: str) -> str:
    """
    Resuelve el path de un binario (ffmpeg/ffprobe) con fallback seguro.
    Si el env var apunta a un path inexistente (ej: path de Windows en Linux),
    cae al ejecutable en PATH en vez de explotar en runtime.
    """
    env_val = os.getenv(env_var)
    if env_val and Path(env_val).exists():
        return env_val
    on_path = shutil.which(name)
    if on_path:
        return on_path
    # Último recurso: nombre plano (falla clara en runtime si tampoco está)
    return name


FFMPEG_PATH = _resolve_bin('FFMPEG_PATH', 'ffmpeg')
FFPROBE_PATH = _resolve_bin('FFPROBE_PATH', 'ffprobe')

CLIPS_DIR = Path(__file__).parent.parent / "clips"


@dataclass
class ClipMetadata:
    """Metadata del clip generado, util para validacion y logging."""
    path: str
    duration_sec: float
    size_mb: float
    width: int
    height: int
    has_audio: bool

    @property
    def aspect_ratio(self) -> str:
        if self.height == 0:
            return "unknown"
        ratio = self.width / self.height
        if abs(ratio - 9/16) < 0.05:
            return "9:16"
        if abs(ratio - 16/9) < 0.05:
            return "16:9"
        if abs(ratio - 1.0) < 0.05:
            return "1:1"
        return f"{self.width}x{self.height}"


class ClipGenerationError(Exception):
    """Error al generar un clip."""
    pass


def probe_video(video_path: str) -> ClipMetadata:
    """Extrae metadata del video usando ffprobe."""
    if not os.path.exists(video_path):
        raise ClipGenerationError(f"Video no encontrado: {video_path}")

    cmd = [
        FFPROBE_PATH,
        '-v', 'quiet',
        '-print_format', 'json',
        '-show_format',
        '-show_streams',
        video_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        raise ClipGenerationError(f"ffprobe fallo: {e.stderr}")

    video_stream = next((s for s in data.get('streams', []) if s.get('codec_type') == 'video'), None)
    audio_stream = next((s for s in data.get('streams', []) if s.get('codec_type') == 'audio'), None)

    if not video_stream:
        raise ClipGenerationError(f"No hay stream de video en {video_path}")

    duration = float(data.get('format', {}).get('duration', 0))
    size_bytes = int(data.get('format', {}).get('size', 0))

    return ClipMetadata(
        path=video_path,
        duration_sec=duration,
        size_mb=size_bytes / (1024 * 1024),
        width=int(video_stream.get('width', 0)),
        height=int(video_stream.get('height', 0)),
        has_audio=audio_stream is not None,
    )


def cut_clip(
    video_path: str,
    start_sec: float,
    end_sec: float,
    output_path: Optional[str] = None,
    video_id: Optional[str] = None,
    moment_index: int = 0,
) -> ClipMetadata:
    """
    Corta un clip del video original con re-encode (frame accurate).

    A diferencia del clipper.py legacy (-c copy), este SIEMPRE re-encodea
    porque:
      1. -c copy solo corta en keyframes, no es preciso
      2. Necesitamos asegurar codec/resolucion consistente para el pipeline siguiente
      3. Moving viral content requires exact timing

    Args:
        video_path: Path al MP4 origen
        start_sec: Segundo de inicio (ej: 45.2)
        end_sec: Segundo de fin (ej: 75.8)
        output_path: Path de salida (opcional, se genera automatico si no se pasa)
        video_id: ID del video (para naming si no hay output_path)
        moment_index: Indice del momento viral (para naming)

    Returns:
        ClipMetadata con info del clip generado

    Raises:
        ClipGenerationError si falla el corte o validacion
    """
    # Validaciones
    if not os.path.exists(video_path):
        raise ClipGenerationError(f"Video origen no existe: {video_path}")

    if start_sec < 0:
        raise ClipGenerationError(f"start_sec no puede ser negativo: {start_sec}")

    duration = end_sec - start_sec
    if duration <= 0:
        raise ClipGenerationError(f"Duracion invalida: start={start_sec}, end={end_sec}")

    if duration < 3:
        raise ClipGenerationError(f"Clip demasiado corto (<3s): {duration}s")

    if duration > 180:
        raise ClipGenerationError(f"Clip demasiado largo (>3min): {duration}s")

    # Verificar que el rango este dentro del video
    source_meta = probe_video(video_path)
    if end_sec > source_meta.duration_sec + 1:  # +1s margen
        raise ClipGenerationError(
            f"end_sec ({end_sec}) excede duracion del video ({source_meta.duration_sec:.1f}s)"
        )

    # Generar output path si no se paso
    if output_path is None:
        CLIPS_DIR.mkdir(exist_ok=True)
        vid_part = video_id or Path(video_path).stem
        output_path = str(CLIPS_DIR / f"{vid_part}_moment_{moment_index}_cut.mp4")

    # Asegurar que el directorio existe
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # FFmpeg con re-encode preciso
    # -ss DESPUES de -i = seek preciso pero mas lento
    # Para clips <60s no es problema, ganamos precision
    # -threads 2: limita uso de memoria (default = all cores → buffers enormes en RAM)
    # -preset veryfast: ~30% menos RAM que 'fast', calidad casi idéntica para clips cortos
    # -loglevel error: evita bufferar cientos de KB de progress output en capture_output
    cmd = [
        FFMPEG_PATH,
        '-y', '-loglevel', 'error',
        '-i', video_path,
        '-ss', f'{start_sec:.3f}',
        '-to', f'{end_sec:.3f}',
        '-c:v', 'libx264',
        '-preset', 'veryfast',
        '-threads', '2',
        '-crf', '23',
        '-c:a', 'aac',
        '-b:a', '128k',
        '-movflags', '+faststart',
        '-avoid_negative_ts', 'make_zero',
        output_path,
    ]

    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                       check=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise ClipGenerationError(f"Timeout cortando clip (>5min)")
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode(errors='replace') if isinstance(e.stderr, bytes) else (e.stderr or '')
        raise ClipGenerationError(f"FFmpeg fallo: {err[-500:]}")

    if not os.path.exists(output_path):
        raise ClipGenerationError(f"FFmpeg termino pero el archivo no existe: {output_path}")

    # Validar el clip generado
    clip_meta = probe_video(output_path)

    # Tolerancia de 0.5s en la duracion final (re-encoding puede ajustar)
    if abs(clip_meta.duration_sec - duration) > 1.0:
        print(f"⚠️ Duracion no coincide: esperado {duration:.1f}s, obtenido {clip_meta.duration_sec:.1f}s")

    return clip_meta


WHISPER_HALLUCINATIONS = (
    "amara", "amara.org", "subtítulos por la comunidad",
    "subtitles by the amara.org community",
    "subtítulos realizados por la comunidad",
    "thanks for watching", "thank you for watching",
    "gracias por ver", "like and subscribe", "suscríbete al canal",
)


def extract_whisper_audio(video_path: str, output_mp3: str) -> None:
    """Extrae audio mono 16kHz de un clip ya cortado (sin seeking — sync exacto)."""
    cmd = [
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-i", video_path,
        "-vn",
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-acodec", "libmp3lame",
        "-ar", "16000", "-ac", "1", "-b:a", "128k",
        output_mp3,
    ]
    subprocess.run(cmd, check=True, timeout=120, capture_output=True, text=True)


_WORD_CORRECTION_TOLERANCE = 0.05


def apply_word_corrections(
    words: list[dict],
    corrections: list[dict],
    tolerance: float = _WORD_CORRECTION_TOLERANCE,
) -> list[dict]:
    """
    Apply user word_corrections to whisper word list.

    corrections format: [{start, end, original, corrected, index?}]
    Matches by start/end tolerance (~0.05s) or optional index fallback.
    """
    if not words:
        return []
    if not corrections:
        return [dict(w) for w in words]

    result = [dict(w) for w in words]
    for corr in corrections:
        corrected = corr.get("corrected")
        if corrected is None:
            continue

        c_start = float(corr.get("start", -1))
        c_end = float(corr.get("end", c_start))
        matched = False

        for w in result:
            ws = float(w.get("start", 0))
            we = float(w.get("end", ws))
            if abs(ws - c_start) <= tolerance and abs(we - c_end) <= tolerance:
                w["word"] = corrected
                matched = True
                break

        if not matched and corr.get("index") is not None:
            idx = int(corr["index"])
            if 0 <= idx < len(result):
                result[idx]["word"] = corrected

    return result


def snap_trim_bounds(
    words: list[dict],
    clip_duration: float,
    silence_threshold: float = 2.0,
    start_pad: float = 0.3,
    end_pad: float = 0.5,
) -> tuple[float, float]:
    """
    Snap clip bounds to speech, trimming leading/trailing silence > threshold.

    Returns (trim_start, trim_end) within [0, clip_duration].
    """
    if not words or clip_duration <= 0:
        return 0.0, clip_duration

    first_start = float(words[0].get("start", 0))
    last_end = float(words[-1].get("end", first_start))

    trim_start = 0.0
    if first_start > silence_threshold:
        trim_start = max(0.0, first_start - start_pad)

    trim_end = clip_duration
    trailing_silence = clip_duration - last_end
    if trailing_silence > silence_threshold:
        trim_end = min(clip_duration, last_end + end_pad)

    if trim_end - trim_start < 3.0:
        return 0.0, clip_duration
    return trim_start, trim_end


def shift_words_timeline(words: list[dict], offset_sec: float) -> list[dict]:
    """Shift word timestamps after trimming clip start."""
    shifted = []
    for w in words:
        ws = max(0.0, float(w.get("start", 0)) - offset_sec)
        we = max(ws + 0.04, float(w.get("end", ws + 0.08)) - offset_sec)
        w2 = dict(w)
        w2["start"] = ws
        w2["end"] = we
        shifted.append(w2)
    return shifted


def _parse_srt_timestamp(ts: str) -> float:
    """Parse HH:MM:SS,mmm to seconds."""
    h, m, rest = ts.strip().split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def first_srt_chunk_duration(srt_entries: list[str]) -> Optional[float]:
    """Duration in seconds of the first SRT chunk."""
    if not srt_entries:
        return None
    first = srt_entries[0].split("\n")
    if len(first) < 2 or "-->" not in first[1]:
        return None
    start_ts, end_ts = [t.strip() for t in first[1].split("-->")]
    return _parse_srt_timestamp(end_ts) - _parse_srt_timestamp(start_ts)


def srt_coverage_metric(
    words: list[dict],
    clip_duration: float,
    max_words_per_line: int = 4,
    start_offset_sec: float = 0.0,
) -> float:
    """
    Ratio of clip duration covered by subtitles (last chunk end / clip_duration).

    Returns 0.0–1.0; target >= 0.9 for golden-set regression.
    """
    if not words or clip_duration <= 0:
        return 0.0
    entries = _words_to_srt_entries(
        words=words,
        start_offset_sec=start_offset_sec,
        clip_duration_sec=clip_duration,
        max_words_per_line=max_words_per_line,
    )
    if not entries:
        return 0.0
    last = entries[-1].split("\n")
    if len(last) < 2 or "-->" not in last[1]:
        return 0.0
    end_ts = last[1].split("-->")[1].strip()
    last_end = _parse_srt_timestamp(end_ts)
    return min(1.0, max(0.0, last_end / clip_duration))


def filter_whisper_words(raw_words: list, clip_duration: float) -> list[dict]:
    """Filtra alucinaciones de Whisper; conserva el máximo de palabras del clip."""
    clip_words = []
    for w in raw_words or []:
        txt = (w.get("word") or "").strip()
        if not txt:
            continue
        low = txt.lower()
        if any(h in low for h in WHISPER_HALLUCINATIONS):
            continue
        ws = float(w.get("start", 0))
        we = float(w.get("end", ws + 0.08))
        if we <= 0 or ws >= clip_duration + 0.35:
            continue
        w2 = dict(w)
        w2["start"] = max(0.0, ws)
        w2["end"] = min(clip_duration + 0.10, max(we, ws + 0.04))
        clip_words.append(w2)
    return clip_words


def _format_srt_timestamp(seconds: float) -> str:
    """Convierte segundos a formato SRT: HH:MM:SS,mmm"""
    if seconds < 0:
        seconds = 0
    ms = int(round((seconds - int(seconds)) * 1000))
    s = int(seconds) % 60
    m = (int(seconds) // 60) % 60
    h = int(seconds) // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _split_text_into_chunks(text: str, max_words: int) -> list[str]:
    """Parte un texto en bloques de max_words palabras cada uno."""
    words = text.strip().split()
    chunks = []
    for i in range(0, len(words), max_words):
        chunks.append(" ".join(words[i:i + max_words]))
    return chunks


def _group_words_for_srt(
    clip_words: list[dict],
    max_words_per_line: int,
    max_gap_sec: float = 0.75,
    max_chunk_duration_sec: float = 4.0,
) -> list[list[dict]]:
    """
    Agrupa palabras para SRT por proximidad temporal (no solo conteo fijo).

    Evita bloques de 8–9s cuando Whisper espacia mal las primeras palabras.
    """
    groups: list[list[dict]] = []
    current: list[dict] = []

    for w in clip_words:
        if not current:
            current.append(w)
            continue

        prev = current[-1]
        gap = w["start"] - prev["end"]
        projected_span = w["end"] - current[0]["start"]
        should_split = (
            len(current) >= max_words_per_line
            or gap > max_gap_sec
            or (
                projected_span > max_chunk_duration_sec
                and len(current) >= 1
            )
        )
        if should_split:
            groups.append(current)
            current = [w]
        else:
            current.append(w)

    if current:
        groups.append(current)
    return groups


def _words_to_srt_entries(
    words: list[dict],
    start_offset_sec: float,
    clip_duration_sec: Optional[float],
    max_words_per_line: int,
    gap_sec: float = 0.030,
) -> list[str]:
    """
    Genera entradas SRT a partir de timestamps por palabra (Whisper word-level).

    Timings anclados al inicio/fin real de cada palabra del grupo — sin recortar
    el final del chunk (causa principal de subs desfasados o que desaparecen antes).
    """
    entries = []
    idx = 1

    clip_words = []
    end_limit = (clip_duration_sec + 0.25) if clip_duration_sec is not None else None
    for w in words:
        w_start = float(w.get("start", 0)) - start_offset_sec
        w_end = float(w.get("end", w_start + 0.1)) - start_offset_sec
        word_text = (w.get("word") or "").strip()
        if not word_text:
            continue
        if w_end <= 0:
            continue
        if end_limit is not None and w_start > end_limit:
            break
        w_start = max(0.0, w_start)
        if clip_duration_sec is not None:
            w_end = min(clip_duration_sec + 0.15, w_end)
        if w_end <= w_start:
            w_end = w_start + 0.06
        clip_words.append({"text": word_text, "start": w_start, "end": w_end})

    if not clip_words:
        return entries

    word_groups = _group_words_for_srt(clip_words, max_words_per_line)
    last_entry_end = -1.0

    for gi, group in enumerate(word_groups):
        is_last_group = gi == len(word_groups) - 1
        chunk_text = " ".join(g["text"] for g in group)

        chunk_start = group[0]["start"]
        chunk_end = group[-1]["end"]

        if chunk_start < last_entry_end + gap_sec:
            chunk_start = last_entry_end + gap_sec

        if not is_last_group:
            next_start = word_groups[gi + 1][0]["start"]
            chunk_end = min(chunk_end + 0.10, next_start - gap_sec)
        elif clip_duration_sec is not None:
            chunk_end = min(chunk_end + 0.15, clip_duration_sec + 0.10)

        chunk_end = max(chunk_start + 0.08, chunk_end)

        entries.append(
            f"{idx}\n"
            f"{_format_srt_timestamp(chunk_start)} --> {_format_srt_timestamp(chunk_end)}\n"
            f"{chunk_text}\n"
        )
        last_entry_end = chunk_end
        idx += 1

    return entries


def segments_to_srt(
    segments: list[dict],
    output_path: str,
    start_offset_sec: float = 0.0,
    clip_duration_sec: Optional[float] = None,
    max_words_per_line: int = 6,
    words: Optional[list[dict]] = None,
) -> str:
    """
    Convierte segmentos tipo Whisper a archivo SRT listo para quemar.

    Si se pasa `words` (timestamps por palabra de Whisper verbose_json con
    timestamp_granularities=["word"]), se usa el tiempo EXACTO de cada palabra
    → subtítulos perfectamente sincronizados con el audio, sin lag.
    Fallback a chunking por tiempo interpolado si no hay words.

    Args:
        segments: Lista con formato [{"start": s, "end": s, "text": "..."}]
                  (timestamps en segundos, relativos al video ORIGINAL)
        output_path: Donde guardar el .srt
        start_offset_sec: Segundo del video original donde arranca el clip.
                          Los timestamps se shiftean restando este valor.
                          Ej: si el clip es de s60-s90 del original, pasar 60.
        clip_duration_sec: Duracion del clip. Segmentos fuera de rango se recortan.
        max_words_per_line: Particiona segmentos largos en bloques mas cortos
                            para que sean legibles en vertical (recomendado 3-4).
        words: [OPCIONAL] Lista de palabras individuales con timestamps exactos:
               [{"word": "hola", "start": 0.5, "end": 0.7}, ...]

    Returns:
        Path al SRT generado

    Raises:
        ClipGenerationError si no hay segmentos utilizables
    """
    if not segments and not words:
        raise ClipGenerationError("segments y words vacíos")

    # ── Camino preferido: usar word-level timestamps (perfecto sync, sin lag) ──
    if words:
        srt_entries = _words_to_srt_entries(
            words=words,
            start_offset_sec=start_offset_sec,
            clip_duration_sec=clip_duration_sec,
            max_words_per_line=max_words_per_line,
        )
        if srt_entries:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write("\n".join(srt_entries))
            # Diagnóstico: log de cuántos sub bursts se generan + sample
            try:
                n_in = len(words)
                n_out = len(srt_entries)
                first = srt_entries[0].split("\n")[1] if srt_entries else "-"
                last = srt_entries[-1].split("\n")[1] if srt_entries else "-"
                print(f"   📜 SRT: {n_out} chunks de {n_in} words "
                      f"(max_words={max_words_per_line})")
                print(f"      primer chunk: {first}")
                print(f"      último chunk: {last}")
            except Exception:
                pass
            return output_path
        # Si no hay words en el rango del clip, caer al fallback
        print("⚠️ No hay words en el rango del clip — fallback a segments con interpolación")

    # ── Fallback: interpolación por segmento (legacy, lag inherente) ──────────
    srt_entries = []
    idx = 1
    last_entry_end = -1.0  # track cross-segment para evitar overlap entre segs

    for seg in segments:
        seg_start_raw = float(seg.get("start", 0))
        seg_end_raw = float(seg.get("end", seg_start_raw + 2))
        text = (seg.get("text") or "").strip()

        if not text:
            continue

        # Filtrar labels auto-generados tipo [Music], [Applause]
        if text.startswith("[") and text.endswith("]"):
            continue

        # Shift al tiempo del clip
        seg_start = seg_start_raw - start_offset_sec
        seg_end = seg_end_raw - start_offset_sec

        # Descartar los que quedan fuera del clip
        if seg_end <= 0:
            continue
        if clip_duration_sec is not None and seg_start >= clip_duration_sec:
            continue

        # Recortar a los limites del clip
        seg_start = max(0.0, seg_start)
        if clip_duration_sec is not None:
            seg_end = min(clip_duration_sec, seg_end)

        if seg_end - seg_start < 0.2:
            continue  # muy corto para leer

        # Partir segmentos largos en sub-bloques
        chunks = _split_text_into_chunks(text, max_words_per_line)
        if not chunks:
            continue

        # Gap entre chunks consecutivos: evita que dos líneas aparezcan al mismo
        # tiempo en el frame exacto del boundary (libass muestra ambas apiladas).
        GAP_SEC = 0.050

        # Respetar gap cross-segment: si el segmento anterior terminó muy cerca
        # del inicio de este, arrancamos un poquito después. Pero NUNCA movemos
        # el inicio más de 100ms para no acumular desfase perceptible.
        effective_start = max(seg_start, last_entry_end + GAP_SEC)
        if effective_start - seg_start > 0.10:
            # Si tenemos que mover más de 100ms el arranque, mejor dejarlo en
            # seg_start + GAP mínimo (aceptamos micro-overlap antes que lag).
            effective_start = seg_start + GAP_SEC

        # Si no queda tiempo dentro del segmento, skip
        if seg_end - effective_start < 0.2:
            continue

        # Distribuir tiempo equitativo entre chunks dentro del segmento.
        # Por caracteres era más fiel al pace pero acumulaba errores cuando
        # el cursor se movía por gap cross-segment — mejor simple y robusto.
        n = len(chunks)
        available = seg_end - effective_start
        total_gap = GAP_SEC * (n - 1)
        chunk_duration = max(0.2, (available - total_gap) / n)

        for i, chunk in enumerate(chunks):
            chunk_start = effective_start + i * (chunk_duration + GAP_SEC)
            chunk_end = chunk_start + chunk_duration
            # Clampar al final del segmento
            chunk_end = min(chunk_end, seg_end)

            if chunk_end - chunk_start < 0.15:
                # No hay tiempo para este chunk — break para no perder los que
                # sí entrarían. Pero al ser el ÚLTIMO del segmento, igual se
                # perdería; acá priorizamos no generar entries invisibles.
                break

            srt_entries.append(
                f"{idx}\n"
                f"{_format_srt_timestamp(chunk_start)} --> {_format_srt_timestamp(chunk_end)}\n"
                f"{chunk}\n"
            )
            idx += 1
            last_entry_end = chunk_end

    if not srt_entries:
        raise ClipGenerationError("No quedaron segmentos validos despues de shift/recorte")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(srt_entries))

    return output_path


# Presets de estilo para subtitulos (formato ASS force_style)
# Colores en formato ASS: &HAABBGGRR  (AA=alpha 00=opaco, BB=blue, GG=green, RR=red)
# Ejemplos: &H00FFFFFF=blanco, &H0000FFFF=amarillo, &H000080FF=naranja, &H0000FF00=verde
SUBTITLE_STYLES = {
    # ── TikTok viral 2024/2025: blanco bold con borde negro grueso ─────────────
    # El estilo más usado en clips virales — máximo contraste, legible sobre
    # cualquier fondo, transmite energía. FontName Liberation Sans = Arial-
    # compatible garantizado en Ubuntu (fonts-liberation viene por defecto).
    "tiktok_viral": {
        "FontName": "Liberation Sans",
        "FontSize": "44",                # baja para que 3 palabras largas entren
        "PrimaryColour": "&H00FFFFFF",   # blanco
        "OutlineColour": "&H00000000",   # negro
        "BorderStyle": "1",              # 1 = outline + shadow
        "Outline": "4",                  # borde visible pero no invasivo
        "Shadow": "0",
        "Bold": "1",
        "Spacing": "0",
        "Alignment": "2",                # 2 = bottom-center
        "MarginV": "340",                # zona blur inferior (debajo del video 16:9)
    },
    # ── Clean: blanco sobre fondo semi-transparente ────────────────────────────
    # Para contenido más formal (entrevistas, business content)
    "clean": {
        "FontName": "Liberation Sans",
        "FontSize": "48",
        "PrimaryColour": "&H00FFFFFF",   # blanco
        "OutlineColour": "&H00000000",   # negro
        "BackColour": "&HAA000000",      # fondo negro 67% opaco
        "BorderStyle": "4",              # 4 = box con BackColour (sin outline)
        "Outline": "16",                 # padding del box
        "Shadow": "0",
        "Bold": "1",
        "Alignment": "2",
        "MarginV": "340",
    },
    # ── Podcast: texto grande amarillo clásico ─────────────────────────────────
    # Para podcasts y vlogs donde el amarillo da energía
    "podcast": {
        "FontName": "Liberation Sans",
        "FontSize": "50",
        "PrimaryColour": "&H0000FFFF",   # amarillo
        "OutlineColour": "&H00000000",   # negro
        "BorderStyle": "1",
        "Outline": "3",
        "Shadow": "0",
        "Bold": "1",
        "Alignment": "2",
        "MarginV": "340",
    },
}


def _style_to_force_string(style: dict) -> str:
    """Convierte dict de estilo a string de force_style para FFmpeg."""
    return ",".join(f"{k}={v}" for k, v in style.items())


def _srt_time_to_ass(srt_ts: str) -> str:
    """Convierte HH:MM:SS,mmm (SRT) a H:MM:SS.cc (ASS, centesimas)."""
    h, m, rest = srt_ts.split(":")
    s, ms = rest.split(",")
    cc = int(ms) // 10  # centesimas
    return f"{int(h)}:{m}:{s}.{cc:02d}"


def _hex_to_ass_color(hex_color: str) -> str:
    """Convert #RRGGBB to ASS &HBBGGRR (no alpha prefix)."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return "&H00FFFFFF"
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"&H00{b:02X}{g:02X}{r:02X}"


def _ass_inline_tags_for_word(
    word_entry: dict,
    word_styles: Optional[list[dict]],
    tolerance: float = _WORD_CORRECTION_TOLERANCE,
) -> tuple[str, str]:
    """Return (open_tags, close_tag) for per-word ASS styling."""
    if not word_styles:
        return "", ""

    ws = float(word_entry.get("start", 0))
    we = float(word_entry.get("end", ws))
    for st in word_styles:
        st_start = float(st.get("start", -1))
        st_end = float(st.get("end", st_start))
        if abs(ws - st_start) > tolerance or abs(we - st_end) > tolerance:
            continue

        style_name = (st.get("style") or "default").lower()
        open_tags = []
        if style_name in ("highlight", "emphasis"):
            open_tags.append(r"{\b1}")
        if style_name == "highlight":
            open_tags.append(r"{\c&H0000FFFF&}")
        if st.get("color"):
            open_tags.append(r"{\c" + _hex_to_ass_color(st["color"]) + r"&}")
        if open_tags:
            return "".join(open_tags), r"{\r}"
    return "", ""


def _words_to_per_word_ass(
    words: list[dict],
    output_path: str,
    base_style: dict,
    play_res_x: int,
    play_res_y: int,
    clip_duration_sec: Optional[float],
    max_words_per_line: int = 4,
    word_styles: Optional[list[dict]] = None,
    start_offset_sec: float = 0.0,
) -> str:
    """Build ASS with grouped chunks and per-word inline style tags."""
    clip_words = []
    end_limit = (clip_duration_sec + 0.25) if clip_duration_sec is not None else None
    for w in words:
        w_start = float(w.get("start", 0)) - start_offset_sec
        w_end = float(w.get("end", w_start + 0.1)) - start_offset_sec
        word_text = (w.get("word") or "").strip()
        if not word_text or w_end <= 0:
            continue
        if end_limit is not None and w_start > end_limit:
            break
        w_start = max(0.0, w_start)
        if clip_duration_sec is not None:
            w_end = min(clip_duration_sec + 0.15, w_end)
        if w_end <= w_start:
            w_end = w_start + 0.06
        clip_words.append({"text": word_text, "start": w_start, "end": w_end})

    if not clip_words:
        raise ClipGenerationError("No words for per-word ASS")

    groups = _group_words_for_srt(clip_words, max_words_per_line)
    events = []
    last_entry_end = -1.0
    gap_sec = 0.030

    for gi, group in enumerate(groups):
        is_last_group = gi == len(groups) - 1
        chunk_start = group[0]["start"]
        chunk_end = group[-1]["end"]
        if chunk_start < last_entry_end + gap_sec:
            chunk_start = last_entry_end + gap_sec
        if not is_last_group:
            next_start = groups[gi + 1][0]["start"]
            chunk_end = min(chunk_end + 0.10, next_start - gap_sec)
        elif clip_duration_sec is not None:
            chunk_end = min(chunk_end + 0.15, clip_duration_sec + 0.10)
        chunk_end = max(chunk_start + 0.08, chunk_end)

        styled_parts = []
        for g in group:
            open_tags, close_tag = _ass_inline_tags_for_word(g, word_styles)
            safe = g["text"].replace("{", "\\{").replace("}", "\\}")
            styled_parts.append(f"{open_tags}{safe}{close_tag}")
        text = " ".join(styled_parts)

        start_ass = _format_ass_time(chunk_start)
        end_ass = _format_ass_time(chunk_end)
        events.append(f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{text}")
        last_entry_end = chunk_end

    s = base_style
    style_line = (
        f"Style: Default,"
        f"{s.get('FontName', 'Arial')},"
        f"{s.get('FontSize', '48')},"
        f"{s.get('PrimaryColour', '&H00FFFFFF')},"
        f"&H000000FF,"
        f"{s.get('OutlineColour', '&H00000000')},"
        f"{s.get('BackColour', '&H00000000')},"
        f"{s.get('Bold', '0')},"
        f"0,0,0,"
        f"100,100,{s.get('Spacing', '0')},0,"
        f"{s.get('BorderStyle', '1')},"
        f"{s.get('Outline', '1')},"
        f"{s.get('Shadow', '0')},"
        f"{s.get('Alignment', '2')},"
        f"60,60,"
        f"{s.get('MarginV', '40')},"
        f"1"
    )
    ass_content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
ScaledBorderAndShadow: yes
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style_line}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(events) + "\n"

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ass_content)
    print(f"   🎬 Per-word ASS: {len(events)} dialogue events")
    return output_path


def _srt_to_ass(srt_path: str, ass_path: str, style: dict, play_res_x: int, play_res_y: int) -> None:
    """
    Convierte un archivo SRT a ASS con PlayResX/Y explicitos y estilo embebido.

    Necesario porque libass con input SRT usa PlayResY por default (288),
    lo que hace que MarginV se interprete en esa escala virtual y no en
    pixeles reales del video.
    """
    with open(srt_path, "r", encoding="utf-8") as f:
        srt_content = f.read()

    # Parsear bloques SRT
    blocks = [b.strip() for b in srt_content.split("\n\n") if b.strip()]
    events = []
    for block in blocks:
        lines = block.split("\n")
        if len(lines) < 3:
            continue
        # Saltear el indice (linea 0), tomar timestamp (linea 1) y texto (resto)
        if "-->" not in lines[1]:
            continue
        start_ts, end_ts = [t.strip() for t in lines[1].split("-->")]
        text = " \\N ".join(lines[2:]).replace("\n", " \\N ")  # \N = newline en ASS
        start_ass = _srt_time_to_ass(start_ts)
        end_ass = _srt_time_to_ass(end_ts)
        events.append(f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{text}")

    # Armar estilo: ASS Style line
    # Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,
    #         Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,
    #         Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
    s = style
    style_line = (
        f"Style: Default,"
        f"{s.get('FontName', 'Arial')},"
        f"{s.get('FontSize', '48')},"
        f"{s.get('PrimaryColour', '&H00FFFFFF')},"
        f"&H000000FF,"  # secondary (unused)
        f"{s.get('OutlineColour', '&H00000000')},"
        f"{s.get('BackColour', '&H00000000')},"  # back (transparente si no se define)
        f"{s.get('Bold', '0')},"
        f"0,0,0,"                               # Italic, Underline, StrikeOut
        f"100,100,{s.get('Spacing', '0')},0,"   # ScaleX, ScaleY, Spacing, Angle
        f"{s.get('BorderStyle', '1')},"
        f"{s.get('Outline', '1')},"
        f"{s.get('Shadow', '0')},"
        f"{s.get('Alignment', '2')},"
        f"60,60,"       # MarginL, MarginR (más aire para que no toque bordes)
        f"{s.get('MarginV', '40')},"
        f"1"            # Encoding
    )

    ass_content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
ScaledBorderAndShadow: yes
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style_line}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(events) + "\n"

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_content)
    # Diagnóstico: cuántos events terminaron en el ASS
    print(f"   🎬 ASS: {len(events)} dialogue events (de {len(blocks)} bloques SRT)")


def burn_subtitles(
    video_path: str,
    srt_path: str,
    output_path: Optional[str] = None,
    style: str = "tiktok_viral",
) -> ClipMetadata:
    """
    Quema subtitulos de un archivo SRT sobre el video usando FFmpeg.

    Args:
        video_path: Video input (tipicamente un clip 9:16)
        srt_path: Archivo SRT con los subs
        output_path: Path de salida (opcional)
        style: Preset de estilo ('tiktok_viral', 'clean', 'podcast')

    Returns:
        ClipMetadata del video con subs quemados

    Raises:
        ClipGenerationError si falla
    """
    if not os.path.exists(video_path):
        raise ClipGenerationError(f"Video no existe: {video_path}")
    if not os.path.exists(srt_path):
        raise ClipGenerationError(f"SRT no existe: {srt_path}")
    if style not in SUBTITLE_STYLES:
        raise ClipGenerationError(
            f"Estilo desconocido: {style}. Opciones: {list(SUBTITLE_STYLES.keys())}"
        )

    if output_path is None:
        p = Path(video_path)
        output_path = str(p.parent / f"{p.stem}_subs.mp4")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Convertir SRT a ASS con PlayResY correcto para que MarginV funcione en pixeles reales
    video_meta = probe_video(video_path)
    ass_path = srt_path.replace(".srt", ".ass")
    _srt_to_ass(srt_path, ass_path, SUBTITLE_STYLES[style], video_meta.width, video_meta.height)

    ass_for_filter = ass_path.replace("\\", "/").replace(":", "\\:")
    vf = f"ass='{ass_for_filter}'"

    cmd = [
        FFMPEG_PATH,
        '-y', '-loglevel', 'error',
        '-i', video_path,
        '-vf', vf,
        '-c:v', 'libx264',
        '-preset', 'veryfast',
        '-threads', '2',
        '-crf', '23',
        '-c:a', 'copy',
        '-movflags', '+faststart',
        output_path,
    ]

    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                       check=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise ClipGenerationError("Timeout quemando subtitulos (>10min)")
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode(errors='replace') if isinstance(e.stderr, bytes) else (e.stderr or '')
        raise ClipGenerationError(f"FFmpeg burn_subtitles fallo: {err[-500:]}")

    if not os.path.exists(output_path):
        raise ClipGenerationError(f"FFmpeg termino pero el archivo no existe: {output_path}")

    return probe_video(output_path)


# Presets de estilo para overlay text (hook inicial)
# Diferencia vs SUBTITLE_STYLES: fuente mas grande, posicion configurable
# El overlay va en la zona SUPERIOR del blur (top blur band del layout 9:16).
OVERLAY_STYLES = {
    # ── TikTok viral: blanco bold con borde negro — el clásico de hooks ───────
    "tiktok_viral": {
        "FontName": "Liberation Sans",
        "FontSize": "58",                # baja para que 4 palabras UPPER entren cómodas
        "PrimaryColour": "&H00FFFFFF",   # blanco
        "OutlineColour": "&H00000000",   # negro
        "BorderStyle": "1",
        "Outline": "5",                  # borde grueso para legibilidad sobre blur
        "Shadow": "0",
        "Bold": "1",
        "Spacing": "1",
    },
    # ── Question: pregunta en amarillo ─────────────────────────────────────────
    "question": {
        "FontName": "Liberation Sans",
        "FontSize": "74",
        "PrimaryColour": "&H0000FFFF",   # amarillo
        "OutlineColour": "&H00000000",
        "BorderStyle": "1",
        "Outline": "5",
        "Shadow": "0",
        "Bold": "1",
        "Spacing": "1",
    },
    # ── Stat: dato/número con box negro ────────────────────────────────────────
    "stat": {
        "FontName": "Liberation Sans",
        "FontSize": "90",
        "PrimaryColour": "&H00FFFFFF",   # blanco
        "OutlineColour": "&H00000000",
        "BackColour": "&HCC000000",      # fondo negro 80% opaco
        "BorderStyle": "4",              # box
        "Outline": "18",                 # padding del box
        "Shadow": "0",
        "Bold": "1",
    },
}

# Mapeo de posicion a Alignment ASS
# 7=top-left, 8=top-center, 9=top-right
# 4=mid-left, 5=mid-center, 6=mid-right
# 1=bot-left, 2=bot-center, 3=bot-right
_POSITION_TO_ALIGNMENT = {
    "top": 8,
    "center": 5,
    "bottom": 2,
}


def _format_ass_time(seconds: float) -> str:
    """Segundos a H:MM:SS.cc (ASS)."""
    if seconds < 0:
        seconds = 0
    cs = int(round((seconds - int(seconds)) * 100))
    if cs == 100:
        seconds += 1
        cs = 0
    s = int(seconds) % 60
    m = (int(seconds) // 60) % 60
    h = int(seconds) // 3600
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _build_overlay_ass(
    text: str,
    start_sec: float,
    duration_sec: float,
    style: dict,
    alignment: int,
    margin_v: int,
    play_res_x: int,
    play_res_y: int,
    output_path: str,
) -> None:
    """Arma un ASS con un unico Dialogue de overlay."""
    s = style
    style_line = (
        f"Style: Overlay,"
        f"{s.get('FontName', 'Arial')},"
        f"{s.get('FontSize', '80')},"
        f"{s.get('PrimaryColour', '&H00FFFFFF')},"
        f"&H000000FF,"
        f"{s.get('OutlineColour', '&H00000000')},"
        f"{s.get('BackColour', '&H80000000')},"
        f"{s.get('Bold', '1')},"
        f"0,0,0,"
        f"100,100,{s.get('Spacing', '0')},0,"  # ScaleX, ScaleY, Spacing, Angle
        f"{s.get('BorderStyle', '1')},"
        f"{s.get('Outline', '3')},"
        f"{s.get('Shadow', '1')},"
        f"{alignment},"
        f"80,80,"       # MarginL, MarginR (overlay tiene más aire lateral)
        f"{margin_v},"
        f"1"
    )

    # Escape ASS: comas literales, llaves, etc.
    safe_text = text.replace("\n", " \\N ").replace("{", "\\{").replace("}", "\\}")

    start_ass = _format_ass_time(start_sec)
    end_ass = _format_ass_time(start_sec + duration_sec)
    dialogue = f"Dialogue: 0,{start_ass},{end_ass},Overlay,,0,0,0,,{safe_text}"

    ass_content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
ScaledBorderAndShadow: yes
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style_line}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
{dialogue}
"""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ass_content)


def burn_overlay_text(
    video_path: str,
    text: str,
    output_path: Optional[str] = None,
    position: str = "top",
    start_sec: float = 0.0,
    duration_sec: float = 3.0,
    style: str = "tiktok_viral",
) -> ClipMetadata:
    """
    Quema un texto overlay estilo hook sobre el video (ej: "NADIE ESPERABA ESTO").

    Args:
        video_path: Video input (tipicamente un clip 9:16 con o sin subs)
        text: Texto a mostrar (mayusculas recomendado para tiktok_viral)
        output_path: Path de salida
        position: 'top' (arriba, sobre blur superior), 'center', 'bottom'
        start_sec: Desde que segundo aparece (default 0 = desde el inicio)
        duration_sec: Cuanto dura visible
        style: 'tiktok_viral' | 'question' | 'stat'

    Returns:
        ClipMetadata del video con overlay
    """
    if not os.path.exists(video_path):
        raise ClipGenerationError(f"Video no existe: {video_path}")
    if not text or not text.strip():
        raise ClipGenerationError("text vacio")
    if style not in OVERLAY_STYLES:
        raise ClipGenerationError(
            f"Estilo desconocido: {style}. Opciones: {list(OVERLAY_STYLES.keys())}"
        )
    if position not in _POSITION_TO_ALIGNMENT:
        raise ClipGenerationError(
            f"Posicion desconocida: {position}. Opciones: {list(_POSITION_TO_ALIGNMENT.keys())}"
        )
    if duration_sec <= 0:
        raise ClipGenerationError(f"duration_sec debe ser > 0: {duration_sec}")

    if output_path is None:
        p = Path(video_path)
        output_path = str(p.parent / f"{p.stem}_overlay.mp4")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    video_meta = probe_video(video_path)

    # MarginV segun posicion (canvas 1920px):
    # top    -> 100px desde arriba = bien dentro del blur superior sin pegar al borde
    #           El blur superior ocupa 0-656px (para video 16:9 en canvas 9:16).
    #           100px nos da margen al top, el texto flota cómodo en el blur.
    # bottom -> 340px desde abajo = zona blur inferior, sobre los subs
    # center -> no aplica (Alignment=5 ignora MarginV)
    margin_v_by_pos = {"top": 100, "bottom": 340, "center": 0}
    alignment = _POSITION_TO_ALIGNMENT[position]
    margin_v = margin_v_by_pos[position]

    # Armar ASS temporal
    ass_path = str(Path(output_path).with_suffix(".overlay.ass"))
    _build_overlay_ass(
        text=text,
        start_sec=start_sec,
        duration_sec=duration_sec,
        style=OVERLAY_STYLES[style],
        alignment=alignment,
        margin_v=margin_v,
        play_res_x=video_meta.width,
        play_res_y=video_meta.height,
        output_path=ass_path,
    )

    ass_for_filter = ass_path.replace("\\", "/").replace(":", "\\:")
    vf = f"ass='{ass_for_filter}'"

    cmd = [
        FFMPEG_PATH,
        '-y', '-loglevel', 'error',
        '-i', video_path,
        '-vf', vf,
        '-c:v', 'libx264',
        '-preset', 'veryfast',
        '-threads', '2',
        '-crf', '23',
        '-c:a', 'copy',
        '-movflags', '+faststart',
        output_path,
    ]

    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                       check=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise ClipGenerationError("Timeout quemando overlay (>10min)")
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode(errors='replace') if isinstance(e.stderr, bytes) else (e.stderr or '')
        raise ClipGenerationError(f"FFmpeg burn_overlay fallo: {err[-500:]}")

    if not os.path.exists(output_path):
        raise ClipGenerationError(f"FFmpeg termino pero el archivo no existe: {output_path}")

    return probe_video(output_path)


def to_vertical_9_16(
    clip_path: str,
    output_path: Optional[str] = None,
    target_width: int = 1080,
    target_height: int = 1920,
    blur_intensity: int = 25,
) -> ClipMetadata:
    """
    Convierte un clip a formato vertical 9:16 con fondo desenfocado.

    Estilo TikTok/Reels/Shorts:
      - Fondo: version ampliada del mismo video con blur fuerte (llena el frame)
      - Foreground: video original centrado, escalado a ancho completo manteniendo aspect ratio
      - Resultado: 1080x1920 (o el target que se pase)

    Args:
        clip_path: Path al clip (tipicamente output de cut_clip)
        output_path: Path de salida (opcional)
        target_width: Ancho final (default 1080)
        target_height: Alto final (default 1920)
        blur_intensity: Intensidad del blur del fondo (10=suave, 25=medio, 50=fuerte)

    Returns:
        ClipMetadata del clip vertical generado

    Raises:
        ClipGenerationError si falla
    """
    if not os.path.exists(clip_path):
        raise ClipGenerationError(f"Clip no existe: {clip_path}")

    if target_width <= 0 or target_height <= 0:
        raise ClipGenerationError(f"Dimensiones invalidas: {target_width}x{target_height}")

    source = probe_video(clip_path)

    if output_path is None:
        p = Path(clip_path)
        output_path = str(p.parent / f"{p.stem}_9x16.mp4")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Filtergraph:
    # [0:v]split=2[bg][fg]                          ← duplicar stream de video
    # [bg]scale=W:H:force_original_aspect_ratio=increase,crop=W:H,
    #       boxblur=luma_radius=BLUR:luma_power=1[bg2]  ← fondo cubriendo + blur
    # [fg]scale=W:-2:force_original_aspect_ratio=decrease[fg2]  ← foreground ancho completo manteniendo AR
    # [bg2][fg2]overlay=(W-w)/2:(H-h)/2             ← centrar fg sobre bg
    W, H, B = target_width, target_height, blur_intensity
    filter_complex = (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},boxblur=luma_radius={B}:luma_power=1[bg2];"
        f"[fg]scale={W}:-2:force_original_aspect_ratio=decrease[fg2];"
        f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2:format=auto,format=yuv420p"
    )

    cmd = [
        FFMPEG_PATH,
        '-y', '-loglevel', 'error',
        '-i', clip_path,
        '-filter_complex', filter_complex,
        '-c:v', 'libx264',
        '-preset', 'veryfast',
        '-threads', '2',
        '-crf', '23',
        '-c:a', 'copy',
        '-movflags', '+faststart',
        output_path,
    ]

    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                       check=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise ClipGenerationError("Timeout convirtiendo a 9:16 (>10min)")
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode(errors='replace') if isinstance(e.stderr, bytes) else (e.stderr or '')
        # Si falla por audio no compatible, reintentar con re-encode de audio
        if 'invalid argument' in err.lower() or 'audio' in err.lower():
            cmd_retry = [c if c != 'copy' else 'aac' for c in cmd]
            if '-b:a' not in cmd_retry:
                idx = cmd_retry.index('aac')
                cmd_retry[idx:idx+1] = ['aac', '-b:a', '128k']
            try:
                subprocess.run(cmd_retry, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               check=True, timeout=600)
            except subprocess.CalledProcessError as e2:
                err2 = e2.stderr.decode(errors='replace') if isinstance(e2.stderr, bytes) else (e2.stderr or '')
                raise ClipGenerationError(f"FFmpeg 9:16 fallo (retry): {err2[-500:]}")
        else:
            raise ClipGenerationError(f"FFmpeg 9:16 fallo: {err[-500:]}")

    if not os.path.exists(output_path):
        raise ClipGenerationError(f"FFmpeg termino pero el archivo no existe: {output_path}")

    meta = probe_video(output_path)

    # Validar dimensiones
    if meta.width != target_width or meta.height != target_height:
        raise ClipGenerationError(
            f"Dimensiones incorrectas: esperado {target_width}x{target_height}, "
            f"obtenido {meta.width}x{meta.height}"
        )

    return meta


@dataclass
class GenerateClipResult:
    """Resultado del pipeline completo, incluye metadata + paths intermedios."""
    final: ClipMetadata
    total_time_sec: float
    steps_time: dict  # {'cut': 1.3, '9x16': 8.0, 'subs': 10.5, 'overlay': 11.7}
    intermediate_paths: list  # archivos temporales generados (para limpiar si hace falta)


def generate_clip(
    video_path: Optional[str] = None,
    start_sec: float = 0.0,
    end_sec: float = 0.0,
    output_path: str = "",
    segments: Optional[list[dict]] = None,
    segments_start_offset_sec: Optional[float] = None,
    words: Optional[list[dict]] = None,  # word-level timestamps (Whisper verbose_json)
    word_styles: Optional[list[dict]] = None,  # per-word ASS overrides
    subtitle_style: str = "tiktok_viral",
    overlay_text: Optional[str] = None,
    overlay_style: str = "tiktok_viral",
    overlay_duration_sec: float = 3.5,
    overlay_position: str = "top",
    target_width: int = 1080,
    target_height: int = 1920,
    keep_intermediate: bool = False,
    workdir: Optional[str] = None,
    # ── Descarga selectiva (stream URLs) ──────────────────────────────────────
    # Si se pasan, FFmpeg descarga directamente desde las URLs (HTTP Range
    # requests para el segmento exacto) en vez de leer un archivo local.
    # Reduce el ancho de banda de ~1.3GB a ~50MB por job.
    video_stream_url: Optional[str] = None,
    audio_stream_url: Optional[str] = None,
    proxy_url: Optional[str] = None,
) -> GenerateClipResult:
    """
    Pipeline completo: video + timestamps → MP4 9:16 listo para TikTok/Reels.

    Modos de entrada:
      A) Archivo local  : pasar video_path (comportamiento original)
      B) Descarga selectiva: pasar video_stream_url + audio_stream_url.
         FFmpeg hace HTTP Range requests SOLO para los bytes del clip
         (~50MB en vez de bajar el video completo de 1.3GB).

    Pasos:
      1. Corte preciso + 9:16 + subs + overlay en una sola pasada ffmpeg
      2. (Modo B) FFmpeg descarga directo desde URL con seek eficiente

    Args:
        video_path: MP4 fuente local. Requerido si no se pasan stream URLs.
        start_sec, end_sec: rango del momento viral (en segundos del video original)
        output_path: path del MP4 final
        segments: lista Whisper-style para subtitulos.
        segments_start_offset_sec: offset de timestamps de segments.
        subtitle_style: 'tiktok_viral' | 'clean' | 'podcast'
        overlay_text: texto del hook inicial. Si None, no se quema overlay.
        overlay_style: 'tiktok_viral' | 'question' | 'stat'
        overlay_duration_sec: cuantos segundos dura el overlay visible
        overlay_position: 'top' | 'center' | 'bottom'
        target_width, target_height: dimensiones finales (default 1080x1920)
        keep_intermediate: si True, no borra los archivos temporales
        workdir: directorio para archivos intermedios
        video_stream_url: URL de stream de video (modo descarga selectiva)
        audio_stream_url: URL de stream de audio (modo descarga selectiva)
        proxy_url: proxy HTTP/HTTPS para que FFmpeg acceda a las URLs

    Returns:
        GenerateClipResult con metadata del clip final y timings por paso

    Raises:
        ClipGenerationError si algun paso falla
    """
    use_stream_urls = bool(video_stream_url and audio_stream_url)
    if not use_stream_urls and not video_path:
        raise ClipGenerationError(
            "Debe pasarse video_path (archivo local) o video_stream_url+audio_stream_url"
        )
    if not output_path:
        raise ClipGenerationError("output_path es requerido")
    import time

    step_times = {}
    intermediates = []
    t_total_start = time.time()

    def _del(path: str) -> None:
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    # Setup workdir
    if workdir is None:
        workdir = str(Path(output_path).parent / ".tmp_pipeline")
    Path(workdir).mkdir(parents=True, exist_ok=True)

    stem = Path(output_path).stem
    duration_sec = end_sec - start_sec
    W, H = target_width, target_height
    B = 25  # blur intensity

    try:
        # ── SINGLE-PASS PIPELINE ────────────────────────────────────────────
        # Genera corte + 9:16 + subs + overlay en UNA sola llamada a ffmpeg.
        # Antes eran 4 pasadas secuenciales (~220s). Ahora ~55s (4x más rápido).
        #
        # Estrategia:
        #   1. Generar archivos ASS (texto puro, instantáneo)
        #   2. Una sola llamada ffmpeg con filter_complex que:
        #      a) Escala/cropea a 9:16 con fondo blur
        #      b) Encadena ass= para subs y overlay en el mismo filtro de video
        # ─────────────────────────────────────────────────────────────────────

        t0 = time.time()

        # Paso A: generar ASS de subtítulos (si hay segments o words)
        subs_ass_path = None
        if segments or words:
            offset = segments_start_offset_sec if segments_start_offset_sec is not None else start_sec
            ass_path = str(Path(workdir) / f"{stem}_subs.ass")
            try:
                if words and word_styles:
                    _words_to_per_word_ass(
                        words=words,
                        output_path=ass_path,
                        base_style=SUBTITLE_STYLES[subtitle_style],
                        play_res_x=W,
                        play_res_y=H,
                        clip_duration_sec=duration_sec,
                        max_words_per_line=4,
                        word_styles=word_styles,
                        start_offset_sec=offset,
                    )
                    subs_ass_path = ass_path
                    intermediates.append(ass_path)
                else:
                    srt_path = str(Path(workdir) / f"{stem}_subs.srt")
                    segments_to_srt(
                        segments=segments,
                        output_path=srt_path,
                        start_offset_sec=offset,
                        clip_duration_sec=duration_sec,
                        max_words_per_line=4,
                        words=words,
                    )
                    intermediates.append(srt_path)
                    _srt_to_ass(srt_path, ass_path, SUBTITLE_STYLES[subtitle_style], W, H)
                    subs_ass_path = ass_path
                    intermediates.append(ass_path)
            except ClipGenerationError as e:
                if words:
                    raise ClipGenerationError(
                        f"Subs fallaron con {len(words)} words "
                        f"(rango {start_sec}-{end_sec}s): {e}"
                    ) from e
                print(f"⚠️ Sin segments para rango {start_sec}-{end_sec}s, sigo sin subs: {e}")
        # Nota: srt_to_ass usa W,H (dimensiones del target) como PlayRes,
        # así que no necesitamos hacer probe del video fuente aquí.

        # Paso B: generar ASS del overlay (si hay overlay_text)
        overlay_ass_path = None
        if overlay_text and overlay_text.strip():
            overlay_ass_path = str(Path(workdir) / f"{stem}_overlay.ass")
            margin_v_by_pos = {"top": 100, "bottom": 340, "center": 0}
            alignment = _POSITION_TO_ALIGNMENT[overlay_position]
            margin_v = margin_v_by_pos[overlay_position]
            _build_overlay_ass(
                text=overlay_text,
                start_sec=0.0,
                duration_sec=overlay_duration_sec,
                style=OVERLAY_STYLES[overlay_style],
                alignment=alignment,
                margin_v=margin_v,
                play_res_x=W,
                play_res_y=H,
                output_path=overlay_ass_path,
            )
            intermediates.append(overlay_ass_path)

        step_times['prep'] = round(time.time() - t0, 2)

        # Paso C: filter_complex — 9:16 base
        # bg: escala a 9:16 rellenando + blur
        # fg: escala manteniendo aspect ratio (ancho completo)
        # overlay centra fg sobre bg
        fg_filter = f"[fg]scale={W}:-2:force_original_aspect_ratio=decrease[fg2]"
        filter_parts = (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},boxblur=luma_radius={B}:luma_power=1[bg2];"
            f"{fg_filter};"
            f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2:format=auto,format=yuv420p"
        )

        # Encadenar filtros de texto directamente en el mismo stream
        def _esc(p: str) -> str:
            """Escapa path para filtro ass= de ffmpeg (barras y dos puntos)."""
            return p.replace("\\", "/").replace(":", "\\:")

        if subs_ass_path:
            filter_parts += f",ass='{_esc(subs_ass_path)}'"
        if overlay_ass_path:
            filter_parts += f",ass='{_esc(overlay_ass_path)}'"

        filter_parts += "[vout]"

        t0 = time.time()

        # ── Construir comando FFmpeg ──────────────────────────────────────────
        # Modo A: archivo local  → un solo -i, audio de 0:a
        # Modo B: stream URLs    → dos -i (video URL + audio URL), audio de 1:a
        #   FFmpeg hace HTTP Range request hacia la posición exacta del clip,
        #   descargando solo ~50MB en vez de 1.3GB del video completo.
        # ─────────────────────────────────────────────────────────────────────
        subprocess_env = None
        if use_stream_urls:
            print(f"   🎯 Descarga selectiva: {start_sec:.1f}s-{end_sec:.1f}s ({duration_sec:.1f}s)")

            # ── Proxy: usar -http_proxy nativo de FFmpeg, NO env vars ─────────
            # HTTPS_PROXY/HTTP_PROXY env vars no funcionan bien con FFmpeg para
            # HTTPS CONNECT tunneling. El flag -http_proxy va antes de cada -i
            # y aplica solo a esa entrada.
            proxy_flags = ['-http_proxy', proxy_url] if proxy_url else []
            if proxy_url:
                print(f"   🌐 FFmpeg proxy: {proxy_url.split('@')[-1] if '@' in proxy_url else proxy_url}")

            cmd = [
                FFMPEG_PATH, '-y', '-loglevel', 'error',
                *proxy_flags,
                '-ss', f'{start_sec:.3f}',
                '-i', video_stream_url,   # input 0: video stream URL
                *proxy_flags,
                '-ss', f'{start_sec:.3f}',
                '-i', audio_stream_url,   # input 1: audio stream URL
                '-t', f'{duration_sec:.3f}',
                '-filter_complex', filter_parts,  # opera sobre [0:v]
                '-map', '[vout]',
                '-map', '1:a',            # audio de input 1
                '-c:v', 'libx264', '-preset', 'veryfast', '-threads', '2', '-crf', '23',
                '-c:a', 'aac', '-b:a', '128k',
                '-avoid_negative_ts', 'make_zero',
                '-movflags', '+faststart',
                output_path,
            ]
        else:
            cmd = [
                FFMPEG_PATH, '-y', '-loglevel', 'error',
                '-i', video_path,
                '-ss', f'{start_sec:.3f}',
                '-t', f'{duration_sec:.3f}',
                '-filter_complex', filter_parts,
                '-map', '[vout]',
                '-map', '0:a',
                '-c:v', 'libx264', '-preset', 'veryfast', '-threads', '2', '-crf', '23',
                '-c:a', 'aac', '-b:a', '128k',
                '-avoid_negative_ts', 'make_zero',
                '-movflags', '+faststart',
                output_path,
            ]

        try:
            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=True,
                timeout=600,
            )
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode(errors='replace') if isinstance(e.stderr, bytes) else (e.stderr or '')
            raise ClipGenerationError(f"FFmpeg single-pass falló: {err[-800:]}")
        except subprocess.TimeoutExpired:
            raise ClipGenerationError("FFmpeg single-pass timeout (>10min)")

        step_times['encode'] = round(time.time() - t0, 2)

        if not os.path.exists(output_path):
            raise ClipGenerationError(f"Pipeline termino pero el output no existe: {output_path}")

        final_meta = probe_video(output_path)
        total_time = round(time.time() - t_total_start, 2)

        # Validar que las dimensiones sean correctas
        if final_meta.width != target_width or final_meta.height != target_height:
            raise ClipGenerationError(
                f"Output con dimensiones incorrectas: "
                f"esperado {target_width}x{target_height}, obtenido {final_meta.width}x{final_meta.height}"
            )

        return GenerateClipResult(
            final=final_meta,
            total_time_sec=total_time,
            steps_time=step_times,
            intermediate_paths=intermediates,
        )

    finally:
        if not keep_intermediate:
            for p in intermediates:
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except Exception as e:
                    print(f"⚠️ No se pudo borrar intermedio {p}: {e}")
            # Intentar borrar workdir si quedo vacio
            try:
                Path(workdir).rmdir()
            except OSError:
                pass  # no estaba vacio


if __name__ == "__main__":
    # Smoke test rapido
    import sys
    if len(sys.argv) < 4:
        print("Uso: python clip_generator.py <video> <start_sec> <end_sec>")
        sys.exit(1)

    meta = cut_clip(sys.argv[1], float(sys.argv[2]), float(sys.argv[3]))
    print(f"✅ Clip OK: {meta.path}")
    print(f"   Duracion: {meta.duration_sec:.1f}s | Tamaño: {meta.size_mb:.1f}MB")
    print(f"   Resolucion: {meta.width}x{meta.height} ({meta.aspect_ratio})")
    print(f"   Audio: {'si' if meta.has_audio else 'no'}")
