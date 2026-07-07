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
import json
import os
from pathlib import Path
from typing import Optional

from services.downloader import (
    download_clip_ytdlp,
    download_clip_via_stream_urls,
    _extract_video_id,
)
from services.storage_client import download_file, object_key_from_public_url
from services.clip_generator import generate_clip, ClipGenerationError, apply_word_corrections, probe_video
from services.supabase_client import (
    get_content_result,
    get_job,
    mark_clip_edit_completed,
    mark_clip_edit_failed,
    upload_edited_clip_to_storage,
)

DOWNLOADS_DIR = Path(__file__).parent.parent / "downloads"
CLIPS_DIR = Path(__file__).parent.parent / "clips"


def _download_from_r2(
    raw_clip_url: str,
    edit_id: str,
    *,
    job_id: Optional[str] = None,
    moment_index: Optional[int] = None,
) -> str:
    """
    Descarga el segmento crudo cacheado en R2.
    Usa API S3 autenticada (raw_clips/ no siempre es público) y cae a URL pública.
    """
    import urllib.request

    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    out = str(DOWNLOADS_DIR / f"edit_{edit_id}_seg.mp4")
    print(f"   ⬇️  Cache hit: bajando segmento crudo desde R2")

    keys_to_try: list[str] = []
    if raw_clip_url:
        parsed = object_key_from_public_url(raw_clip_url)
        if parsed:
            keys_to_try.append(parsed)
    if job_id is not None and moment_index is not None:
        stable = f"raw_clips/{job_id}_{moment_index}.mp4"
        if stable not in keys_to_try:
            keys_to_try.append(stable)

    for key in keys_to_try:
        if download_file(key, out):
            size_mb = Path(out).stat().st_size // (1 << 20)
            print(f"   ✅ R2 segment OK via S3 ({size_mb}MB, key={key})")
            return out

    if raw_clip_url:
        print(f"   ⚠️ S3 download falló — probando URL pública")
        urllib.request.urlretrieve(raw_clip_url, out)
        size_mb = Path(out).stat().st_size // (1 << 20)
        print(f"   ✅ R2 segment OK via URL pública ({size_mb}MB)")
        return out

    raise RuntimeError("No raw clip key/URL available for R2 download")


def _download_segment_with_fallback(
    video_url: str,
    start_s: float,
    end_s: float,
    edit_id: str,
    raw_clip_url: Optional[str] = None,
    *,
    job_id: Optional[str] = None,
    moment_index: Optional[int] = None,
) -> str:
    """
    Descarga el segmento [start_s, end_s] del video con cascada:
      0. (Plan C) Si raw_clip_url está cacheado en R2 → bajarlo de ahí.
      1. yt-dlp download_ranges (~50MB, rápido)
      2. Si falla: get_stream_urls + descarga parcial desde byte 0 + corte
         con ffmpeg al rango pedido.

    Retorna path a un MP4 local que contiene EXACTAMENTE el rango del clip
    (start=0, duration=end_s-start_s) para simplificar el resto del pipeline.
    """
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    yt_video_id = _extract_video_id(video_url)

    # ── Intento 0: cache R2 (Plan C) ──────────────────────────────────
    if raw_clip_url or (job_id and moment_index is not None):
        try:
            return _download_from_r2(
                raw_clip_url or "",
                edit_id,
                job_id=job_id,
                moment_index=moment_index,
            )
        except Exception as e_cache:
            print(f"   ⚠️ R2 cache miss ({e_cache}) — fallback a streams")

    # ── Intento 1: RapidAPI / stream partial (prod) ───────────────────
    if os.getenv("ENVIRONMENT", "").lower() in ("production", "prod") or os.getenv("RAPIDAPI_KEY"):
        try:
            return download_clip_via_stream_urls(
                youtube_url=video_url,
                start_sec=start_s,
                end_sec=end_s,
                video_duration=end_s + 30,
                video_id=yt_video_id,
                temp_id=f"edit_{edit_id}",
                force_rapidapi=True,
            )
        except Exception as e_rapid:
            print(f"   ⚠️ RapidAPI edit falló ({e_rapid}) — probando yt-dlp")

    # ── Intento 2: yt-dlp download_ranges (dev) ─────────────────────
    try:
        seg_out = str(DOWNLOADS_DIR / f"edit_{edit_id}_seg")
        path = download_clip_ytdlp(
            youtube_url=video_url,
            start_sec=start_s,
            end_sec=end_s,
            output_path=seg_out,
        )
        print(f"   ✅ yt-dlp segment OK")
        return path
    except Exception as e_ytdlp:
        print(f"   ⚠️ yt-dlp falló ({e_ytdlp}) — fallback a partial download")

    # ── Intento 2: stream URLs + partial download + ffmpeg cut ────────
    try:
        return download_clip_via_stream_urls(
            youtube_url=video_url,
            start_sec=start_s,
            end_sec=end_s,
            video_duration=end_s + 30,
            video_id=yt_video_id,
            temp_id=f"edit_{edit_id}",
            force_rapidapi=True,
        )
    except Exception as e_streams:
        raise RuntimeError(
            f"Sin streams disponibles (yt-dlp y RapidAPI fallaron): {e_streams}"
        ) from e_streams


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
            "-vn",
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-acodec", "libmp3lame",
            "-ar", "16000", "-ac", "1", "-b:a", "128k",
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

        # 3) Re-download segment con cascada (cache R2 → yt-dlp → partial)
        CLIPS_DIR.mkdir(parents=True, exist_ok=True)
        cached_raw_url = cr.get("raw_clip_url")
        print(
            f"   ⬇️  Segment {int(start_s)}-{int(end_s)}s "
            f"(cache={'sí' if cached_raw_url else 'no'})"
        )
        seg_path = _download_segment_with_fallback(
            video_url=video_url,
            start_s=start_s,
            end_s=end_s,
            edit_id=edit_id,
            raw_clip_url=cached_raw_url,
            job_id=cr.get("job_id"),
            moment_index=cr.get("moment_index"),
        )

        # 4) Whisper words: usar cache si existe, sino transcribir
        words, segments = None, None
        cached_whisper = cr.get("whisper_words")
        if cached_whisper:
            try:
                if isinstance(cached_whisper, str):
                    cached_whisper = json.loads(cached_whisper)
                words = cached_whisper.get("words") or None
                segments = cached_whisper.get("segments") or None
                if words or segments:
                    print(
                        f"   ✅ Whisper cache hit: "
                        f"{len(words or [])} words, {len(segments or [])} segments"
                    )
            except Exception as e_cache:
                print(f"   ⚠️ Whisper cache parse falló ({e_cache}) — re-transcribo")
                words, segments = None, None

        # Duración real del MP4 cacheado (post-snap) — no end_s-start_s del job original.
        try:
            probed = probe_video(seg_path)
            clip_duration = float(probed.duration_sec or 0)
        except Exception as e_probe:
            print(f"   ⚠️ ffprobe falló ({e_probe}) — uso duración del job")
            clip_duration = end_s - start_s

        cached_duration = None
        if cached_whisper and isinstance(cached_whisper, dict):
            cached_duration = cached_whisper.get("duration_sec")
        if cached_duration and abs(float(cached_duration) - clip_duration) > 0.5:
            print(
                f"   📐 Ajustando duración clip: job={end_s - start_s:.1f}s "
                f"→ cache={float(cached_duration):.1f}s (whisper alineado)"
            )
            clip_duration = float(cached_duration)

        probed_duration = clip_duration
        snap_trim_start = 0.0
        if cached_whisper and isinstance(cached_whisper, dict):
            stored_snap = float(cached_whisper.get("snap_trim_start") or 0)
            # Solo aplicar offset si el raw cache es pre-snap (más largo que whisper).
            if (
                stored_snap > 0
                and cached_duration
                and probed_duration > float(cached_duration) + 0.5
            ):
                snap_trim_start = stored_snap

        # Legacy jobs: raw pre-snap en R2 + words post-snap → re-transcribir para alinear.
        if words and clip_duration > 0:
            word_max = max(float(w.get("end", 0)) for w in words)
            legacy_mismatch = (
                not cached_duration
                and clip_duration - word_max > 2.5
                and float(words[0].get("start", 0)) < 0.5
            )
            if legacy_mismatch:
                print(
                    "   🔄 Raw cache legacy (pre-snap) — re-transcribo para alinear subs"
                )
                words, segments = _whisper_per_clip(seg_path, clip_duration, edit_id)
                snap_trim_start = 0.0

        if not words and not segments:
            words, segments = _whisper_per_clip(seg_path, clip_duration, edit_id)

        # Apply user word corrections before render
        word_corrections = edit.get("word_corrections")
        if words and word_corrections:
            try:
                if isinstance(word_corrections, str):
                    word_corrections = json.loads(word_corrections)
                words = apply_word_corrections(words, word_corrections)
                print(f"   ✅ Applied {len(word_corrections)} word_corrections")
            except Exception as e_corr:
                print(f"   ⚠️ word_corrections falló ({e_corr}) — sigo sin correcciones")

        word_styles = edit.get("word_styles")
        if isinstance(word_styles, str):
            try:
                word_styles = json.loads(word_styles)
            except Exception:
                word_styles = None

        # 5) Generate clip with edit's overrides
        clip_output = CLIPS_DIR / f"edit_{edit_id}.mp4"

        # Apply trim offsets if present (recortes adicionales del usuario)
        trim_start = float(edit.get("trim_start_offset") or 0.0)
        trim_end_off = float(edit.get("trim_end_offset") or 0.0)
        clip_start = max(0.0, snap_trim_start + trim_start)
        clip_end = max(clip_start + 1.0, clip_duration - trim_end_off)

        gen = generate_clip(
            video_path=seg_path,
            start_sec=clip_start,
            end_sec=clip_end,
            output_path=str(clip_output),
            segments=segments,
            segments_start_offset_sec=0.0,
            words=words,
            word_styles=word_styles,
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
