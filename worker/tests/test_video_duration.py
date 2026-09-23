"""
W14-B (docs/PLAN_CALIDAD.md): la duración real del video hay que conocerla
ANTES de bajar el audio para poder armar el timeout dinámico del job a
tiempo. `get_video_metadata` la saca de `lengthSeconds` en el HTML público
de la watch page (mismo campo que `backend/src/lib/youtube-duration.js`),
fail-open a 0 si no se puede.
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.yt_transcript import _fetch_length_seconds, get_video_metadata  # noqa: E402


def _fake_response(*, ok=True, text=""):
    r = MagicMock()
    r.ok = ok
    r.text = text
    return r


class TestFetchLengthSeconds:
    def test_html_con_length_seconds_devuelve_la_duracion(self):
        html = '...,"lengthSeconds":"3273","isOwnerViewing":false,...'
        with patch("services.yt_transcript.requests.get", return_value=_fake_response(text=html)):
            assert _fetch_length_seconds("abc12345678") == 3273

    def test_html_sin_length_seconds_devuelve_cero_sin_romper(self):
        with patch("services.yt_transcript.requests.get", return_value=_fake_response(text="<html>nada acá</html>")):
            assert _fetch_length_seconds("abc12345678") == 0

    def test_respuesta_no_ok_devuelve_cero(self):
        with patch("services.yt_transcript.requests.get", return_value=_fake_response(ok=False)):
            assert _fetch_length_seconds("abc12345678") == 0

    def test_excepcion_de_red_devuelve_cero_fail_open(self):
        with patch("services.yt_transcript.requests.get", side_effect=Exception("timeout")):
            assert _fetch_length_seconds("abc12345678") == 0

    def test_ip_propia_bloqueada_prueba_por_proxy(self):
        """VPS: la IP propia recibe la página de bot (sin lengthSeconds); el
        primer proxy del pool recibe la página completa."""
        pagina_bot = '"playabilityStatus":{"status":"LOGIN_REQUIRED"}'
        pagina_ok = '"lengthSeconds":"3261","isOwnerViewing":false'
        llamadas = []

        def _get(url, **kw):
            llamadas.append(kw.get("proxies"))
            return _fake_response(text=pagina_ok if kw.get("proxies") else pagina_bot)

        with patch("services.yt_transcript.requests.get", side_effect=_get), \
             patch("services.downloader._get_proxy_list", return_value=["http://u:p@p1:1", "http://u:p@p2:2"]):
            assert _fetch_length_seconds("abc12345678") == 3261
        assert llamadas[0] is None and llamadas[1] == {"http": "http://u:p@p1:1", "https": "http://u:p@p1:1"}

    def test_sin_length_seconds_en_ningun_lado_devuelve_cero(self):
        with patch("services.yt_transcript.requests.get", return_value=_fake_response(text="bot")), \
             patch("services.downloader._get_proxy_list", return_value=["http://u:p@p1:1"] * 5):
            assert _fetch_length_seconds("abc12345678") == 0


class TestGetVideoMetadataDuration:
    def test_oembed_ok_usa_length_seconds_del_html(self):
        oembed_resp = MagicMock(status_code=200)
        oembed_resp.json.return_value = {"title": "Un video", "author_name": "Canal"}
        with patch("services.yt_transcript.requests.get", side_effect=[
            oembed_resp,
            _fake_response(text='"lengthSeconds":"600"'),
        ]):
            meta = get_video_metadata("abc12345678")
        assert meta["duration"] == 600
        assert meta["title"] == "Un video"

    def test_oembed_ok_pero_sin_length_seconds_da_duracion_cero(self):
        oembed_resp = MagicMock(status_code=200)
        oembed_resp.json.return_value = {"title": "Un video", "author_name": "Canal"}
        with patch("services.yt_transcript.requests.get", side_effect=[
            oembed_resp,
            _fake_response(text="sin el campo"),
        ]):
            meta = get_video_metadata("abc12345678")
        assert meta["duration"] == 0
