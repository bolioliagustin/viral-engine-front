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
    
    # Cascada de clients para 2025-2026:
    #   - tv: cliente Smart TV, no requiere PO Token, devuelve adaptive formats hasta 1080p
    #   - ios: cliente iOS oficial, también bypass de PO Token
    #   - web: legacy fallback (en 2025 muchas veces filtra todos los formats sin PO Token,
    #          que es exactamente el bug "Requested format is not available" que veíamos)
    # Probamos tv primero porque tiene mejores formats h264 para nuestro ffmpeg pipeline.
    base_opts['extractor_args'] = {'youtube': {'player_client': ['tv', 'ios', 'web']}}

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

    PROXY ROTATION (Fase 1.6 v4):
    Si hay múltiples proxies configurados (WEBSHARE_PROXY_FILE / WEBSHARE_PROXY_LIST),
    cada chunk se enruta por un proxy distinto vía round-robin. Esto:
      - Multiplica el throughput total (cada proxy contribuye su bandwidth)
      - Da fault tolerance: si un proxy IP está rate-limiteado por googlevideo
        para una URL específica, los otros chunks pueden completar
      - En el retry, cada chunk prueba con el SIGUIENTE proxy de la lista
        (no insiste con el mismo que falló)

    end_byte: si se pasa, descarga solo hasta ese byte (descarga parcial).
              Si None, descarga el archivo completo.
    """
    proxies = _get_proxy_list()
    n_proxies = len(proxies) if proxies else 0

    def _make_opener(proxy_url: str | None):
        if proxy_url:
            return build_opener(ProxyHandler({"http": proxy_url, "https": proxy_url}))
        return None

    # Pre-build openers (una vez para reusar)
    openers = [_make_opener(p) for p in proxies] if proxies else [None]

    def _open_with(opener, req, timeout=30):
        if opener:
            return opener.open(req, timeout=timeout)
        return urlopen(req, timeout=timeout)

    # HEAD vía primer proxy (o directo) para conocer tamaño
    if n_proxies > 0:
        print(f"   🌐 {label}: pool de {n_proxies} proxies disponibles")
    head_opener = openers[0]
    req = Request(url, method="HEAD", headers={"User-Agent": _DEFAULT_UA})
    with _open_with(head_opener, req, 30) as r:
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
    proxy_label = (
        f" cada uno via proxy distinto (round-robin sobre {n_proxies})"
        if n_proxies > 1 else ""
    )
    print(f"   ⬇️ {label}: {size_label} en {len(ranges)} chunks paralelos{proxy_label}")
    t0 = time.time()

    def fetch(i: int, s: int, e: int) -> Path:
        tmp = tmp_dir / f"chunk_{i:04d}"
        last_err = None
        for attempt in range(3):
            # Round-robin: chunk i en el intento N usa el proxy (i + N) mod n_openers.
            # Así si chunk 0 falla con proxy 0, reintenta con proxy 1, después 2.
            # Y dos chunks distintos arrancan por proxies distintos en el primer intento.
            opener_idx = (i + attempt) % len(openers)
            opener = openers[opener_idx]
            try:
                rq = Request(url, headers={
                    "User-Agent": _DEFAULT_UA,
                    "Range": f"bytes={s}-{e}",
                })
                with _open_with(opener, rq, 180) as r, open(tmp, "wb") as f:
                    shutil.copyfileobj(r, f, 1 << 20)
                if tmp.stat().st_size != (e - s + 1):
                    raise IOError(f"chunk {i} size mismatch: got {tmp.stat().st_size}, expected {e-s+1}")
                return tmp
            except Exception as ex:
                last_err = ex
                # Solo logueamos en retries (no spam en éxito al primer intento)
                if attempt > 0:
                    print(f"   ↻ {label} chunk {i}: retry {attempt+1}/3 (proxy idx {opener_idx}): {str(ex)[:80]}")
                time.sleep(1 + attempt)
        raise RuntimeError(f"chunk {i} failed after 3 attempts: {last_err}")

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
    # ⚠️ Audio: descargar SIEMPRE completo. El m4a tiene el moov atom al final,
    # si lo truncamos por byte-range el container queda inválido y ffmpeg no
    # puede leer el header. Es solo unos pocos MB de más, vale la pena.
    aud_end_byte = aud_total

    vid_path = DOWNLOADS_DIR / f"{video_id}_pvid.mp4"
    aud_path = DOWNLOADS_DIR / f"{video_id}_paud.m4a"

    t0 = time.time()
    # Estrategia híbrida (Fase 1.6 v2):
    #   - VIDEO: 8 chunks paralelos vía proxy. Probado: ~40-80 MB/s, no se cuelga.
    #     YouTube exige proxy para evitar IP-ban del watch page, pero una vez que
    #     tenemos URL firmada el video baja rápido.
    #   - AUDIO: descarga secuencial con _download_with_progress, intentando
    #     PRIMERO sin proxy (googlevideo CDN de audio raramente IP-banea).
    #     Fallback a proxy si direct falla.
    #     Esto debería resolver el caso donde el audio se cuelga vía proxy.
    with ThreadPoolExecutor(max_workers=2) as ex:
        fv = ex.submit(_parallel_download, video_url, vid_path, 8, "video", vid_end_byte)
        fa = ex.submit(
            _download_with_progress, audio_url, aud_path, "audio",
            try_direct_first=True,
            overall_timeout_sec=360,
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
