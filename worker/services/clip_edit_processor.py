"""
Clip Edit Processor (Phase 3)
─────────────────────────────────────────────────────────────────────────────
Procesa filas en `clip_edits` con status='queued':
  1. Resuelve content_result + job → obtiene video_url, start/end del momento.
  2. Re-descarga el segmento via yt-dlp (~50MB, mismo path que el job original).
  3. Re-transcribe con Whisper per-clip (word-level sync).
  4. Llama a generate_clip() con overlay_text + subtitle_style del edit.
  5. Sube el MP4 a R2 → setea rendered_clip_url + status='completed'.
  6. Limpia archivos temporales.

En cualquier error, marca el edit como 'failed' con mensaje descriptivo
para que la UI lo muestre.
"""
import gc
from pathlib import Path

from services.downloader import download_clip_ytdlp
from services.clip_generator import generate_clip, ClipGenerationError
from services.supabase_client import (
    get_content_result,
    get_job,
    mark_clip_edit_completed,
    mark_clip_edit_failed,
    upload_edited_clip_to_storage,
)

DOWNLOADS_DIR = Path(__file__).parent.parent / "downloads"
CLIPS_DIR = Path(__file__).parent.parent / "clips"


def _whisper_per_clip(src_path: str, clip_duration: float, edit_id: str):
    """
    Extrae audio del clip y lo transcribe con Whisper (word-level).
    Retorna (words, segments) o (None, None) si falla.
    Mantiene el mismo padding que el pipeline principal para sync perfecto.
    """
    import subprocess as _sp
    import shutil as _sh

    audio_path = DOWNLOADS_DIR / f"edit_{edit_id}_audio.mp3"
    try:
        from services.transcriber import transcribe_with_whisper_openrouter

        ffmpeg = _sh.which("ffmpeg") or "ffmpeg"
        PRE_PAD = 0.5
        POST_PAD = 1.0
        # src_path ya es el clip recortado (start=0, end=clip_duration)
        pad_start = 0.0
        pad_end = clip_duration + POST_PAD

        cmd = [
            ffmpeg, "-y", "-loglevel", "error",
            "-ss", str(pad_start), "-to", str(pad_end),
            "-i", src_path,
            "-vn", "-acodec", "libmp3lame",
            "-ar", "16000", "-ac", "1", "-b:a", "96k",
            str(audio_path),
        ]
        _sp.run(cmd, check=True, timeout=120, capture_output=True, text=True)

        tr = transcribe_with_whisper_openrouter(str(audio_path))
        raw_words = tr.get("words") or []
        raw_segments = tr.get("segments") or []

        # Filter words/segments to clip range (no shift needed, pad_start=0)
        words = []
        for w in raw_words:
            ws = float(w.get("start", 0))
            we = float(w.get("end", ws + 0.1))
            if we <= 0 or ws >= clip_duration:
                continue
            w2 = dict(w)
            w2["start"] = max(0.0, ws)
            w2["end"] = min(clip_duration, we)
            words.append(w2)

        segments = []
        for sg in raw_segments:
            ss = float(sg.get("start", 0))
            se = float(sg.get("end", ss + 0.1))
            if se <= 0 or ss >= clip_duration:
                continue
            sg2 = dict(sg)
            sg2["start"] = max(0.0, ss)
            sg2["end"] = min(clip_duration, se)
            segments.append(sg2)

        print(f"   ✅ Whisper edit: {len(words)} words, {len(segments)} segments")
        return words, segments
    except Exception as e:
        print(f"   ⚠️ Whisper edit falló ({e}) — clip se generará sin subs")
        return None, None
    finally:
        try:
            audio_path.unlink(missing_ok=True)
        except Exception:
            pass


def process_clip_edit(edit: dict) -> None:
    """
    Procesa un único clip_edit. Side-effects: actualiza la fila de DB,
    sube MP4 a R2. No levanta excepciones — las captura y marca failed.
    """
    edit_id = edit["id"]
    content_result_id = edit["content_result_id"]
    overlay_text = edit.get("overlay_text")
    subtitle_style = edit.get("subtitle_style") or "tiktok_viral"
    overlay_position = edit.get("overlay_position") or "top"

    print(f"\n{'─'*60}")
    print(f"🎨 Processing clip_edit: {edit_id}")
    print(f"   overlay_text={overlay_text!r}  style={subtitle_style}  pos={overlay_position}")

    seg_path = None
    clip_output = None

    try:
        # 1) Resolve content_result
        cr = get_content_result(content_result_id)
        if not cr:
            raise RuntimeError(f"content_result {content_result_id} no existe")
        if cr.get("start_time") is None or cr.get("end_time") is None:
            raise RuntimeError("content_result sin start/end timestamps")

        start_s = float(cr["start_time"])
        end_s = float(cr["end_time"])

        # 2) Resolve job → video_url
        job = get_job(cr["job_id"])
        if not job:
            raise RuntimeError(f"job {cr['job_id']} no existe")
        video_url = job.get("video_url")
        if not video_url:
            raise RuntimeError("job sin video_url")

        # 3) Re-download segment (~50MB)
        DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
        CLIPS_DIR.mkdir(parents=True, exist_ok=True)
        seg_out = str(DOWNLOADS_DIR / f"edit_{edit_id}_seg")
        print(f"   ⬇️  yt-dlp segment: {int(start_s)}-{int(end_s)}s")
        seg_path = download_clip_ytdlp(
            youtube_url=video_url,
            start_sec=start_s,
            end_sec=end_s,
            output_path=seg_out,
        )

        # 4) Whisper per-clip (best-effort, sync palabra a palabra)
        clip_duration = end_s - start_s
        words, segments = _whisper_per_clip(seg_path, clip_duration, edit_id)

        # 5) Generate clip with edit's overrides
        clip_output = CLIPS_DIR / f"edit_{edit_id}.mp4"

        # Apply trim offsets if present (recortes adicionales del usuario)
        trim_start = float(edit.get("trim_start_offset") or 0.0)
        trim_end_off = float(edit.get("trim_end_offset") or 0.0)
        clip_start = max(0.0, trim_start)
        clip_end = max(clip_start + 1.0, clip_duration - trim_end_off)

        gen = generate_clip(
            video_path=seg_path,
            start_sec=clip_start,
            end_sec=clip_end,
            output_path=str(clip_output),
            segments=segments,
            segments_start_offset_sec=0.0,
            words=words,
            subtitle_style=subtitle_style,
            overlay_text=overlay_text,
            overlay_style="tiktok_viral",
            overlay_position=overlay_position,
            target_width=720,
            target_height=1280,
        )
        print(f"   ✅ Clip re-generado en {gen.total_time_sec}s, {gen.final.size_mb:.1f}MB")

        # 6) Upload
        url = upload_edited_clip_to_storage(str(clip_output), edit_id)
        if not url:
            raise RuntimeError("upload_edited_clip_to_storage devolvió None")

        # 7) Mark completed
        mark_clip_edit_completed(edit_id, url)
        print(f"✅ clip_edit {edit_id} completado")

    except (ClipGenerationError, Exception) as e:
        msg = str(e) or e.__class__.__name__
        print(f"❌ clip_edit {edit_id} falló: {msg}")
        try:
            mark_clip_edit_failed(edit_id, msg)
        except Exception:
            pass
    finally:
        # Cleanup
        for p in (seg_path, str(clip_output) if clip_output else None):
            if p:
                try:
                    Path(p).unlink(missing_ok=True)
                except Exception:
                    pass
        gc.collect()
