"""
W14 (docs/PLAN_CALIDAD.md): la descarga de audio abandona un proxy lento en
vez de esperar media hora (medido: 52 KB/s en el job real 4c6e4410), y
`download_audio_only` reintenta re-resolviendo con OTRO proxy — sin red real,
con un socket simulado.
"""
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "development")

import services.downloader as downloader  # noqa: E402


class _FakeSocket:
    """Simula `resp.read(n)` a un caudal fijo (bytes/seg) — sin red real."""

    def __init__(self, total_bytes: int, bytes_per_sec: float, block: int = 1 << 16):
        self.total = total_bytes
        self.sent = 0
        self.bps = bytes_per_sec
        self.block = block

    def read(self, n: int) -> bytes:
        if self.sent >= self.total:
            return b""
        take = min(n, self.block, self.total - self.sent)
        time.sleep(take / self.bps)
        self.sent += take
        return b"\0" * take


@pytest.fixture(autouse=True)
def _fast_probe_window(monkeypatch):
    """Ventana de prueba chica: no esperar los 25 s reales en cada test."""
    monkeypatch.setattr(downloader, "AUDIO_SPEED_PROBE_SEC", 0.3)
    monkeypatch.setattr(downloader, "AUDIO_MIN_SPEED_KBPS", 200.0)


class TestReadWithSpeedGuard:
    def test_caudal_bajo_aborta_con_slow_proxy_error(self, tmp_path):
        # 50 KB/s (bien por debajo del piso de 200 KB/s) sobre un archivo
        # grande: a los 0.3s de prueba no se llega ni cerca del 80%.
        sock = _FakeSocket(total_bytes=5 * (1 << 20), bytes_per_sec=50 * 1024)
        out = tmp_path / "audio.m4a"
        t0 = time.time()
        with pytest.raises(downloader.SlowProxyError):
            downloader._read_with_speed_guard(sock, out, want_bytes=sock.total, label="audio completo")
        # Aborta enseguida — no espera los 30 s reales que motivaron W14.
        assert time.time() - t0 < 5.0

    def test_caudal_alto_descarga_completa(self, tmp_path):
        sock = _FakeSocket(total_bytes=1 << 20, bytes_per_sec=5 * (1 << 20))  # 5 MB/s
        out = tmp_path / "audio.m4a"
        downloader._read_with_speed_guard(sock, out, want_bytes=sock.total, label="audio completo")
        assert out.stat().st_size == sock.total

    def test_lento_pero_ya_80_por_ciento_no_aborta(self, tmp_path):
        # Mismo caudal "lento" (50 KB/s) que el primer test, pero sobre un
        # archivo chico: para cuando se cumple la ventana de prueba ya se
        # bajó >80%, así que NO debe abortar — conviene terminar.
        sock = _FakeSocket(total_bytes=18_000, bytes_per_sec=51_200, block=4096)
        out = tmp_path / "audio.m4a"
        downloader._read_with_speed_guard(sock, out, want_bytes=sock.total, label="audio completo")
        assert out.stat().st_size == sock.total


class TestDownloadAudioOnlyReintenta:
    def test_primer_proxy_lento_reintenta_con_otro_y_termina_bien(self, tmp_path, monkeypatch):
        monkeypatch.setattr(downloader, "DOWNLOADS_DIR", tmp_path)
        monkeypatch.setattr(downloader, "find_local_full_media", lambda vid: None)
        monkeypatch.setattr(downloader, "_get_proxy_list", lambda: ["http://proxy1", "http://proxy2"])

        # Estrategias A/C (yt-dlp) fallan siempre — solo queda la B (stream URLs).
        mock_ydl = MagicMock()
        mock_ydl.__enter__.return_value.extract_info.side_effect = RuntimeError("sin formato utilizable")
        monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", lambda opts: mock_ydl)

        resolved_with: list[str | None] = []

        def _fake_get_stream_urls(video_url, video_id=None, *, only_proxy=None, force_rapidapi=False):
            resolved_with.append(only_proxy)
            return {"audio_url": "http://googlevideo.fake/audio", "resolve_proxy": only_proxy}

        monkeypatch.setattr(downloader, "get_stream_urls", _fake_get_stream_urls)

        calls = {"n": 0}

        def _fake_download_bytes_sequential(url, out_path, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise downloader.SlowProxyError("proxy1: 40 KB/s (< 200 KB/s piso)")
            Path(out_path).write_bytes(b"audio-ok")

        monkeypatch.setattr(downloader, "_download_bytes_sequential", _fake_download_bytes_sequential)

        result = downloader.download_audio_only("https://youtu.be/xyz12345678", "xyz12345678")

        assert calls["n"] == 2
        assert resolved_with == ["http://proxy1", "http://proxy2"]
        assert Path(result).name == "xyz12345678_audio_only.m4a"

    def test_todos_lentos_pero_el_mas_rapido_entra_en_presupuesto_hace_intento_paciente(self, tmp_path, monkeypatch):
        """W14-B: 3 intentos lentos con distinto caudal medido; el que midió
        más rápido se reintenta una última vez sin guarda, y esta vez
        termina — sin pasar por la Estrategia C."""
        monkeypatch.setattr(downloader, "DOWNLOADS_DIR", tmp_path)
        monkeypatch.setattr(downloader, "AUDIO_DOWNLOAD_ATTEMPTS", 3)
        monkeypatch.setattr(downloader, "AUDIO_MAX_DOWNLOAD_SEC", 1500.0)
        monkeypatch.setattr(downloader, "find_local_full_media", lambda vid: None)
        monkeypatch.setattr(downloader, "_get_proxy_list", lambda: ["http://p1", "http://p2", "http://p3"])

        mock_ydl = MagicMock()
        mock_ydl.__enter__.return_value.extract_info.side_effect = RuntimeError("sin formato")
        monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", lambda opts: mock_ydl)

        def _fake_get_stream_urls(video_url, video_id=None, *, only_proxy=None, force_rapidapi=False):
            return {"audio_url": "http://googlevideo.fake/audio", "resolve_proxy": only_proxy}

        monkeypatch.setattr(downloader, "get_stream_urls", _fake_get_stream_urls)

        # want_bytes = 45_000 KB; a 45 KB/s (el más rápido, p2) proyecta 1000s (<=1500s).
        want_bytes = 45_000 * 1024
        speeds = {"http://p1": 30.0, "http://p2": 45.0, "http://p3": 20.0}
        calls = []

        def _fake_download(url, out_path, *args, **kwargs):
            calls.append(kwargs)
            proxy = kwargs.get("sticky_proxy")
            if len(calls) <= 3:
                raise downloader.SlowProxyError(
                    f"{proxy}: lento", speed_kbps=speeds[proxy], want_bytes=want_bytes,
                )
            assert kwargs.get("enforce_speed_guard") is False
            assert proxy == "http://p2"  # el más rápido de los tres
            Path(out_path).write_bytes(b"audio-ok")

        monkeypatch.setattr(downloader, "_download_bytes_sequential", _fake_download)

        result = downloader.download_audio_only("https://youtu.be/xyz12345678", "xyz12345678")

        assert len(calls) == 4  # 3 lentos + 1 paciente
        assert Path(result).name == "xyz12345678_audio_only.m4a"
        mock_ydl.__enter__.return_value.extract_info.assert_called_once()  # solo Estrategia A, nunca C

    def test_todos_lentos_y_el_mas_rapido_no_entra_en_presupuesto_no_pasa_a_estrategia_c(self, tmp_path, monkeypatch):
        """W14-B: si ni el proxy más rápido entra en AUDIO_MAX_DOWNLOAD_SEC,
        RuntimeError directo con log claro — no vale la pena insistir con la
        Estrategia C si el cuello de botella es la red del proxy."""
        monkeypatch.setattr(downloader, "DOWNLOADS_DIR", tmp_path)
        monkeypatch.setattr(downloader, "AUDIO_DOWNLOAD_ATTEMPTS", 3)
        monkeypatch.setattr(downloader, "AUDIO_MAX_DOWNLOAD_SEC", 1500.0)
        monkeypatch.setattr(downloader, "find_local_full_media", lambda vid: None)
        monkeypatch.setattr(downloader, "_get_proxy_list", lambda: ["http://p1", "http://p2", "http://p3"])

        mock_ydl = MagicMock()
        mock_ydl.__enter__.return_value.extract_info.side_effect = RuntimeError("sin formato")
        monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", lambda opts: mock_ydl)

        def _fake_get_stream_urls(video_url, video_id=None, *, only_proxy=None, force_rapidapi=False):
            return {"audio_url": "http://googlevideo.fake/audio", "resolve_proxy": only_proxy}

        monkeypatch.setattr(downloader, "get_stream_urls", _fake_get_stream_urls)

        # 50 MB a 30 KB/s (el más rápido) proyecta ~1748s > 1500s de presupuesto.
        want_bytes = 50 * (1 << 20)

        def _always_slow(url, out_path, *args, **kwargs):
            raise downloader.SlowProxyError("lento", speed_kbps=30.0, want_bytes=want_bytes)

        monkeypatch.setattr(downloader, "_download_bytes_sequential", _always_slow)

        with pytest.raises(RuntimeError) as exc_info:
            downloader.download_audio_only("https://youtu.be/xyz12345678", "xyz12345678")

        assert "captions" in str(exc_info.value)
        mock_ydl.__enter__.return_value.extract_info.assert_called_once()  # nunca llegó a la Estrategia C

    def test_todos_los_proxies_lentos_agota_los_intentos_sin_romper_antes_de_tiempo(self, tmp_path, monkeypatch):
        """Si ni re-resolviendo con otro proxy se supera el piso, cae
        prolijamente a la estrategia C (y si esa también falla, el error
        final lista los N intentos — no explota a mitad del loop)."""
        monkeypatch.setattr(downloader, "DOWNLOADS_DIR", tmp_path)
        monkeypatch.setattr(downloader, "AUDIO_DOWNLOAD_ATTEMPTS", 2)
        monkeypatch.setattr(downloader, "find_local_full_media", lambda vid: None)
        monkeypatch.setattr(downloader, "_get_proxy_list", lambda: ["http://proxy1", "http://proxy2"])

        mock_ydl = MagicMock()
        mock_ydl.__enter__.return_value.extract_info.side_effect = RuntimeError("sin formato")
        monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", lambda opts: mock_ydl)

        resolved_with: list[str | None] = []

        def _fake_get_stream_urls(video_url, video_id=None, *, only_proxy=None, force_rapidapi=False):
            resolved_with.append(only_proxy)
            return {"audio_url": "http://googlevideo.fake/audio", "resolve_proxy": only_proxy}

        monkeypatch.setattr(downloader, "get_stream_urls", _fake_get_stream_urls)

        def _always_slow(url, out_path, *args, **kwargs):
            raise downloader.SlowProxyError("lento")

        monkeypatch.setattr(downloader, "_download_bytes_sequential", _always_slow)

        with pytest.raises(RuntimeError) as exc_info:
            downloader.download_audio_only("https://youtu.be/abc12345678", "abc12345678")

        assert resolved_with == ["http://proxy1", "http://proxy2"]
        assert "intento 1" in str(exc_info.value)
        assert "intento 2" in str(exc_info.value)
