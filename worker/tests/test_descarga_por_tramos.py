"""
W16: el audio se pide por tramos Range y nunca queda un parcial en disco.

googlevideo estrangula a velocidad de reproducción cuando se le pide el
archivo entero en un solo Range (medido desde el VPS con el mismo proxy:
27 KB/s contra 2236 KB/s pidiendo tramos de 4 MB). Y un parcial de una
descarga abortada se colaba después como "audio completo" (job 1ea90b15:
transcribió 64 s de un video de 111 min).
"""
import os
import sys
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


class _RangeServer:
    """Sirve `total` bytes respetando el Range que se le pide, y los anota."""

    def __init__(self, total: int, fail_on_range: int | None = None):
        self.total = total
        self.rangos: list[tuple[int, int]] = []
        self.fail_on_range = fail_on_range

    def open(self, req, timeout=None):
        ini, fin = (int(x) for x in req.headers["Range"].split("=")[1].split("-"))
        self.rangos.append((ini, fin))
        if self.fail_on_range is not None and len(self.rangos) > self.fail_on_range:
            raise OSError("se cortó la conexión")
        datos = b"\0" * (min(fin, self.total - 1) - ini + 1)

        class _Resp:
            def __init__(self, d):
                self._d = d
                self._leido = False

            def read(self, n=-1):
                if self._leido:
                    return b""
                self._leido = True
                return self._d

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _Resp(datos)


@pytest.fixture
def _sin_espera(monkeypatch):
    monkeypatch.setattr(downloader, "AUDIO_RANGE_CHUNK_BYTES", 4 << 20)


class TestDescargaPorTramos:
    def test_pide_el_archivo_en_tramos_y_no_de_una(self, tmp_path, monkeypatch, _sin_espera):
        total = 10 << 20  # 10 MB con tramos de 4 MB → 3 requests
        server = _RangeServer(total)
        monkeypatch.setattr(downloader, "build_opener", lambda *_: server)
        out = tmp_path / "audio.m4a"

        downloader._download_bytes_sequential(
            "https://r1.googlevideo.com/videoplayback?x=1", out,
            known_total=total, sticky_proxy="http://p1", label="audio completo",
        )

        assert out.stat().st_size == total
        assert server.rangos == [(0, 4194303), (4194304, 8388607), (8388608, 10485759)]

    def test_una_falla_a_mitad_no_deja_el_parcial_en_disco(self, tmp_path, monkeypatch, _sin_espera):
        total = 10 << 20
        server = _RangeServer(total, fail_on_range=1)  # el 2º tramo se corta
        monkeypatch.setattr(downloader, "build_opener", lambda *_: server)
        out = tmp_path / "audio.m4a"

        with pytest.raises(OSError):
            downloader._download_bytes_sequential(
                "https://r1.googlevideo.com/videoplayback?x=1", out,
                known_total=total, sticky_proxy="http://p1", label="audio completo",
            )

        assert not out.exists()  # antes quedaba un parcial que pasaba por completo


class TestFindLocalFullMediaDescartaParciales:
    def test_archivo_corto_se_descarta_cuando_se_sabe_la_duracion(self, tmp_path, monkeypatch):
        monkeypatch.setattr(downloader, "DOWNLOADS_DIR", tmp_path)
        parcial = tmp_path / "vid123_audio_only.m4a"
        parcial.write_bytes(b"\0" * 1024)
        monkeypatch.setattr(
            "services.audio_utils.probe_audio_duration_sec", lambda p: 64.0,
        )

        # video de 111 min, archivo de 64 s → no es el audio completo
        assert downloader.find_local_full_media("vid123", expected_duration_sec=6660) is None
        assert not parcial.exists()  # además se borra, para no reencontrarlo

    def test_archivo_completo_se_acepta(self, tmp_path, monkeypatch):
        monkeypatch.setattr(downloader, "DOWNLOADS_DIR", tmp_path)
        completo = tmp_path / "vid123_audio_only.m4a"
        completo.write_bytes(b"\0" * 2048)
        monkeypatch.setattr(
            "services.audio_utils.probe_audio_duration_sec", lambda p: 6650.0,
        )

        assert downloader.find_local_full_media("vid123", expected_duration_sec=6660) == str(completo)

    def test_sin_duracion_esperada_se_comporta_como_antes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(downloader, "DOWNLOADS_DIR", tmp_path)
        algo = tmp_path / "vid123_audio_only.m4a"
        algo.write_bytes(b"\0" * 10)
        assert downloader.find_local_full_media("vid123") == str(algo)
