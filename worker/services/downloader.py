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
from urllib.request import urlopen, Request, build_opener, ProxyHandler
from concurrent.futures import ThreadPoolExecutor, as_completed

DOWNLOADS_DIR = Path(__file__).parent.parent / "downloads"
COOKIES_FILE = Path(__file__).parent.parent / "cookies.txt"


def _get_proxy_url() -> str | None:
    """
    Proxy residencial para bypassear IP bans de YouTube/googlevideo desde datacenters.
    Formato esperado: http://user:pass@host:port (ej: Webshare rotating residential).

    Aplica a:
      - Chunks de googlevideo en _parallel_download()
      - yt-dlp (fallback de transcript y descarga)
    """
    url = os.getenv("WEBSHARE_PROXY_URL") or os.getenv("HTTP_PROXY_URL")
    return url.strip() if url else None


def _urlopen_maybe_proxied(req, timeout: int = 30):
    """urlopen que usa WEBSHARE_PROXY_URL si está seteado."""
    proxy = _get_proxy_url()
    if proxy:
        opener = build_opener(ProxyHandler({"http": proxy, "https": proxy}))
        return opener.open(req, timeout=timeout)
    return urlopen(req, timeout=timeout)


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

    # Proxy residencial si está configurado (bypassea IP bans de YouTube)
    proxy = _get_proxy_url()
    if proxy:
        base_opts['proxy'] = proxy
        print(f"🌐 yt-dlp usando proxy residencial: {proxy.split('@')[-1] if '@' in proxy else proxy}")

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


def _parallel_download(url: str, out_path: Path, num_chunks: int = 8, label: str = "",
                        end_byte: int = None) -> None:
    """
    Descarga un archivo usando HTTP Range requests en paralelo.

    end_byte: si se pasa, descarga solo hasta ese byte (descarga parcial).
              Si None, descarga el archivo completo.

    googlevideo throttlea conexiones individuales a ~4MB/min. Con N conexiones
    paralelas lográs ~N * 4MB/min. Con 8 chunks ≈ 32MB/min = 40MB en 75s.
    """
    proxy = _get_proxy_url()
    if proxy:
        opener = build_opener(ProxyHandler({"http": proxy, "https": proxy}))
        _open = lambda r, t=30: opener.open(r, timeout=t)
        print(f"   🌐 {label}: vía proxy residencial")
    else:
        _open = lambda r, t=30: urlopen(r, timeout=t)

    req = Request(url, method="HEAD", headers={"User-Agent": _DEFAULT_UA})
    with _open(req, 30) as r:
        total = int(r.headers.get("Content-Length", 0))
    if total <= 0:
        raise RuntimeError(f"{label}: no se pudo determinar Content-Length")

    # Limitar al end_byte pedido (descarga parcial desde byte 0)
    effective_end = (min(end_byte, total - 1) if end_byte is not None else total - 1)
    effective_total = effective_end + 1

    chunk_size = max(1 << 20, effective_total // num_chunks)
    ranges = []
    start = 0
    idx = 0
    while start <= effective_end:
        end = min(effective_end, start + chunk_size - 1)
        ranges.append((idx, start, end))
        start = end + 1
        idx += 1

    tmp_dir = out_path.parent / f".{out_path.stem}_chunks"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir()

    size_label = f"{effective_total // (1<<20)}MB" + (f" de {total // (1<<20)}MB" if end_byte else "")
    print(f"   ⬇️ {label}: {size_label} en {len(ranges)} chunks paralelos")
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
                with _open(rq, 180) as r, open(tmp, "wb") as f:
                    shutil.copyfileobj(r, f, 1 << 20)
                if tmp.stat().st_size != (e - s + 1):
                    raise IOError(f"chunk {i} size mismatch: got {tmp.stat().st_size}, expected {e-s+1}")
                return tmp
            except Exception as ex:
                last_err = ex
                time.sleep(1 + attempt)
        raise RuntimeError(f"chunk {i} failed after 3 attempts: {last_err}")

    with ThreadPoolExecutor(max_workers=len(ranges)) as ex:
        futures = {ex.submit(fetch, i, s, e): i for i, s, e in ranges}
        for fut in as_completed(futures):
            fut.result()

    elapsed = time.time() - t0
    mbps = (effective_total / (1 << 20)) / max(elapsed, 0.01)
    print(f"   ✅ {label}: {effective_total // (1<<20)}MB en {elapsed:.1f}s ({mbps:.1f}MB/s)")

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
            fv = ex.submit(_parallel_download, vid["url"], vid_tmp, 4, "video")
            fa = ex.submit(_parallel_download, aud["url"], aud_tmp, 2, "audio")
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


def download_video_for_clips(
    video_url: str,
    audio_url: str,
    max_end_sec: float,
    video_duration: float,
    video_id: str,
    buffer_sec: float = 15.0,
) -> tuple[str, str]:
    """
    Descarga video+audio desde byte 0 hasta el byte correspondiente a
    max_end_sec + buffer usando Python urllib + proxy.

    Al descargar desde byte 0, el archivo resultante es un MP4/M4A válido
    con el moov atom completo — FFmpeg puede procesarlo sin problemas.

    Para un video de 2h con clips hasta los 4625s:
      - Descarga: (4640/7200) × 1.3GB ≈ 837MB (vs 1.3GB full)
      - Para clips early (< 2000s): (2015/7200) × 1.3GB ≈ 363MB

    Se descarga UNA sola vez para todos los clips del job.

    Returns:
        (video_path, audio_path) — archivos MP4/M4A válidos y procesables
    """
    DOWNLOADS_DIR.mkdir(exist_ok=True)

    effective_end = min(max_end_sec + buffer_sec, video_duration)
    ratio = effective_end / max(video_duration, 1)

    print(f"   📐 Descargando 0s–{effective_end:.0f}s ({ratio * 100:.0f}% del video)")

    # Obtener tamaños totales para calcular end_byte
    proxy = _get_proxy_url()
    if proxy:
        opener = build_opener(ProxyHandler({"http": proxy, "https": proxy}))
        _open_head = lambda r, t=30: opener.open(r, timeout=t)
    else:
        _open_head = lambda r, t=30: urlopen(r, timeout=t)

    def _get_size(url: str) -> int:
        r = Request(url, method="HEAD", headers={"User-Agent": _DEFAULT_UA})
        with _open_head(r, 30) as resp:
            return int(resp.headers.get("Content-Length", 0))

    try:
        vid_total = _get_size(video_url)
        aud_total = _get_size(audio_url)
    except Exception as e:
        raise RuntimeError(f"No se pudo obtener tamaño de streams: {e}")

    if vid_total <= 0 or aud_total <= 0:
        raise RuntimeError(f"Content-Length inválido: video={vid_total}, audio={aud_total}")

    vid_end_byte = int(ratio * vid_total)
    aud_end_byte = int(ratio * aud_total)

    vid_path = DOWNLOADS_DIR / f"{video_id}_pvid.mp4"
    aud_path = DOWNLOADS_DIR / f"{video_id}_paud.m4a"

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=2) as ex:
        fv = ex.submit(_parallel_download, video_url, vid_path, 8, "video", vid_end_byte)
        fa = ex.submit(_parallel_download, audio_url, aud_path, 4, "audio", aud_end_byte)
        fv.result()
        fa.result()

    print(f"   ⏱️ Descarga parcial completada en {time.time() - t0:.1f}s")
    return str(vid_path), str(aud_path)


def download_clip_segment(
    video_url: str,
    audio_url: str,
    start_sec: float,
    end_sec: float,
    video_duration: float,
    video_id: str,
    buffer_sec: float = 12.0,
) -> tuple[str, str]:
    """
    Descarga solo los bytes del segmento necesario para un clip usando
    Python urllib + proxy (el mismo mecanismo que _parallel_download).

    Estrategia:
      - Estima el byte range con: offset = (time / duration) * content_length
      - Agrega buffer_sec antes y después para asegurar que el keyframe anterior
        esté incluido (FFmpeg necesita el I-frame para decodificar correctamente)
      - Descarga video+audio en paralelo usando _parallel_download
      - Devuelve rutas a los archivos temporales (video_seg.mp4, audio_seg.m4a)

    Para un clip de 35s en un video de 2h:
      - Full download: ~1.3GB
      - Este método: ~60-100MB (35s + 24s buffer) ≈ 15x menos

    Returns:
        (video_segment_path, audio_segment_path)
    """
    DOWNLOADS_DIR.mkdir(exist_ok=True)

    proxy = _get_proxy_url()
    if proxy:
        opener = build_opener(ProxyHandler({"http": proxy, "https": proxy}))
        _open = lambda r, t=30: opener.open(r, timeout=t)
    else:
        _open = lambda r, t=30: urlopen(r, timeout=t)

    def _get_size(url: str) -> int:
        req = Request(url, method="HEAD", headers={"User-Agent": _DEFAULT_UA})
        with _open(req, 30) as r:
            return int(r.headers.get("Content-Length", 0))

    def _byte_range(size: int, t_start: float, t_end: float) -> tuple[int, int]:
        """Convierte rango de tiempo a rango de bytes (heurístico CBR)."""
        s_byte = max(0, int((t_start / video_duration) * size))
        e_byte = min(size - 1, int((t_end / video_duration) * size))
        return s_byte, e_byte

    def _range_download(url: str, out_path: Path, s_byte: int, e_byte: int, label: str) -> None:
        """Descarga un rango de bytes específico en paralelo (igual que _parallel_download)."""
        total = e_byte - s_byte + 1
        num_chunks = min(8, max(1, total // (1 << 20)))  # 1 chunk por MB, máx 8
        chunk_size = max(1 << 20, total // num_chunks)

        ranges = []
        cur = s_byte
        idx = 0
        while cur <= e_byte:
            chunk_end = min(e_byte, cur + chunk_size - 1)
            ranges.append((idx, cur, chunk_end))
            cur = chunk_end + 1
            idx += 1

        tmp_dir = out_path.parent / f".{out_path.stem}_chunks"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir()

        print(f"   ⬇️ {label}: {total // (1 << 20)}MB en {len(ranges)} chunks (bytes {s_byte}-{e_byte})")
        t0 = time.time()

        def fetch(i: int, bs: int, be: int) -> Path:
            tmp = tmp_dir / f"chunk_{i:04d}"
            last_err = None
            for attempt in range(3):
                try:
                    rq = Request(url, headers={
                        "User-Agent": _DEFAULT_UA,
                        "Range": f"bytes={bs}-{be}",
                    })
                    with _open(rq, 180) as r, open(tmp, "wb") as f:
                        shutil.copyfileobj(r, f, 1 << 20)
                    return tmp
                except Exception as ex:
                    last_err = ex
                    time.sleep(1 + attempt)
            raise RuntimeError(f"chunk {i} falló: {last_err}")

        with ThreadPoolExecutor(max_workers=len(ranges)) as ex:
            futures = {ex.submit(fetch, i, bs, be): i for i, bs, be in ranges}
            for fut in as_completed(futures):
                fut.result()

        elapsed = time.time() - t0
        print(f"   ✅ {label}: {total // (1 << 20)}MB en {elapsed:.1f}s ({total // (1 << 20) / max(elapsed, 0.01):.1f}MB/s)")

        with open(out_path, "wb") as out:
            for i, _, _ in ranges:
                chunk_path = tmp_dir / f"chunk_{i:04d}"
                with open(chunk_path, "rb") as cf:
                    shutil.copyfileobj(cf, out, 1 << 20)
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # ── Calcular byte ranges con buffer ──────────────────────────────────────
    seg_start = max(0.0, start_sec - buffer_sec)
    seg_end = min(video_duration, end_sec + buffer_sec)

    print(f"   📐 Segmento: {seg_start:.0f}s-{seg_end:.0f}s (clip: {start_sec:.0f}s-{end_sec:.0f}s, buffer: ±{buffer_sec:.0f}s)")

    vid_path = DOWNLOADS_DIR / f"{video_id}_vseg_{int(start_sec)}.mp4"
    aud_path = DOWNLOADS_DIR / f"{video_id}_aseg_{int(start_sec)}.m4a"

    t0 = time.time()
    vid_size, aud_size = 0, 0
    try:
        vid_size = _get_size(video_url)
        aud_size = _get_size(audio_url)
    except Exception as e:
        raise RuntimeError(f"No se pudo obtener tamaño de los streams: {e}")

    if vid_size <= 0 or aud_size <= 0:
        raise RuntimeError(f"Content-Length inválido: video={vid_size}, audio={aud_size}")

    vid_s, vid_e = _byte_range(vid_size, seg_start, seg_end)
    aud_s, aud_e = _byte_range(aud_size, seg_start, seg_end)

    # Descargar video+audio en paralelo
    with ThreadPoolExecutor(max_workers=2) as ex:
        fv = ex.submit(_range_download, video_url, vid_path, vid_s, vid_e, "video-seg")
        fa = ex.submit(_range_download, audio_url, aud_path, aud_s, aud_e, "audio-seg")
        fv.result()
        fa.result()

    print(f"   ⏱️ Segmento descargado en {time.time() - t0:.1f}s total")
    return str(vid_path), str(aud_path)


def get_stream_urls_rapidapi(video_url: str, video_id: str = None, max_height: int = 720) -> dict:
    """
    Obtiene URLs de stream de video+audio vía RapidAPI SIN descargar nada.

    Retorna {"video_url": str, "audio_url": str, "video_id": str}.
    Llama al mismo endpoint que download_video_rapidapi pero solo devuelve
    las URLs — FFmpeg luego hace HTTP Range requests para los segmentos exactos
    que necesita (descarga selectiva: ~50MB en vez de 1.3GB).
    """
    api_key = os.getenv("RAPIDAPI_KEY")
    if not api_key:
        raise RuntimeError("RAPIDAPI_KEY no está seteada en el entorno")

    if not video_id:
        video_id = _extract_video_id(video_url)

    print(f"🔌 RapidAPI: obteniendo URLs de stream para {video_id}...")
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

    print(f"   ✅ URLs obtenidas: video={vid_quality}p avc1 | audio={aud_bitrate // 1000}kbps")
    return {
        "video_url": vid["url"],
        "audio_url": aud["url"],
        "video_id": video_id,
    }


def _get_stream_urls_ytdlp(video_url: str, video_id: str = None) -> dict:
    """
    Extrae URLs de stream vía yt-dlp sin descargar (download=False).
    Retorna {"video_url": str, "audio_url": str, "video_id": str}.
    """
    ydl_opts = _build_ydl_opts({
        'format': 'bestvideo[height<=720][vcodec^=avc1]+bestaudio[acodec^=mp4a]/bestvideo[height<=720]+bestaudio',
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
    })
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        if not video_id:
            video_id = info['id']

        # Para formatos merged yt-dlp pone los componentes en requested_formats
        req_formats = info.get('requested_formats', [])
        if len(req_formats) >= 2:
            v_fmt = next((f for f in req_formats if f.get('vcodec', 'none') != 'none'), None)
            a_fmt = next((f for f in req_formats
                         if f.get('acodec', 'none') != 'none' and f.get('vcodec', 'none') == 'none'), None)
        elif len(req_formats) == 1:
            v_fmt = req_formats[0]
            a_fmt = req_formats[0]
        else:
            v_fmt = info if info.get('url') else None
            a_fmt = info if info.get('url') else None

        if not v_fmt or not a_fmt or not v_fmt.get('url') or not a_fmt.get('url'):
            raise RuntimeError("yt-dlp no pudo obtener URLs de stream utilizables")

        print(f"   ✅ URLs yt-dlp: video={v_fmt.get('height', '?')}p | audio OK")
        return {
            "video_url": v_fmt["url"],
            "audio_url": a_fmt["url"],
            "video_id": video_id,
        }


def get_stream_urls(video_url: str, video_id: str = None) -> dict:
    """
    Obtiene URLs de stream de video+audio sin descargar el archivo completo.
    Intenta yt-dlp primero (gratis); si falla cae a RapidAPI.

    Forzar RapidAPI con USE_RAPIDAPI_DOWNLOAD=true.

    Retorna {"video_url": str, "audio_url": str, "video_id": str}.
    Estas URLs se pasan directamente a FFmpeg para descarga selectiva:
    FFmpeg solo descarga los bytes correspondientes al clip pedido (~50MB
    en vez de los ~1.3GB del video completo).
    """
    force_rapidapi = os.getenv("USE_RAPIDAPI_DOWNLOAD", "").lower() in ("1", "true", "yes")

    if not force_rapidapi:
        try:
            return _get_stream_urls_ytdlp(video_url, video_id)
        except Exception as e:
            print(f"⚠️ yt-dlp stream URLs falló ({type(e).__name__}: {str(e)[:120]}) — fallback a RapidAPI")
    else:
        print("ℹ️ USE_RAPIDAPI_DOWNLOAD=true, usando RapidAPI para stream URLs")

    return get_stream_urls_rapidapi(video_url, video_id)


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
