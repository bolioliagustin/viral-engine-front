"""
W17: YouTube dobla con IA y sirve el doblaje como pista por defecto.

Medido el 23-sep-2026 en B60BHDNFNxM (programa argentino): la pista default
era `English (US)` con `isAutoDubbed: true` y la original `Spanish (US)
original`. Bajar la default dio transcript y clips en inglés — el "problema
de detección de idioma" era en realidad audio doblado.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "development")

import services.downloader as downloader  # noqa: E402


def _fmt(url, bitrate, track=None, mime="audio/mp4"):
    f = {"mimeType": mime, "url": url, "bitrate": bitrate}
    if track is not None:
        f["audioTrack"] = track
    return f


DOBLADA = {"displayName": "English (US)", "id": "en-US.10",
           "audioIsDefault": True, "isAutoDubbed": True}
ORIGINAL = {"displayName": "Spanish (US) original", "id": "es-US.4",
            "audioIsDefault": False}
ORIGINAL_PT = {"displayName": "Portuguese original", "id": "pt-BR.4",
               "audioIsDefault": False}


class TestPreferirLaPistaOriginal:
    def test_descarta_el_doblaje_aunque_sea_la_pista_por_defecto(self, monkeypatch):
        monkeypatch.setattr(downloader, "AUDIO_TRACK_LANGUAGE", "es")
        elegidas = downloader._prefer_original_audio_track(
            [_fmt("dub", 136463, DOBLADA), _fmt("orig", 136718, ORIGINAL)]
        )
        assert [f["url"] for f in elegidas] == ["orig"]

    def test_sin_idioma_configurado_igual_evita_el_doblaje(self, monkeypatch):
        monkeypatch.setattr(downloader, "AUDIO_TRACK_LANGUAGE", "")
        elegidas = downloader._prefer_original_audio_track(
            [_fmt("dub", 136463, DOBLADA), _fmt("orig", 136718, ORIGINAL_PT)]
        )
        assert [f["url"] for f in elegidas] == ["orig"]

    def test_video_sin_pistas_multiples_no_cambia_nada(self, monkeypatch):
        """El caso normal: un solo audio y sin `audioTrack`."""
        monkeypatch.setattr(downloader, "AUDIO_TRACK_LANGUAGE", "es")
        fmts = [_fmt("a", 128000), _fmt("b", 64000)]
        assert downloader._prefer_original_audio_track(fmts) == fmts

    def test_si_no_hay_pista_en_el_idioma_pedido_usa_la_original(self, monkeypatch):
        monkeypatch.setattr(downloader, "AUDIO_TRACK_LANGUAGE", "es")
        elegidas = downloader._prefer_original_audio_track(
            [_fmt("dub", 136463, DOBLADA), _fmt("orig_pt", 136718, ORIGINAL_PT)]
        )
        assert [f["url"] for f in elegidas] == ["orig_pt"]

    def test_solo_hay_doblaje_no_deja_al_job_sin_audio(self, monkeypatch):
        monkeypatch.setattr(downloader, "AUDIO_TRACK_LANGUAGE", "es")
        elegidas = downloader._prefer_original_audio_track([_fmt("dub", 136463, DOBLADA)])
        assert [f["url"] for f in elegidas] == ["dub"]


class TestPickRapidapiUsaLaOriginal:
    def test_el_par_video_audio_sale_con_la_pista_original(self, monkeypatch):
        monkeypatch.setattr(downloader, "AUDIO_TRACK_LANGUAGE", "es")
        data = {"adaptiveFormats": [
            {"mimeType": 'video/mp4; codecs="avc1.4d401f"', "url": "v720",
             "qualityLabel": "720p", "bitrate": 1_500_000},
            _fmt("dub", 136463, DOBLADA),
            _fmt("orig", 136718, ORIGINAL),
        ]}
        vid, aud = downloader._pick_rapidapi_formats(data)
        assert vid["url"] == "v720"
        assert aud["url"] == "orig"


class TestFiltroDeYtDlp:
    def test_con_idioma_filtra_la_pista(self, monkeypatch):
        monkeypatch.setattr(downloader, "AUDIO_TRACK_LANGUAGE", "es")
        assert downloader._ydl_audio_filter() == "[language^=es]"

    def test_sin_idioma_no_filtra(self, monkeypatch):
        monkeypatch.setattr(downloader, "AUDIO_TRACK_LANGUAGE", "")
        assert downloader._ydl_audio_filter() == ""
