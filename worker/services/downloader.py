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


def _get_proxy_list() -> list[str]:
    """
    Devuelve TODOS los proxies configurados, en orden.

    Soporta varios formatos para que el usuario pueda configurar 1 o N proxies:

    1. WEBSHARE_PROXY_FILE=/path/a/proxies.txt
       - Archivo con UN proxy por línea (formato: http://user:pass@host:port)
       - Líneas vacías o con # se ignoran
       - Ideal para 20+ proxies de Webshare

    2. WEBSHARE_PROXY_LIST="http://...,http://...,http://..."
       - Lista separada por comas o newlines en una sola env var
       - Práctico para 2-5 proxies

    3. WEBSHARE_PROXY_URL=http://user:pass@host:port  (legacy)
       - Un solo proxy. Backward-compat.
    """
    import re as _re

    # 1. Archivo
    path = os.getenv("WEBSHARE_PROXY_FILE")
    if path:
        try:
            from pathlib import Path as _P
            lines = _P(path).read_text(encoding="utf-8").splitlines()
            urls = [
                ln.strip() for ln in lines
                if ln.strip() and not ln.strip().startswith("#")
            ]
            if urls:
                return urls
        except Exception as e:
            print(f"⚠️ No se pudo leer WEBSHARE_PROXY_FILE={path}: {e}")

    # 2. Lista inline
    multi = os.getenv("WEBSHARE_PROXY_LIST", "")
    if multi:
        urls = [u.strip() for u in _re.split(r"[,\n]", multi) if u.strip()]
        if urls:
            return urls

    # 3. Single (legacy)
    single = os.getenv("WEBSHARE_PROXY_URL") or os.getenv("HTTP_PROXY_URL")
    if single:
        return [single.strip()]

    return []


def _get_proxy_url() -> str | None:
    """
    Backward-compat: devuelve el primer proxy de la lista, o None.

    Para descargas que se beneficien de paralelizar entre proxies, usar
    _get_proxy_list() directamente.
    """
    lst = _get_proxy_list()
    return lst[0] if lst else None


def _urlopen_maybe_proxied(req, timeout: int = 30):
    """urlopen que usa WEBSHARE_PROXY_URL si está seteado."""
    proxy = _get_proxy_url()
    if proxy:
        opener = build_opener(ProxyHandler({"http": proxy, "https": proxy}))
        return opener.open(req, timeout=timeout)
    return urlopen(req, timeout=timeout)


def _get_cookies_path():
    """Get YouTube cookies file path for yt-dlp authentication.
    Priority: YOUTUBE_COOKIES env var (base64 or plaintext) > cookies.txt file
    """
    cookies_raw = (os.getenv('YOUTUBE_COOKIES') or '').strip()
    if cookies_raw:
        tmp = Path(tempfile.gettempdir()) / 'yt_cookies.txt'
        # Plaintext Netscape (usuario pegó el archivo sin base64)
        if cookies_raw.startswith('#') or cookies_raw.lstrip().startswith('.google'):
            try:
                tmp.write_text(cookies_raw, encoding='utf-8')
                print(f"🍪 YOUTUBE_COOKIES: plaintext Netscape ({len(cookies_raw)} chars)")
                return str(tmp)
            except Exception as e:
                print(f"❌ Failed to write YOUTUBE_COOKIES plaintext: {e}")
                return None
        try:
            # Limpiar saltos de línea/espacios del base64 (común al pegar en Render)
            b64_clean = ''.join(cookies_raw.split())
            decoded = base64.b64decode(b64_clean, validate=True)
            tmp.write_bytes(decoded)
            print(f"🍪 Decoded YOUTUBE_COOKIES ({len(decoded)} bytes) -> {tmp}")
            first_line = decoded.decode('utf-8', errors='ignore').split('\n')[0]
            print(f"🍪 First line: {first_line[:60]}...")
            return str(tmp)
        except Exception as e:
            print(
                f"❌ Failed to decode YOUTUBE_COOKIES "
                f"({len(cookies_raw)} chars in env): {e}"
            )
            return None

    # Option 2: cookies.txt file in worker dir
    if COOKIES_FILE.exists():
        print(f"🍪 Using cookies file: {COOKIES_FILE} ({COOKIES_FILE.stat().st_size} bytes)")
        return str(COOKIES_FILE)

    print("⚠️ No YouTube cookies found (set YOUTUBE_COOKIES env var)")
    return None


def cookies_env_status() -> str:
    """Diagnóstico seguro para logs (sin exponer el valor)."""
    raw = os.getenv('YOUTUBE_COOKIES') or ''
    if not raw.strip():
        return 'MISSING'
    return f'SET ({len(raw.strip())} chars)'


def _build_ydl_opts(
    base_opts: dict,
    *,
    player_clients: list[str] | None = None,
    proxy_url: str | None = None,
) -> dict:
    """Add cookies, player_client cascade, and proxy to yt-dlp opts."""
    cookies_path = _get_cookies_path()
    if cookies_path:
        base_opts['cookiefile'] = cookies_path

    clients = player_clients or ['tv', 'ios', 'android', 'web']
    base_opts['extractor_args'] = {'youtube': {'player_client': clients}}

    proxy = proxy_url or _get_proxy_url()
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


def is_ytdlp_drm_error(exc: BaseException) -> bool:
    """True cuando yt-dlp no puede bajar por DRM, PO Token o experimentos tv client."""
    msg = str(exc).lower()
    return (
        "drm protected" in msg
        or "drm" in msg and "sign in" not in msg
        or "po token" in msg
        or "challenge solving failed" in msg
        or "n challenge" in msg
    )


def is_ytdlp_bot_error(exc: BaseException) -> bool:
    """True cuando YouTube pide cookies / verificación anti-bot."""
    msg = str(exc).lower()
    return (
        "sign in to confirm" in msg
        or "not a bot" in msg
        or "confirm you're not a bot" in msg
        or "cookies" in msg and "bot" in msg
    )


def has_youtube_cookies() -> bool:
    return _get_cookies_path() is not None


def _pick_rapidapi_formats(
    data: dict,
    max_height: int = 720,
) -> tuple[dict, dict]:
    """
    Elige mejor par video+audio de adaptiveFormats de YT-API.
    Preferimos h264/mp4 pero aceptamos cualquier video/mp4 si no hay avc1.
    """
    formats = data.get("adaptiveFormats") or []

    def _height(f: dict) -> int:
        ql = f.get("qualityLabel", "")
        m = re.match(r"(\d+)", ql)
        return int(m.group(1)) if m else 0

    vid = None
    vid_quality = 0
    vid_fallback = None
    vid_fallback_q = 0
    for f in formats:
        mime = f.get("mimeType", "")
        if "video/" not in mime or not f.get("url"):
            continue
        h = _height(f)
        if h > max_height:
            continue
        if "video/mp4" in mime and "avc1" in mime and h > vid_quality:
            vid = f
            vid_quality = h
        elif "video/mp4" in mime and h > vid_fallback_q:
            vid_fallback = f
            vid_fallback_q = h

    if not vid:
        vid = vid_fallback

    aud = None
    aud_bitrate = 0
    for f in formats:
        mime = f.get("mimeType", "")
        if "audio/mp4" not in mime or not f.get("url"):
            continue
        br = f.get("bitrate", 0)
        if br > aud_bitrate:
            aud = f
            aud_bitrate = br

    if not vid or not aud:
        raise RuntimeError(f"YT-API no devolvió formatos utilizables (vid={bool(vid)}, aud={bool(aud)})")

    return vid, aud


_GOOGLEVIDEO_HEADERS = {
    "User-Agent": _DEFAULT_UA,
    "Origin": "https://www.youtube.com",
    "Referer": "https://www.youtube.com/",
    "Accept": "*/*",
    "Accept-Encoding": "identity",
}

_YT_MEDIA_HEADERS = {
    **_GOOGLEVIDEO_HEADERS,
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-Dest": "video",
    "Accept-Language": "en-US,en;q=0.9",
}


def _parse_content_range(header: str | None) -> int | None:
    """Parse Content-Range: bytes 0-0/12345 → 12345."""
    if not header:
        return None
    m = re.search(r"/(\d+)\s*$", header.strip())
    return int(m.group(1)) if m else None


def _probe_stream_info(url: str, label: str = "stream") -> tuple[int, str | None, str]:
    """
    Obtiene Content-Length y el proxy/estrategia que funcionó.

    Las URLs de googlevideo (p. ej. vía RapidAPI) deben descargarse con la
    MISMA IP que pasó el probe — round-robin entre proxies distintos → 403.
    """
    proxies = _get_proxy_list()
    strategies: list[tuple[str, str | None, bool]] = [
        ("direct-yt", None, True),
        ("direct", None, False),
    ]
    for i, p in enumerate(proxies):
        name = f"proxy[{i + 1}/{len(proxies)}]" if len(proxies) > 1 else "proxy"
        strategies.append((name, p, True))

    plain_headers = {
        "User-Agent": _DEFAULT_UA,
        "Accept-Encoding": "identity",
    }
    last_err: Exception | None = None
    errors: list[str] = []

    for name, proxy_url, use_yt_headers in strategies:
        try:
            if proxy_url:
                opener = build_opener(ProxyHandler({"http": proxy_url, "https": proxy_url}))
                _open = lambda r, t=30: opener.open(r, timeout=t)
            else:
                _open = lambda r, t=30: urlopen(r, timeout=t)

            base_hdrs = _YT_MEDIA_HEADERS if use_yt_headers else plain_headers

            try:
                with _open(Request(url, method="HEAD", headers=dict(base_hdrs)), 30) as resp:
                    size = int(resp.headers.get("Content-Length", 0))
                if size > 0:
                    if name != "direct-yt":
                        print(f"   📦 {label} size via {name} (HEAD): {size // (1 << 20)}MB")
                    return size, proxy_url, name
            except Exception as head_err:
                last_err = head_err

            get_hdrs = {**base_hdrs, "Range": "bytes=0-0"}
            with _open(Request(url, headers=get_hdrs), 30) as resp:
                size = _parse_content_range(resp.headers.get("Content-Range"))
                if not size:
                    size = int(resp.headers.get("Content-Length", 0))
            if size > 0:
                print(f"   📦 {label} size via {name} (GET probe): {size // (1 << 20)}MB")
                return size, proxy_url, name
            raise RuntimeError("Content-Length inválido o no informado")
        except Exception as e:
            last_err = e
            errors.append(f"{name}: {str(e)[:40]}")
            if name == "direct-yt" and proxies:
                print(f"   ⚠️ {label}: probe direct falló ({str(e)[:50]}), probando proxies...")
            continue

    proxy_info = (
        f"{len(proxies)} proxies configurados"
        if proxies
        else "sin proxies — configura WEBSHARE_PROXY_FILE o WEBSHARE_PROXY_URL"
    )
    raise RuntimeError(
        f"No se pudo obtener tamaño de {label}: {last_err} "
        f"({proxy_info}; intentos: {', '.join(errors[:4])}"
        f"{'...' if len(errors) > 4 else ''})"
    )


def _probe_stream_size(url: str, label: str = "stream") -> int:
    size, _, _ = _probe_stream_info(url, label)
    return size


def _is_googlevideo_url(url: str) -> bool:
    return "googlevideo.com" in url or "googleusercontent.com" in url


def _probe_stream_info_for_proxy(
    url: str,
    proxy_url: str,
    label: str = "stream",
) -> tuple[int, str, str]:
    """Probe Content-Length usando un proxy específico (misma IP que resolvió URLs)."""
    name = "resolve_proxy"
    opener = build_opener(ProxyHandler({"http": proxy_url, "https": proxy_url}))
    _open = lambda r, t=30: opener.open(r, timeout=t)
    base_hdrs = _YT_MEDIA_HEADERS
    try:
        with _open(Request(url, method="HEAD", headers=dict(base_hdrs)), 30) as resp:
            size = int(resp.headers.get("Content-Length", 0))
        if size > 0:
            return size, proxy_url, name
    except Exception:
        pass
    get_hdrs = {**base_hdrs, "Range": "bytes=0-0"}
    with _open(Request(url, headers=get_hdrs), 30) as resp:
        size = _parse_content_range(resp.headers.get("Content-Range"))
        if not size:
            size = int(resp.headers.get("Content-Length", 0))
    if size > 0:
        print(f"   📦 {label} size via {name} (GET probe): {size // (1 << 20)}MB")
        return size, proxy_url, name
    raise RuntimeError(f"{label}: probe falló con resolve_proxy")


def _download_bytes_sequential(
    url: str,
    out_path: Path,
    end_byte: int | None = None,
    known_total: int | None = None,
    *,
    label: str = "stream",
    sticky_proxy: str | None = None,
    sticky_via: str = "",
) -> None:
    """
    Descarga secuencial (1 conexión) vía proxy sticky.

    googlevideo rechaza chunks paralelos en URLs de RapidAPI; una sola conexión
    Range bytes=0-N por la misma IP que resolvió/probeó funciona de forma fiable.
    """
    if known_total and known_total > 0 and sticky_proxy:
        total = known_total
    elif sticky_proxy:
        total, _, sticky_via = _probe_stream_info_for_proxy(url, sticky_proxy, label)
    else:
        total, sticky_proxy, sticky_via = _probe_stream_info(url, label)

    if not sticky_proxy:
        raise RuntimeError(f"{label}: googlevideo requiere proxy residencial para descarga")

    effective_end = min(end_byte, total - 1) if end_byte is not None else total - 1
    want = effective_end + 1
    print(
        f"   📥 {label}: descarga secuencial {want // (1 << 20)}MB "
        f"via {sticky_via or 'proxy'}"
    )

    opener = build_opener(ProxyHandler({"http": sticky_proxy, "https": sticky_proxy}))
    hdrs = {**_YT_MEDIA_HEADERS, "Range": f"bytes=0-{effective_end}"}
    t0 = time.time()
    with opener.open(Request(url, headers=hdrs), timeout=600) as resp:
        with open(out_path, "wb") as f:
            shutil.copyfileobj(resp, f, 1 << 20)
    elapsed = time.time() - t0
    got = out_path.stat().st_size
    if got <= 0:
        raise RuntimeError(f"{label}: descarga secuencial vacía")
    print(f"   ✅ {label}: {got // (1 << 20)}MB en {elapsed:.1f}s ({got // (1 << 20) / max(elapsed, 0.01):.1f}MB/s)")


def _parallel_download(url: str, out_path: Path, num_chunks: int = 8, label: str = "",
                        end_byte: int = None, known_total: int | None = None,
                        sticky_proxy: str | None = None,
                        sticky_via: str = "") -> None:
    """
    Descarga un archivo usando HTTP Range requests en paralelo.

    googlevideo / RapidAPI: URLs firmadas deben descargarse con la MISMA IP
    que pasó el probe (sticky_proxy). Round-robin entre proxies → 403.

    end_byte: si se pasa, descarga solo hasta ese byte (descarga parcial).
              Si None, descarga el archivo completo.
    """
    proxies = _get_proxy_list()
    n_proxies = len(proxies) if proxies else 0
    use_sticky = _is_googlevideo_url(url) or sticky_proxy is not None

    def _make_opener(proxy_url: str | None):
        if proxy_url:
            return build_opener(ProxyHandler({"http": proxy_url, "https": proxy_url}))
        return None

    def _open_with(opener, req, timeout=30):
        if opener:
            return opener.open(req, timeout=timeout)
        return urlopen(req, timeout=timeout)

    total = known_total or 0
    if use_sticky and sticky_proxy is None:
        total, sticky_proxy, sticky_via = _probe_stream_info(url, label=label or "stream")
    elif total <= 0:
        total = _probe_stream_size(url, label=label or "stream")

    if total <= 0:
        raise RuntimeError(f"{label}: no se pudo determinar Content-Length")

    if use_sticky:
        if sticky_via:
            print(f"   🔒 {label}: descarga sticky via {sticky_via}")
        # Fallback chain: proxy que funcionó en probe → resto del pool → directo
        sticky_candidates: list[str | None] = []
        if sticky_proxy:
            sticky_candidates.append(sticky_proxy)
        sticky_candidates.extend(p for p in proxies if p != sticky_proxy)
        sticky_candidates.append(None)
        chunk_openers = [_make_opener(p) for p in sticky_candidates]
        proxy_label = f" sticky ({sticky_via or 'googlevideo'})"
    elif n_proxies > 0:
        chunk_openers = [_make_opener(p) for p in proxies]
        proxy_label = (
            f" cada uno via proxy distinto (round-robin sobre {n_proxies})"
            if n_proxies > 1 else ""
        )
        print(f"   🌐 {label}: pool de {n_proxies} proxies disponibles")
    else:
        chunk_openers = [None]
        proxy_label = ""

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
    print(f"   ⬇️ {label}: {size_label} en {len(ranges)} chunks paralelos{proxy_label}")
    t0 = time.time()

    max_attempts = 3 if use_sticky else 3

    def fetch(i: int, s: int, e: int) -> Path:
        tmp = tmp_dir / f"chunk_{i:04d}"
        last_err = None
        for attempt in range(max_attempts):
            if use_sticky:
                opener_idx = min(attempt, len(chunk_openers) - 1)
            else:
                opener_idx = (i + attempt) % len(chunk_openers)
            opener = chunk_openers[opener_idx]
            try:
                hdrs = {**_YT_MEDIA_HEADERS, "Range": f"bytes={s}-{e}"}
                rq = Request(url, headers=hdrs)
                with _open_with(opener, rq, 180) as r, open(tmp, "wb") as f:
                    shutil.copyfileobj(r, f, 1 << 20)
                if tmp.stat().st_size != (e - s + 1):
                    raise IOError(f"chunk {i} size mismatch: got {tmp.stat().st_size}, expected {e-s+1}")
                return tmp
            except Exception as ex:
                last_err = ex
                if attempt > 0:
                    print(f"   ↻ {label} chunk {i}: retry {attempt+1}/{max_attempts} (opener {opener_idx}): {str(ex)[:80]}")
                time.sleep(1 + attempt)
        raise RuntimeError(f"chunk {i} failed after {max_attempts} attempts: {last_err}")

    # Cap at 8 threads — beyond that, the network bottleneck dominates and
    # extra threads only add context-switch overhead + memory pressure.
    #
    # Global watchdog: 8 min absolute deadline for the whole parallel download.
    # If a single chunk hangs (slow proxy, broken connection silently delivering
    # bytes too slowly to trip the socket timeout) we used to wait forever.
    # Now we cap it. 8 min covers a worst-case 170MB at 350KB/s safely.
    DOWNLOAD_DEADLINE_SEC = 8 * 60
    with ThreadPoolExecutor(max_workers=min(len(ranges), 8)) as ex:
        futures = {ex.submit(fetch, i, s, e): i for i, s, e in ranges}
        try:
            for fut in as_completed(futures, timeout=DOWNLOAD_DEADLINE_SEC):
                fut.result()
        except TimeoutError:
            done_count = sum(1 for f in futures if f.done())
            print(f"   ⏰ {label}: timeout global tras {DOWNLOAD_DEADLINE_SEC}s "
                  f"({done_count}/{len(futures)} chunks completos)")
            for f in futures:
                if not f.done():
                    f.cancel()
            raise RuntimeError(
                f"{label}: parallel download timed out after "
                f"{DOWNLOAD_DEADLINE_SEC}s ({done_count}/{len(futures)} chunks)"
            )

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


def _download_with_progress(
    url: str,
    out_path: Path,
    label: str = "audio",
    *,
    try_direct_first: bool = True,
    overall_timeout_sec: int = 360,
    stall_timeout_sec: int = 60,
    log_interval_sec: float = 5.0,
) -> None:
    """
    Single-connection sequential downloader con diagnóstico detallado.

    Logs progreso (bytes recibidos, throughput, %) cada `log_interval_sec`
    para entender exactamente qué pasa con descargas lentas.

    Estrategia (cuando hay proxy configurado y try_direct_first=True):
      1. Intento DIRECT (sin proxy) — googlevideo CDN normalmente no IP-banea
         para descarga de bytes, solo el watch page. Suele ser MUCHO más rápido.
      2. Si direct falla (403, etc), cae a proxy residencial.

    Si no hay proxy configurado, baja directo siempre.

    Raises:
        RuntimeError si todos los intentos fallan o se estancó.
    """
    proxies = _get_proxy_list()
    # Estrategias en orden:
    # 1. direct-yt    = direct + headers de YouTube (Referer/Origin/Sec-Fetch).
    #                   googlevideo a veces 403ea sin estos headers.
    # 2. direct       = direct con UA simple (fallback).
    # 3. proxy[0..N]  = cada proxy de la lista, uno a uno.
    #                   Si un proxy está rate-limiteado por googlevideo para
    #                   esa URL específica, el siguiente puede tener throughput
    #                   normal (distinta IP = distinta tupla de rate-limit).
    strategies: list[tuple[str, str | None, bool]] = []
    if try_direct_first or not proxies:
        strategies.append(("direct-yt", None, True))
        strategies.append(("direct", None, False))
    for i, p in enumerate(proxies):
        # Label "proxy[1/20]", "proxy[2/20]", etc. para que se vea en logs
        name = f"proxy[{i+1}/{len(proxies)}]" if len(proxies) > 1 else "proxy"
        strategies.append((name, p, False))
    if not strategies:
        strategies.append(("direct-yt", None, True))

    plain_headers = {
        "User-Agent": _DEFAULT_UA,
        "Accept-Encoding": "identity",
    }
    yt_headers = {
        **plain_headers,
        # Pretendemos ser un browser cargando media desde youtube.com
        "Origin": "https://www.youtube.com",
        "Referer": "https://www.youtube.com/",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Dest": "audio",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        # Range header le ayuda a googlevideo a entender que queremos descarga
        # de media (algunas rutas del CDN responden mejor con esto)
        "Range": "bytes=0-",
    }

    last_err: Exception | None = None
    for strategy_name, proxy_url, use_yt_headers in strategies:
        print(f"   🎯 {label}: intentando vía '{strategy_name}'...")
        try:
            if proxy_url:
                opener = build_opener(ProxyHandler({"http": proxy_url, "https": proxy_url}))
                _open = lambda r, t=60: opener.open(r, timeout=t)
            else:
                _open = lambda r, t=60: urlopen(r, timeout=t)

            headers = yt_headers if use_yt_headers else plain_headers

            # HEAD: NO mandamos Range en HEAD (algunos servidores lo rechazan).
            head_hdrs = {k: v for k, v in headers.items() if k.lower() != "range"}
            head_req = Request(url, method="HEAD", headers=head_hdrs)
            with _open(head_req, 30) as r:
                total = int(r.headers.get("Content-Length", 0))
            if total <= 0:
                raise RuntimeError("Content-Length inválido o no informado")
            print(f"   📦 {label} [{strategy_name}]: {total // (1<<20)}MB esperados")

            # Descarga secuencial — pedimos el archivo entero con un solo GET
            get_req = Request(url, headers=headers)
            t0 = time.time()
            last_log = t0
            last_byte_time = t0
            received = 0

            # Fail-fast checkpoints: si a los 30s tenemos <5MB, este proxy
            # va a tardar demasiado — abandonar y probar el siguiente.
            # 5MB en 30s = 167 KB/s mínimo. Por debajo de eso (caso actual:
            # 30 KB/s), 82MB tardarían 45 min → mejor abandonar acá.
            EARLY_CHECKPOINT_SEC = 30
            EARLY_MIN_BYTES = 5 * (1 << 20)

            with _open(get_req, 60) as r, open(out_path, "wb") as f:
                while True:
                    elapsed = time.time() - t0

                    # Deadline global
                    if elapsed > overall_timeout_sec:
                        raise TimeoutError(
                            f"deadline global {overall_timeout_sec}s "
                            f"({received // (1<<20)}MB recibidos)"
                        )

                    # Fail-fast: muy poca data tras 30s = proxy throttled
                    if elapsed > EARLY_CHECKPOINT_SEC and received < EARLY_MIN_BYTES:
                        raise TimeoutError(
                            f"fail-fast: solo {received // (1<<20)}MB en "
                            f"{elapsed:.0f}s — proxy throttled, probando siguiente"
                        )

                    chunk = r.read(1 << 16)  # 64KB
                    now = time.time()
                    if not chunk:
                        break
                    f.write(chunk)
                    received += len(chunk)
                    last_byte_time = now

                    if now - last_log >= log_interval_sec:
                        pct = (received / total * 100) if total else 0
                        speed = received / max(elapsed, 0.01) / (1 << 20)
                        print(
                            f"   📊 {label} [{strategy_name}]: "
                            f"{received // (1<<20)}MB / {total // (1<<20)}MB "
                            f"({pct:.0f}%) — {speed:.2f}MB/s "
                            f"en {elapsed:.0f}s"
                        )
                        last_log = now

            elapsed = time.time() - t0
            mb = received / (1 << 20)
            speed = mb / max(elapsed, 0.01)
            print(
                f"   ✅ {label} [{strategy_name}]: {mb:.1f}MB en {elapsed:.1f}s ({speed:.2f}MB/s)"
            )
            if received < total * 0.99:
                # Recibimos significativamente menos de lo esperado — el servidor
                # cortó la conexión.
                raise RuntimeError(
                    f"truncado: {received}/{total} bytes ({received/total*100:.0f}%)"
                )
            return  # éxito — no probamos las siguientes estrategias

        except Exception as e:
            last_err = e
            err_msg = str(e)[:150]
            print(f"   ❌ {label} [{strategy_name}] falló: {err_msg}")
            # Limpio out_path para no dejar bytes parciales basura
            try:
                if out_path.exists():
                    out_path.unlink()
            except Exception:
                pass

    raise RuntimeError(f"todas las estrategias fallaron para {label}: {last_err}")


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

    vid, aud = _pick_rapidapi_formats(data, max_height)
    vid_quality = int(re.match(r"(\d+)", vid.get("qualityLabel", "0") or "0").group(1) or 0)
    aud_bitrate = aud.get("bitrate", 0)

    print(f"   video: {vid_quality}p | audio: {aud_bitrate // 1000}kbps AAC")

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
    """Download via yt-dlp con cascada de formatos muy permisiva.

    En 2025 con cookies + player_client cascada, los formatos disponibles
    varían mucho entre tv/ios/web. El selector empieza muy específico
    (720p adaptive avc1) y va relajando hasta llegar a 'worst' como
    último recurso. Cualquier MP4 utilizable sirve.
    """
    # Cascada de formatos: del ideal al "lo que sea"
    fmt_cascade = (
        # Adaptive 720p AVC + AAC (mejor calidad+compatibilidad)
        'bestvideo[height<=720][vcodec^=avc1]+bestaudio[acodec^=mp4a]/'
        # Adaptive 720p cualquier codec
        'bestvideo[height<=720]+bestaudio/'
        # Adaptive 480p / 360p
        'bestvideo[height<=480]+bestaudio/'
        'bestvideo[height<=360]+bestaudio/'
        # Progressive (single file con audio embebido) 720p
        'best[height<=720][ext=mp4]/'
        'best[height<=720]/'
        # Progressive lower res
        'best[height<=480]/'
        'best[height<=360]/'
        # Cualquier adaptive
        'bestvideo+bestaudio/'
        # Cualquier progressive
        'best/'
        # Lo que sea (incluso 144p)
        'worst'
    )

    ydl_opts = _build_ydl_opts({
        'format': fmt_cascade,
        'outtmpl': str(DOWNLOADS_DIR / '%(id)s_video.%(ext)s'),
        'ffmpeg_location': FFMPEG_LOCATION,
        'merge_output_format': 'mp4',
        'quiet': False,
        'no_warnings': False,
        'nocheckcertificate': True,
        # Verbose para ver QUÉ formatos están disponibles cuando algo va mal.
        # En producción esto agrega log noise pero es invaluable para diagnostico.
        'verbose': False,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
        }
    })

    # Si yt-dlp falla, intentamos extraer info SIN download para ver qué tiene
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            if not video_id:
                video_id = info['id']
    except yt_dlp.utils.DownloadError as e:
        # Si fue "Requested format is not available", listamos los formatos
        # disponibles para diagnosticar (info_dict mode, sin descargar)
        err_str = str(e)
        if "format is not available" in err_str.lower() or "no video format" in err_str.lower():
            try:
                probe_opts = _build_ydl_opts({
                    'listformats': False,
                    'quiet': True,
                    'no_warnings': True,
                })
                with yt_dlp.YoutubeDL(probe_opts) as probe:
                    info = probe.extract_info(video_url, download=False)
                    formats = info.get('formats', [])
                    print(f"📋 yt-dlp probe: {len(formats)} formatos disponibles")
                    # Mostrar los primeros 5 para diagnóstico
                    for f in formats[:10]:
                        print(f"   • id={f.get('format_id')} "
                              f"ext={f.get('ext')} "
                              f"res={f.get('resolution', f.get('format_note', '?'))} "
                              f"vcodec={f.get('vcodec', 'none')[:10]} "
                              f"acodec={f.get('acodec', 'none')[:10]} "
                              f"filesize={f.get('filesize') or '?'}")
                    if len(formats) > 10:
                        print(f"   ... +{len(formats) - 10} más")
            except Exception as probe_err:
                print(f"⚠️ También falló el probe: {str(probe_err)[:120]}")
        raise

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


def download_clip_ytdlp(
    youtube_url: str,
    start_sec: float,
    end_sec: float,
    output_path: str,
) -> str:
    """
    Descarga SOLO el segmento necesario para un clip usando yt-dlp con proxy.

    yt-dlp con download_ranges descarga solo los segmentos DASH que cubren
    el rango pedido (~50MB para 35s) usando Python requests internamente
    (el proxy funciona aquí, a diferencia de FFmpeg que necesita HTTPS CONNECT).

    Requiere WEBSHARE_PROXY_URL configurado para bypasear el 403 de YouTube
    en IPs de datacenter.

    Returns:
        Path al MP4 descargado (clip listo para procesar con generate_clip)
    """
    from yt_dlp.utils import download_range_func

    # output_path puede terminar en .mp4 pero yt-dlp agrega su propia extensión
    # Usar un stem sin extensión y dejar que yt-dlp la maneje
    out_stem = str(Path(output_path).with_suffix(''))

    ydl_opts = _build_ydl_opts({
        # Cascada de formatos: preferimos AVC1+m4a (compatible con todo),
        # luego cualquier 720p, luego cualquier 480p, y como último recurso
        # el mejor video + mejor audio disponibles (VP9/AV1 también).
        'format': (
            'bestvideo[height<=720][vcodec^=avc1]+bestaudio[acodec^=mp4a]/'
            'bestvideo[height<=720]+bestaudio/'
            'bestvideo[height<=480]+bestaudio/'
            'best[height<=720]/'
            'bestvideo+bestaudio/'
            'best'
        ),
        'download_ranges': download_range_func(None, [(start_sec, end_sec)]),
        'force_keyframes_at_cuts': True,
        'outtmpl': out_stem + '.%(ext)s',
        'ffmpeg_location': FFMPEG_LOCATION,
        'merge_output_format': 'mp4',
        'quiet': False,
        'no_warnings': False,
        'nocheckcertificate': True,
    })

    DOWNLOADS_DIR.mkdir(exist_ok=True)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([youtube_url])

    # Buscar el archivo generado
    result_path = Path(out_stem + '.mp4')
    if result_path.exists():
        size_mb = result_path.stat().st_size / (1 << 20)
        print(f"✅ yt-dlp segmento: {result_path.name} ({size_mb:.1f}MB, {start_sec:.0f}s-{end_sec:.0f}s)")
        return str(result_path)

    # Buscar cualquier extensión
    for ext in ['mp4', 'mkv', 'webm']:
        p = Path(out_stem + f'.{ext}')
        if p.exists():
            return str(p)

    raise FileNotFoundError(f"yt-dlp no generó archivo en: {out_stem}.*")


def _get_stream_content_length(url: str, label: str = "stream") -> int:
    """Obtiene Content-Length de googlevideo (HEAD + GET probe + proxies)."""
    return _probe_stream_size(url, label=label)


def extract_segment_copy(
    source_path: str,
    start_sec: float,
    end_sec: float,
    output_path: str,
) -> str:
    """Extrae [start_sec, end_sec] del video fuente con ffmpeg -c copy (rápido)."""
    ffmpeg_bin = "ffmpeg"
    if FFMPEG_LOCATION:
        p = Path(FFMPEG_LOCATION)
        if p.is_file():
            ffmpeg_bin = str(p)
        elif p.is_dir():
            candidate = p / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            ffmpeg_bin = str(candidate if candidate.exists() else p / "ffmpeg")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_bin, "-y", "-loglevel", "error",
        "-ss", str(start_sec), "-to", str(end_sec),
        "-i", source_path,
        "-c", "copy",
        "-movflags", "+faststart",
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg extract falló: {result.stderr[-300:]}")
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError("ffmpeg extract no generó archivo válido")
    return str(out)


def download_video_for_clips(
    video_url: str,
    audio_url: str,
    max_end_sec: float,
    video_duration: float,
    video_id: str,
    buffer_sec: float = 15.0,
    *,
    resolve_proxy: str | None = None,
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

    if resolve_proxy:
        vid_total, vid_proxy, vid_via = _probe_stream_info_for_proxy(
            video_url, resolve_proxy, label="video",
        )
        aud_total, aud_proxy, aud_via = _probe_stream_info_for_proxy(
            audio_url, resolve_proxy, label="audio",
        )
    else:
        vid_total, vid_proxy, vid_via = _probe_stream_info(video_url, label="video")
        aud_total, aud_proxy, aud_via = _probe_stream_info(audio_url, label="audio")

    if vid_total <= 0 or aud_total <= 0:
        raise RuntimeError(f"Content-Length inválido: video={vid_total}, audio={aud_total}")

    vid_end_byte = int(ratio * vid_total)
    # ⚠️ Audio: descargar SIEMPRE completo. El m4a tiene el moov atom al final,
    # si lo truncamos por byte-range el container queda inválido y ffmpeg no
    # puede leer el header. Es solo unos pocos MB de más, vale la pena.
    aud_end_byte = aud_total

    vid_path = DOWNLOADS_DIR / f"{video_id}_pvid.mp4"
    aud_path = DOWNLOADS_DIR / f"{video_id}_paud.m4a"

    t0 = time.time()
    audio_chunks = min(8, max(4, aud_total // (2 * (1 << 20)) or 4))
    use_sequential = _is_googlevideo_url(video_url) or _is_googlevideo_url(audio_url)
    with ThreadPoolExecutor(max_workers=2) as ex:
        if use_sequential:
            fv = ex.submit(
                _download_bytes_sequential, video_url, vid_path,
                vid_end_byte, vid_total,
                label="video", sticky_proxy=vid_proxy, sticky_via=vid_via,
            )
            fa = ex.submit(
                _download_bytes_sequential, audio_url, aud_path,
                aud_end_byte, aud_total,
                label="audio", sticky_proxy=aud_proxy, sticky_via=aud_via,
            )
        else:
            fv = ex.submit(
                _parallel_download, video_url, vid_path, 8, "video",
                vid_end_byte, vid_total, vid_proxy, vid_via,
            )
            fa = ex.submit(
                _parallel_download, audio_url, aud_path, audio_chunks, "audio",
                aud_end_byte, aud_total, aud_proxy, aud_via,
            )
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
        return _probe_stream_size(url, label="stream-seg")

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

        # Same 8-min watchdog as _parallel_download (see note there).
        RANGE_DOWNLOAD_DEADLINE_SEC = 8 * 60
        with ThreadPoolExecutor(max_workers=min(len(ranges), 8)) as ex:
            futures = {ex.submit(fetch, i, bs, be): i for i, bs, be in ranges}
            try:
                for fut in as_completed(futures, timeout=RANGE_DOWNLOAD_DEADLINE_SEC):
                    fut.result()
            except TimeoutError:
                done_count = sum(1 for f in futures if f.done())
                for f in futures:
                    if not f.done():
                        f.cancel()
                raise RuntimeError(
                    f"{label}: range download timed out after "
                    f"{RANGE_DOWNLOAD_DEADLINE_SEC}s ({done_count}/{len(futures)} chunks)"
                )

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


def download_clip_via_stream_urls(
    youtube_url: str,
    start_sec: float,
    end_sec: float,
    video_duration: float,
    video_id: str,
    *,
    temp_id: str | None = None,
    force_rapidapi: bool = False,
) -> str:
    """
    Descarga un clip [start_sec, end_sec] vía stream URLs (RapidAPI/yt-dlp),
    partial download hasta end_sec y corte ffmpeg. Bypassa DRM de yt-dlp.
    """
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    tid = temp_id or f"{video_id}_pc{int(start_sec)}"
    stream_urls = get_stream_urls(youtube_url, video_id, force_rapidapi=force_rapidapi)

    effective_duration = max(float(video_duration), end_sec + 30)
    print(f"   📥 RapidAPI/stream partial per-clip ({int(start_sec)}-{int(end_sec)}s)...")
    pvid, paud = download_video_for_clips(
        video_url=stream_urls["video_url"],
        audio_url=stream_urls["audio_url"],
        max_end_sec=end_sec,
        video_duration=effective_duration,
        video_id=tid,
        resolve_proxy=stream_urls.get("resolve_proxy"),
    )

    ffmpeg_bin = "ffmpeg"
    if FFMPEG_LOCATION:
        p = Path(FFMPEG_LOCATION)
        if p.is_file():
            ffmpeg_bin = str(p)
        elif p.is_dir():
            candidate = p / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            ffmpeg_bin = str(candidate if candidate.exists() else p / "ffmpeg")

    muxed = DOWNLOADS_DIR / f"{tid}_muxed.mp4"
    mux_r = subprocess.run(
        [ffmpeg_bin, "-y", "-loglevel", "warning",
         "-i", pvid, "-i", paud, "-c", "copy",
         "-movflags", "+faststart", str(muxed)],
        capture_output=True, text=True, timeout=300,
    )
    for pp in (pvid, paud):
        try:
            Path(pp).unlink(missing_ok=True)
        except Exception:
            pass
    if mux_r.returncode != 0:
        raise RuntimeError(f"mux per-clip falló: {mux_r.stderr[-300:]}")

    seg = DOWNLOADS_DIR / f"{tid}_seg.mp4"
    cut_r = subprocess.run(
        [ffmpeg_bin, "-y", "-loglevel", "warning",
         "-ss", str(start_sec), "-to", str(end_sec),
         "-i", str(muxed), "-c", "copy",
         "-avoid_negative_ts", "make_zero",
         "-movflags", "+faststart", str(seg)],
        capture_output=True, text=True, timeout=180,
    )
    try:
        muxed.unlink(missing_ok=True)
    except Exception:
        pass
    if cut_r.returncode != 0:
        raise RuntimeError(f"cut per-clip falló: {cut_r.stderr[-300:]}")

    size_mb = seg.stat().st_size / (1 << 20)
    print(f"   ✅ Stream partial per-clip OK ({size_mb:.1f}MB)")
    return str(seg)


def get_stream_urls_rapidapi(video_url: str, video_id: str = None, max_height: int = 720) -> dict:
    """
    Obtiene URLs de stream vía RapidAPI SIN descargar.

    Las URLs de googlevideo quedan atadas a la IP que llama a yt-api.
    Por eso resolvemos vía proxy residencial (misma IP que usará la descarga).
    """
    api_key = os.getenv("RAPIDAPI_KEY")
    if not api_key:
        raise RuntimeError("RAPIDAPI_KEY no está seteada en el entorno")

    if not video_id:
        video_id = _extract_video_id(video_url)

    import json
    meta_url = f"https://yt-api.p.rapidapi.com/dl?id={video_id}"
    req = Request(meta_url, headers={
        "x-rapidapi-key": api_key,
        "x-rapidapi-host": "yt-api.p.rapidapi.com",
    })

    proxies = _get_proxy_list()
    proxies_to_try: list[str | None] = proxies if proxies else [None]
    last_err: Exception | None = None

    for i, proxy in enumerate(proxies_to_try):
        if proxy:
            name = f"proxy[{i + 1}/{len(proxies_to_try)}]" if len(proxies_to_try) > 1 else "proxy"
        else:
            name = "direct"
        try:
            print(f"🔌 RapidAPI vía {name}: obteniendo URLs de stream para {video_id}...")
            if proxy:
                opener = build_opener(ProxyHandler({"http": proxy, "https": proxy}))
                with opener.open(req, timeout=45) as r:
                    data = json.loads(r.read().decode("utf-8"))
            else:
                with urlopen(req, timeout=45) as r:
                    data = json.loads(r.read().decode("utf-8"))

            if data.get("status") != "OK":
                raise RuntimeError(
                    f"YT-API devolvió status={data.get('status')}: {data.get('reason', '')}"
                )

            vid, aud = _pick_rapidapi_formats(data, max_height)
            vid_quality = int(re.match(r"(\d+)", vid.get("qualityLabel", "0") or "0").group(1) or 0)
            aud_bitrate = aud.get("bitrate", 0)

            print(f"   ✅ URLs obtenidas: video={vid_quality}p | audio={aud_bitrate // 1000}kbps")
            return {
                "video_url": vid["url"],
                "audio_url": aud["url"],
                "video_id": video_id,
                "resolve_proxy": proxy,
            }
        except Exception as e:
            last_err = e
            print(f"   ⚠️ RapidAPI vía {name} falló: {str(e)[:80]}")
            continue

    raise RuntimeError(f"RapidAPI no pudo resolver streams para {video_id}: {last_err}")


def _get_stream_urls_ytdlp(video_url: str, video_id: str = None) -> dict:
    """
    Extrae URLs de stream vía yt-dlp sin descargar (download=False).
    Rota player_client y proxy hasta encontrar una combinación que funcione.
    """
    client_cascades = [
        ['tv', 'ios', 'web'],
        ['android', 'web'],
        ['mweb', 'web'],
        ['web'],
    ]
    proxies = _get_proxy_list()
    proxies_to_try = proxies if proxies else [None]

    last_err: Exception | None = None
    for proxy in proxies_to_try:
        for clients in client_cascades:
            try:
                ydl_opts = _build_ydl_opts({
                    'format': (
                        'bestvideo[height<=720][vcodec^=avc1]+bestaudio[acodec^=mp4a]/'
                        'bestvideo[height<=720]+bestaudio'
                    ),
                    'quiet': True,
                    'no_warnings': True,
                    'nocheckcertificate': True,
                }, player_clients=clients, proxy_url=proxy)
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(video_url, download=False)
                    if not video_id:
                        video_id = info['id']

                    req_formats = info.get('requested_formats', [])
                    if len(req_formats) >= 2:
                        v_fmt = next((f for f in req_formats if f.get('vcodec', 'none') != 'none'), None)
                        a_fmt = next(
                            (f for f in req_formats
                             if f.get('acodec', 'none') != 'none' and f.get('vcodec', 'none') == 'none'),
                            None,
                        )
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
                        "resolve_proxy": proxy,
                    }
            except Exception as e:
                last_err = e
                if is_ytdlp_bot_error(e) or is_ytdlp_drm_error(e):
                    continue
                raise

    if is_ytdlp_bot_error(last_err) and not has_youtube_cookies():
        raise RuntimeError(
            "YouTube pide cookies (anti-bot). Configura YOUTUBE_COOKIES en Render — "
            "exporta cookies.txt desde tu browser logueado en YouTube y codifícalo en base64."
        ) from last_err
    raise last_err or RuntimeError("yt-dlp no pudo resolver stream URLs")


def get_stream_urls(video_url: str, video_id: str = None, *, force_rapidapi: bool = False) -> dict:
    """
    Obtiene URLs de stream de video+audio sin descargar el archivo completo.

    Con proxies residenciales: yt-dlp primero (misma IP resuelve+descarga).
    RapidAPI como fallback cuando yt-dlp falla o force_rapidapi=True.

    Retorna {"video_url": str, "audio_url": str, "video_id": str}.
    """
    env = os.getenv("ENVIRONMENT", "development").lower()
    rapidapi_key = os.getenv("RAPIDAPI_KEY")
    has_proxies = bool(_get_proxy_list())
    use_rapidapi_first = (
        force_rapidapi
        or (
            not has_proxies
            and (
                os.getenv("USE_RAPIDAPI_DOWNLOAD", "").lower() in ("1", "true", "yes")
                or (env in ("production", "prod") and bool(rapidapi_key))
            )
        )
    )

    if not force_rapidapi and has_proxies:
        try:
            print("ℹ️ Resolviendo stream URLs vía yt-dlp + proxy (misma IP para descarga)")
            return _get_stream_urls_ytdlp(video_url, video_id)
        except Exception as e:
            if is_ytdlp_drm_error(e):
                print(f"⚠️ yt-dlp stream URLs bloqueado (DRM/PO Token) — fallback RapidAPI")
            else:
                print(f"⚠️ yt-dlp stream URLs falló ({type(e).__name__}: {str(e)[:120]}) — fallback RapidAPI")

    if use_rapidapi_first or force_rapidapi:
        if not rapidapi_key:
            raise RuntimeError(
                "RAPIDAPI_KEY no configurada — requerida en producción para bypass DRM"
            )
        print("ℹ️ Usando RapidAPI para stream URLs")
        return get_stream_urls_rapidapi(video_url, video_id)

    try:
        return _get_stream_urls_ytdlp(video_url, video_id)
    except Exception as e:
        if is_ytdlp_drm_error(e):
            print(f"⚠️ yt-dlp bloqueado (DRM/PO Token) — saltando a RapidAPI")
        else:
            print(f"⚠️ yt-dlp stream URLs falló ({type(e).__name__}: {str(e)[:120]}) — fallback a RapidAPI")
        if not rapidapi_key:
            raise RuntimeError(
                "RAPIDAPI_KEY no configurada — no hay fallback cuando yt-dlp falla"
            ) from e
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
        patterns = [
            f"{video_id}_audio.*",
            f"{video_id}_video.*",
            f"{video_id}_pvid.*",
            f"{video_id}_paud.*",
            f"{video_id}_muxed.*",
            f"{video_id}_seg_*",
            f"{video_id}_clip_*",
            f"{video_id}_video_only.*",
            f"{video_id}_audio_only.*",
        ]
        for pattern in patterns:
            for file in DOWNLOADS_DIR.glob(pattern):
                file.unlink()
                print(f"🧹 Cleaned up: {file}")
        # Chunk temp dirs from parallel downloads
        for tmp_dir in DOWNLOADS_DIR.glob(f".{video_id}_*_chunks"):
            if tmp_dir.is_dir():
                shutil.rmtree(tmp_dir, ignore_errors=True)
                print(f"🧹 Cleaned up: {tmp_dir}")
    except Exception as e:
        print(f"⚠️ Cleanup error: {e}")
