"""
YouTube downloader service using yt-dlp
Downloads both audio (for Gemini) and video (for clipping).

Sprint 1.1 Día 9: Añadido fallback RapidAPI (yt-api) para cuando yt-dlp
es bloqueado por IP de datacenter (Render/Railway/etc).
"""
import yt_dlp
import os
import re
import time
import base64
import shutil
import subprocess
import tempfile
import urllib.parse
from pathlib import Path
from urllib.request import urlopen, Request
from concurrent.futures import ThreadPoolExecutor, as_completed

DOWNLOADS_DIR = Path(__file__).parent.parent / "downloads"
COOKIES_FILE = Path(__file__).parent.parent / "cookies.txt"


def _get_cookies_path():
    """Get YouTube cookies file path for yt-dlp authentication.
    Priority: YOUTUBE_COOKIES env var (base64) > cookies.txt file
    """
    # Option 1: Base64-encoded cookies in env var
    cookies_b64 = os.getenv('YOUTUBE_COOKIES')
    if cookies_b64:
        tmp = Path(tempfile.gettempdir()) / 'yt_cookies.txt'
        try:
            decoded = base64.b64decode(cookies_b64)
            tmp.write_bytes(decoded)
            print(f"🍪 Decoded cookies from env var ({len(decoded)} bytes) -> {tmp}")
            # Print first line to verify format
            first_line = decoded.decode('utf-8', errors='ignore').split('\n')[0]
            print(f"🍪 First line: {first_line[:60]}...")
            return str(tmp)
        except Exception as e:
            print(f"❌ Failed to decode YOUTUBE_COOKIES: {e}")
            return None
    
    # Option 2: cookies.txt file in worker dir
    if COOKIES_FILE.exists():
        print(f"🍪 Using cookies file: {COOKIES_FILE} ({COOKIES_FILE.stat().st_size} bytes)")
        return str(COOKIES_FILE)
    
    print("⚠️ No YouTube cookies found (set YOUTUBE_COOKIES env var)")
    return None


def _build_ydl_opts(base_opts: dict) -> dict:
    """Add cookies and common options to yt-dlp opts."""
    cookies_path = _get_cookies_path()
    if cookies_path:
        base_opts['cookiefile'] = cookies_path
    
    # Use mobile clients to bypass bot detection (android/ios don't require sign-in)
    base_opts['extractor_args'] = {'youtube': {'player_client': ['android', 'web']}}
    
    return base_opts


# FFmpeg location - use env var or find in PATH
FFMPEG_LOCATION = os.getenv('FFMPEG_PATH')
if not FFMPEG_LOCATION:
    ffmpeg_exe = shutil.which('ffmpeg')
    if ffmpeg_exe:
        FFMPEG_LOCATION = str(Path(ffmpeg_exe).parent)


def download_audio(video_url: str) -> tuple[str, dict]:
    """
    Download audio from YouTube video for Gemini analysis.
    
    Returns:
        Tuple of (audio_path, video_info)
    """
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    
    ydl_opts = _build_ydl_opts({
        'format': 'bestaudio/best',
        'outtmpl': str(DOWNLOADS_DIR / '%(id)s_audio.%(ext)s'),
        'ffmpeg_location': FFMPEG_LOCATION,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '128',
        }],
        'quiet': False,
        'no_warnings': False,
        'nocheckcertificate': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
        }
    })
    
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


def _extract_video_id(video_url: str) -> str:
    """Extracts the 11-char YouTube video id from any common URL form."""
    # youtu.be/ID, watch?v=ID, /embed/ID, /shorts/ID
    m = re.search(r"(?:v=|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})", video_url)
    if m:
        return m.group(1)
    # Plain id
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_url.strip()):
        return video_url.strip()
    raise ValueError(f"No pude extraer video_id de: {video_url}")


_DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def _parallel_download(url: str, out_path: Path, num_chunks: int = 8, label: str = "") -> None:
    """
    Descarga un archivo grande en paralelo usando HTTP Range requests.

    googlevideo throttlea conexiones individuales a ~4MB/min. Con N conexiones
    paralelas lográs ~N * 4MB/min. Con 8 chunks ≈ 32MB/min = 40MB en 75s.

    Escribe a archivos temporales en un subdir, los concatena en orden al final.
    Retriea cada chunk hasta 3 veces antes de fallar.
    """
    # HEAD para obtener tamaño total
    req = Request(url, method="HEAD", headers={"User-Agent": _DEFAULT_UA})
    with urlopen(req, timeout=30) as r:
        total = int(r.headers.get("Content-Length", 0))
    if total <= 0:
        raise RuntimeError(f"{label}: no se pudo determinar Content-Length")

    chunk_size = max(1 << 20, total // num_chunks)  # mínimo 1MB por chunk
    ranges = []
    start = 0
    idx = 0
    while start < total:
        end = min(total - 1, start + chunk_size - 1)
        ranges.append((idx, start, end))
        start = end + 1
        idx += 1

    tmp_dir = out_path.parent / f".{out_path.stem}_chunks"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir()

    print(f"   ⬇️ {label}: {total // (1<<20)}MB en {len(ranges)} chunks paralelos")
    t0 = time.time()

    def fetch(i: int, s: int, e: int) -> Path:
        tmp = tmp_dir / f"chunk_{i:04d}"
        last_err = None
        for attempt in range(3):
            try:
                rq = Request(url, headers={
                    "User-Agent": _DEFAULT_UA,
                    "Range": f"bytes={s}-{e}",
                })
                with urlopen(rq, timeout=180) as r, open(tmp, "wb") as f:
                    shutil.copyfileobj(r, f, 1 << 20)
                if tmp.stat().st_size != (e - s + 1):
                    raise IOError(f"chunk {i} size mismatch: got {tmp.stat().st_size}, expected {e-s+1}")
                return tmp
            except Exception as ex:
                last_err = ex
                time.sleep(1 + attempt)
        raise RuntimeError(f"chunk {i} failed after 3 attempts: {last_err}")

    # Descarga paralela
    with ThreadPoolExecutor(max_workers=len(ranges)) as ex:
        futures = {ex.submit(fetch, i, s, e): i for i, s, e in ranges}
        for fut in as_completed(futures):
            fut.result()  # raise si alguno falló

    elapsed = time.time() - t0
    mbps = (total / (1 << 20)) / max(elapsed, 0.01)
    print(f"   ✅ {label}: {total // (1<<20)}MB en {elapsed:.1f}s ({mbps:.1f}MB/s)")

    # Concatenar en orden
    with open(out_path, "wb") as out:
        for i, _, _ in ranges:
            chunk_path = tmp_dir / f"chunk_{i:04d}"
            with open(chunk_path, "rb") as cf:
                shutil.copyfileobj(cf, out, 1 << 20)

    shutil.rmtree(tmp_dir, ignore_errors=True)


def _stream_download(url: str, out_path: Path, label: str = "") -> None:
    """[Deprecated — usamos _parallel_download por throttling de googlevideo.]"""
    _parallel_download(url, out_path, label=label)


def download_video_rapidapi(video_url: str, video_id: str = None, max_height: int = 720) -> str:
    """
    Fallback: descarga vía RapidAPI YT-API (yt-api.p.rapidapi.com).
    Pide metadata -> elige mejor video MP4 h264 ≤max_height + mejor audio AAC ->
    baja ambos -> muxea con ffmpeg en un solo MP4.

    Raises:
        RuntimeError si falla el API o no hay RAPIDAPI_KEY.
    """
    api_key = os.getenv("RAPIDAPI_KEY")
    if not api_key:
        raise RuntimeError("RAPIDAPI_KEY no está seteada en el entorno")

    if not video_id:
        video_id = _extract_video_id(video_url)

    print(f"🔌 Fallback RapidAPI: pidiendo formatos de {video_id}...")
    import json
    meta_url = f"https://yt-api.p.rapidapi.com/dl?id={video_id}"
    req = Request(meta_url, headers={
        "x-rapidapi-key": api_key,
        "x-rapidapi-host": "yt-api.p.rapidapi.com",
    })
    with urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))

    if data.get("status") != "OK":
        raise RuntimeError(f"YT-API devolvió status={data.get('status')}: {data.get('reason', '')}")

    # Elegir mejor video MP4 (h264/avc1, no av01 por compatibilidad ffmpeg) ≤max_height
    vid = None
    vid_quality = 0
    for f in data.get("adaptiveFormats", []):
        mime = f.get("mimeType", "")
        if "video/mp4" not in mime or "avc1" not in mime:
            continue
        ql = f.get("qualityLabel", "")
        h = int(re.match(r"(\d+)", ql).group(1)) if re.match(r"(\d+)", ql) else 0
        if h > max_height:
            continue
        if h > vid_quality and f.get("url"):
            vid = f
            vid_quality = h

    # Elegir mejor audio MP4
    aud = None
    aud_bitrate = 0
    for f in data.get("adaptiveFormats", []):
        if "audio/mp4" not in f.get("mimeType", ""):
            continue
        br = f.get("bitrate", 0)
        if br > aud_bitrate and f.get("url"):
            aud = f
            aud_bitrate = br

    if not vid or not aud:
        raise RuntimeError(f"YT-API no devolvió formatos utilizables (vid={bool(vid)}, aud={bool(aud)})")

    print(f"   video: {vid_quality}p avc1 | audio: {aud_bitrate // 1000}kbps AAC")

    DOWNLOADS_DIR.mkdir(exist_ok=True)
    vid_tmp = DOWNLOADS_DIR / f"{video_id}_video_only.mp4"
    aud_tmp = DOWNLOADS_DIR / f"{video_id}_audio_only.m4a"
    final_path = DOWNLOADS_DIR / f"{video_id}_video.mp4"

    ffmpeg_bin = "ffmpeg"
    if FFMPEG_LOCATION:
        p = Path(FFMPEG_LOCATION)
        if p.is_file():
            ffmpeg_bin = str(p)
        elif p.is_dir():
            candidate = p / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            ffmpeg_bin = str(candidate if candidate.exists() else p / "ffmpeg")

    try:
        # Bajar ambos streams en paralelo entre sí (2 threads de alto nivel),
        # cada uno con 8 chunks paralelos internamente = 16 conexiones totales.
        # Esto bypassa el throttle de googlevideo (~4MB/min/conn).
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=2) as ex:
            fv = ex.submit(_parallel_download, vid["url"], vid_tmp, 8, "video")
            fa = ex.submit(_parallel_download, aud["url"], aud_tmp, 4, "audio")
            fv.result()
            fa.result()
        print(f"   ⏱️ Descarga total: {time.time() - t0:.1f}s")

        # Mux sin re-encode — local, rápido
        cmd = [
            ffmpeg_bin, "-y", "-loglevel", "warning",
            "-i", str(vid_tmp),
            "-i", str(aud_tmp),
            "-c", "copy",
            "-movflags", "+faststart",
            str(final_path),
        ]
        print(f"   🎬 Muxeando video+audio...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg mux falló: {result.stderr[-500:]}")
    finally:
        for p in (vid_tmp, aud_tmp):
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass

    if not final_path.exists():
        raise RuntimeError("No se generó el MP4 final")
    size_mb = final_path.stat().st_size / (1 << 20)
    print(f"✅ RapidAPI descarga OK: {final_path.name} ({size_mb:.1f}MB)")
    return str(final_path)


def download_video(video_url: str, video_id: str = None) -> str:
    """
    Download video for clipping.
    Intenta yt-dlp primero (mejor calidad, gratis). Si falla (IP bloqueada
    en prod, bot detection, etc) cae a RapidAPI automáticamente.

    Forzar RapidAPI con USE_RAPIDAPI_DOWNLOAD=true.

    Returns:
        Path to downloaded video
    """
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    force_rapidapi = os.getenv("USE_RAPIDAPI_DOWNLOAD", "").lower() in ("1", "true", "yes")

    if not force_rapidapi:
        # Intento 1: yt-dlp
        try:
            return _download_video_ytdlp(video_url, video_id)
        except Exception as e:
            print(f"⚠️ yt-dlp falló ({type(e).__name__}: {str(e)[:120]}) — fallback a RapidAPI")
    else:
        print("ℹ️ USE_RAPIDAPI_DOWNLOAD=true, saltando yt-dlp")

    # Intento 2: RapidAPI
    return download_video_rapidapi(video_url, video_id)


def _download_video_ytdlp(video_url: str, video_id: str = None) -> str:
    """Download via yt-dlp (original implementation)."""
    ydl_opts = _build_ydl_opts({
        'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]',
        'outtmpl': str(DOWNLOADS_DIR / '%(id)s_video.%(ext)s'),
        'ffmpeg_location': FFMPEG_LOCATION,
        'merge_output_format': 'mp4',
        'quiet': False,
        'no_warnings': False,
        'nocheckcertificate': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
        }
    })

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        if not video_id:
            video_id = info['id']

        video_path = DOWNLOADS_DIR / f"{video_id}_video.mp4"
        if not video_path.exists():
            for ext in ['webm', 'mkv']:
                alt = DOWNLOADS_DIR / f"{video_id}_video.{ext}"
                if alt.exists():
                    video_path = alt
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
