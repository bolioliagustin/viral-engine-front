"""
W16: el idioma del transcript no se decide con la intro de un video.

Job 88d6a444 (21-sep-2026): un programa argentino salió en inglés porque
Whisper "detectó" inglés en el primer tramo (intro con música y gritos) y
tradujo los 54 min. Job 1ea90b15: Supadata devolvió la pista de captions
auto-traducida al inglés. Dos defensas: mayoría sobre tramos repartidos, y
`TRANSCRIPT_LANGUAGE` para fijarlo.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "development")

import services.transcriber as transcriber  # noqa: E402
import services.yt_transcript as yt_transcript  # noqa: E402


def _chunks(n):
    return [(f"/tmp/chunk_{i}.mp3", i * 600.0) for i in range(n)]


def _correr(idiomas_por_tramo, n=12, language=None):
    """Corre `transcribe_full_audio` con Whisper simulado, devuelve (lang, llamadas)."""
    llamadas = []

    def _fake_whisper(path, prompt=None, language=None, provider=None, usage_task=None):
        idx = int(path.split("_")[-1].split(".")[0])
        llamadas.append((idx, language))
        return {
            "words": [{"word": "x", "start": 0.0, "end": 0.1}],
            "segments": [{"start": 0.0, "end": 1.0, "text": "x"}],
            "language": language or idiomas_por_tramo[idx],
            "duration": 600.0,
            "provider": "groq",
        }

    with patch.object(transcriber, "transcribe_with_whisper_openrouter", _fake_whisper), \
         patch("services.audio_utils.split_audio_ffmpeg", return_value=_chunks(n)), \
         patch("services.audio_utils.cleanup_chunks", lambda *_: None), \
         patch.object(transcriber, "merge_chunk_transcripts",
                      lambda pares, overlap_sec=0: {
                          "words": [], "segments": [], "duration": 7200.0,
                          "language": [p[1] for p in pares if p[1]][0]["language"],
                      }):
        res = transcriber.transcribe_full_audio("/tmp/audio.m4a", language=language)
    return res, llamadas


class TestDeteccionPorMayoria:
    def test_intro_en_ingles_no_arrastra_el_video_entero(self):
        # el tramo 1 "suena" a inglés, el resto es español
        idiomas = ["english"] + ["spanish"] * 11
        res, llamadas = _correr(idiomas)
        # se detecta sobre 3 tramos repartidos (25/50/75%), no sobre el 1
        muestras = sorted({i for i, lang in llamadas if lang is None})
        assert muestras == [3, 6, 9]
        assert res["language"] == "es"
        # y todos los demás tramos se piden fijados en español
        assert {lang for i, lang in llamadas if lang is not None} == {"es"}

    def test_un_tramo_discordante_pierde_la_mayoria(self):
        idiomas = ["spanish"] * 12
        idiomas[6] = "english"  # una muestra discrepa
        res, llamadas = _correr(idiomas)
        assert res["language"] == "es"
        # el tramo que salió en el idioma equivocado se rehace fijado en es
        assert (6, "es") in llamadas

    def test_idioma_explicito_no_gasta_muestras(self):
        res, llamadas = _correr(["english"] * 12, language="es")
        assert [lang for _, lang in llamadas] == ["es"] * 12
        assert res["language"] == "es"


class TestTranscriptLanguageEnSupadata:
    def test_sin_configurar_no_manda_lang(self, monkeypatch):
        monkeypatch.setattr(yt_transcript, "TRANSCRIPT_LANGUAGE", "")
        with patch.object(yt_transcript.requests, "get") as g:
            yt_transcript._supadata_request("https://youtu.be/x", "k")
        assert "lang" not in g.call_args.kwargs["params"]

    def test_configurado_pide_esa_pista(self, monkeypatch):
        monkeypatch.setattr(yt_transcript, "TRANSCRIPT_LANGUAGE", "es")
        with patch.object(yt_transcript.requests, "get") as g:
            yt_transcript._supadata_request("https://youtu.be/x", "k")
        assert g.call_args.kwargs["params"]["lang"] == "es"
