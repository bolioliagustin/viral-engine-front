"""
Cortes anclados a las frases del modelo (W1, docs/PLAN_CALIDAD.md §4).

La Pasada A entrega start_time/end_time sobre bloques de captions (C1) y el
pipeline descartaba sus first/last_phrase_in_audio (C2). Ahora la verdad son
las frases: se localizan en la Transcripción del segmento ancho y el Clip va
de inicio de oración de la primera al fin de oración de la última.
"""
import os
import sys
import types
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.validation import (  # noqa: E402
    locate_phrase,
    sentence_bounds_around,
    compute_clip_bounds,
    build_clip_quality_issues,
)


# ── Fixtures sintéticas ──────────────────────────────────────────────────────

def _speak(text: str, start: float, wps: float = 2.5, gap_before: float = 0.0) -> list[dict]:
    """Palabras de `text` a `wps` palabras/s desde `start` (+gap_before)."""
    out = []
    t = start + gap_before
    for tok in text.split():
        out.append({"word": tok, "start": round(t, 3), "end": round(t + 0.3, 3)})
        t += 1.0 / wps
    return out


def _segment(sentences: list[tuple[str, float]], start: float = 0.0, wps: float = 2.5) -> list[dict]:
    """
    Oraciones consecutivas: [(texto, gap_antes_s), ...]. Cada oración termina
    donde termina su texto (puntuación incluida en el texto).
    """
    words: list[dict] = []
    t = start
    for text, gap in sentences:
        chunk = _speak(text, t, wps=wps, gap_before=gap)
        words += chunk
        t = chunk[-1]["end"] + 0.1
    return words


FIRST = "El error del Ferrari fue creer que la velocidad lo era todo."
PAYOFF = "La vía de contagio más habitual es el polvo que barrés."

# Segmento ancho típico: 15 s de relleno antes, la idea, y relleno después.
WIDE = _segment([
    ("y entonces bueno nada eso es lo que te decía el otro día.", 0.0),
    ("Bueno, sigamos con lo que importa de verdad hoy.", 0.4),
    (FIRST, 0.8),
    ("Cuando corrés sin frenos terminás pagando el doble.", 0.5),
    ("Y eso lo aprendí tarde, como casi todo.", 0.5),
    (PAYOFF, 0.7),
    ("Y después de eso ya no hay vuelta atrás.", 0.5),
    ("Bueno pasemos a otra cosa que quería comentar.", 0.9),
], start=1.0)
WIDE_DUR = WIDE[-1]["end"] + 3.0


def _t(words, idx):
    return words[idx]["start"]


def _word_at(words, t):
    return next(w for w in words if abs(w["start"] - t) < 1e-6)


# ── locate_phrase ────────────────────────────────────────────────────────────

class TestLocatePhrase:

    def test_exact_match(self):
        r = locate_phrase(WIDE, FIRST)
        assert r is not None
        assert WIDE[r["start_idx"]]["word"] == "El"
        assert WIDE[r["end_idx"]]["word"] == "todo."
        assert r["score"] == 1.0

    def test_accents_and_punctuation_ignored(self):
        r = locate_phrase(WIDE, "la via de contagio mas habitual, es el polvo que barres")
        assert r is not None
        assert WIDE[r["start_idx"]]["word"] == "La"
        assert WIDE[r["end_idx"]]["word"] == "barrés."

    def test_one_word_changed_still_matches(self):
        # 1 de 8 palabras distinta ("velocidad" → "rapidez") → 0.875 ≥ 0.75
        r = locate_phrase(WIDE, "El error del Ferrari fue creer que la rapidez")
        assert r is not None
        assert WIDE[r["start_idx"]]["word"] == "El"

    def test_too_many_differences_is_none(self):
        assert locate_phrase(WIDE, "El horror del Fiat fue pensar que la rapidez") is None

    def test_absent_phrase_is_none(self):
        assert locate_phrase(WIDE, "esto no se dijo nunca en el audio") is None

    def test_prefer_first_and_last_on_repeated_phrase(self):
        words = _segment([
            ("En realidad los modelos están bastante bien.", 0.0),
            ("Y eso es lo que importa.", 0.5),
            ("En realidad los modelos están bastante bien.", 0.5),
        ])
        first = locate_phrase(words, "en realidad los modelos están bastante bien", prefer="first")
        last = locate_phrase(words, "en realidad los modelos están bastante bien", prefer="last")
        assert first["start_idx"] == 0
        assert last["start_idx"] > first["end_idx"]
        assert words[last["start_idx"]]["word"] == "En"

    def test_start_idx_restricts_search(self):
        words = _segment([
            ("En realidad los modelos están bastante bien.", 0.0),
            ("Y eso es lo que importa.", 0.5),
            ("En realidad los modelos están bastante bien.", 0.5),
        ])
        r = locate_phrase(words, "en realidad los modelos", prefer="first", start_idx=8)
        assert r["start_idx"] >= 8

    def test_empty_inputs(self):
        assert locate_phrase([], FIRST) is None
        assert locate_phrase(WIDE, "") is None


# ── sentence_bounds_around ───────────────────────────────────────────────────

class TestSentenceBounds:

    def test_punctuation_bounds(self):
        r = locate_phrase(WIDE, "creer que la velocidad")
        s_idx, e_idx = sentence_bounds_around(WIDE, r["start_idx"])
        assert WIDE[s_idx]["word"] == "El"
        assert WIDE[e_idx]["word"] == "todo."

    def test_gap_bounds_without_punctuation(self):
        words = _speak("hola qué tal todo bien", 0.0) + _speak("ahora arranca la idea buena", 5.0)
        s_idx, e_idx = sentence_bounds_around(words, 6)
        assert words[s_idx]["word"] == "ahora"
        assert e_idx == len(words) - 1

    def test_lookback_cap_on_endless_sentence(self):
        # 30 s de palabras sin puntuación ni gaps: no retroceder más de 10 s
        words = _speak(" ".join(f"w{i}" for i in range(75)), 0.0)
        s_idx, e_idx = sentence_bounds_around(words, 60)
        assert words[60]["start"] - words[s_idx]["start"] <= 10.0 + 0.5
        assert words[e_idx]["end"] - words[60]["end"] <= 10.0 + 0.5


# ── compute_clip_bounds ──────────────────────────────────────────────────────

def _bounds(words, first, last, **kw):
    params = dict(
        seg_start_abs=100.0,
        seg_end_abs=100.0 + WIDE_DUR,
        video_duration=3000.0,
        hint_start_abs=100.0 + 12.0,
        hint_end_abs=100.0 + 30.0,
    )
    params.update(kw)
    return compute_clip_bounds(words, first, last, **params)


class TestComputeClipBounds:

    def test_phrases_found_clip_spans_sentences(self):
        r = _bounds(WIDE, FIRST, PAYOFF)
        assert r["flags"] == []
        first_w = _word_at(WIDE, locate_phrase(WIDE, FIRST)["start_idx"] and _t(WIDE, locate_phrase(WIDE, FIRST)["start_idx"]))
        # Arranca 0,25 s antes de "El" y termina 0,40 s después de "barrés."
        assert r["start_rel"] == pytest.approx(first_w["start"] - 0.25, abs=0.01)
        payoff_end = WIDE[locate_phrase(WIDE, PAYOFF, prefer="last")["end_idx"]]["end"]
        assert r["end_rel"] == pytest.approx(payoff_end + 0.40, abs=0.01)
        assert 15.0 <= r["end_rel"] - r["start_rel"] <= 60.0
        assert r["evidence"]["first_found"] and r["evidence"]["last_found"]
        assert r["evidence"]["extend_recommended"] is False

    def test_phrases_at_segment_edges(self):
        # Primera frase es la primera oración del segmento y la última la final
        words = _segment([(FIRST, 0.0), ("Algo en el medio que suma.", 0.5), (PAYOFF, 0.5)], wps=1.5)
        dur = words[-1]["end"] + 0.3
        r = compute_clip_bounds(
            words, FIRST, PAYOFF,
            seg_start_abs=0.0, seg_end_abs=dur, video_duration=dur,
        )
        assert r["start_rel"] == 0.0
        assert r["end_rel"] == pytest.approx(dur, abs=0.01)   # remate + 0,4 s, capped al segmento
        assert r["end_rel"] - r["start_rel"] >= 15.0
        assert r["flags"] == []

    def test_last_phrase_outside_segment_recommends_extension(self):
        r = _bounds(WIDE, FIRST, "esta frase quedó fuera del segmento descargado")
        assert "payoff_not_found" in r["flags"]
        assert r["evidence"]["extend_recommended"] is True
        # Fin de respaldo: primer fin de oración en o después del end_time numérico
        assert r["evidence"]["end_source"] in ("sentence_after_hint", "last_fitting_sentence")
        assert 15.0 <= r["end_rel"] - r["start_rel"] <= 60.0
        # y sigue arrancando en la primera frase
        assert r["evidence"]["start_source"] == "first_phrase"

    def test_no_extension_when_segment_reaches_video_end(self):
        r = _bounds(
            WIDE, FIRST, "frase inexistente",
            video_duration=100.0 + WIDE_DUR,   # el segmento ya llega al final
        )
        assert "payoff_not_found" in r["flags"]
        assert r["evidence"]["extend_recommended"] is False

    def test_first_phrase_missing_uses_hint_sentence_start(self):
        r = _bounds(WIDE, "frase que no está en el audio", PAYOFF)
        assert "hook_not_found" in r["flags"]
        assert r["evidence"]["start_source"] == "sentence_after_hint"
        # Inicio de oración más cercano ≥ start_time − 3 s (hint_start_rel = 12)
        assert r["start_rel"] >= 12.0 - 3.0 - 0.25
        assert r["evidence"]["last_found"] is True

    def test_first_phrase_missing_falls_back_to_hook_anchor(self):
        r = _bounds(WIDE, "frase inexistente", PAYOFF, hook="Cuando corrés sin frenos pagás el doble")
        assert "hook_not_found" in r["flags"]
        assert r["evidence"]["start_source"] == "hook_anchor"
        assert WIDE[[i for i, w in enumerate(WIDE) if abs(w["start"] - 0.25 - r["start_rel"]) < 0.01][0]]["word"] == "Cuando"

    def test_repeated_last_phrase_prefers_last_after_first(self):
        words = _segment([
            ("Arrancamos con la idea principal.", 0.0),
            ("Lo importante es medir antes de cambiar.", 0.5),
            ("Y después de eso seguimos hablando un rato largo.", 0.5),
            ("Lo importante es medir antes de cambiar.", 0.5),
            ("Fin del bloque.", 0.5),
        ])
        dur = words[-1]["end"] + 1.0
        r = compute_clip_bounds(
            words, "Arrancamos con la idea principal", "lo importante es medir antes de cambiar",
            seg_start_abs=0.0, seg_end_abs=dur, video_duration=500.0,
        )
        second = locate_phrase(words, "lo importante es medir antes de cambiar", prefer="last")
        assert r["evidence"]["last_idx"] == second["end_idx"]

    def test_no_punctuation_uses_gaps(self):
        words = (
            _speak("relleno relleno relleno relleno relleno", 0.0)
            + _speak("el error del ferrari fue creer que la velocidad lo era todo", 4.0)
            + _speak("cuando corrés sin frenos terminás pagando el doble", 10.5)
            + _speak("la vía de contagio más habitual es el polvo que barrés", 16.0)
            + _speak("y después de eso ya no hay vuelta atrás", 22.0)
            + _speak("relleno final relleno final relleno", 28.0)
        )
        dur = words[-1]["end"] + 2.0
        r = compute_clip_bounds(
            words, "el error del ferrari fue creer", "la vía de contagio más habitual",
            seg_start_abs=0.0, seg_end_abs=dur, video_duration=500.0,
        )
        assert r["flags"] == []
        assert r["start_rel"] == pytest.approx(4.0 - 0.25, abs=0.01)
        # termina al final de la "oración" (gap) que contiene la última frase:
        # "barrés" termina en 20.3 → 20.7; no entra la oración siguiente (22.0)
        assert r["end_rel"] == pytest.approx(20.3 + 0.4, abs=0.01)

    def test_short_clip_is_extended_to_next_sentence_end(self):
        words = _segment([
            ("Frase corta de gancho.", 0.0),
            ("Y el remate viene enseguida.", 0.4),
            ("Esta oración extra completa los quince segundos que hacen falta.", 0.4),
            ("Y otra más por si acaso hiciera falta todavía.", 0.4),
        ], wps=2.0)
        dur = words[-1]["end"] + 1.0
        r = compute_clip_bounds(
            words, "Frase corta de gancho", "el remate viene enseguida",
            seg_start_abs=0.0, seg_end_abs=dur, video_duration=500.0,
        )
        assert r["end_rel"] - r["start_rel"] >= 15.0
        assert r["evidence"].get("extended_for_min") is True
        assert r["flags"] == []

    def test_long_clip_moves_start_forward_before_dropping_payoff(self):
        # Primera frase + 65 s hasta el remate (> 60 s): antes que perder la
        # última frase se mueve el START al siguiente inicio de oración
        # (decisión del brief W1); la primera frase queda afuera y se flaggea.
        sentences = [(FIRST, 0.0)] + [
            (f"Oración de relleno número {i} que dura lo suficiente para sumar tiempo.", 0.4)
            for i in range(7)
        ] + [(PAYOFF, 0.4)]
        words = _segment(sentences, wps=1.5)
        dur = words[-1]["end"] + 1.0
        r = compute_clip_bounds(
            words, FIRST, PAYOFF,
            seg_start_abs=0.0, seg_end_abs=dur, video_duration=500.0,
        )
        payoff_end = words[locate_phrase(words, PAYOFF, prefer="last")["end_idx"]]["end"]
        assert 15.0 <= r["end_rel"] - r["start_rel"] <= 60.0
        assert r["end_rel"] >= payoff_end
        assert "payoff_not_found" not in r["flags"]
        assert r["evidence"].get("start_moved_for_max") is True
        assert r["evidence"].get("hook_dropped_for_max") is True
        assert "hook_not_found" in r["flags"]
        # arranca en el primer inicio de oración que deja ≤ 60 s
        start_word = next(w for w in words if abs(w["start"] - 0.25 - r["start_rel"]) < 0.01)
        assert start_word["word"] == "Oración"

    def test_long_clip_without_room_cuts_end_and_flags(self):
        # Una sola oración larguísima (sin puntuación ni gaps) de 80 s con la
        # primera frase al inicio y el remate al final: no hay inicio de oración
        # al que mover el start → se corta a ≤ 60 s y se flaggea el remate perdido.
        text = FIRST[:-1] + " " + " ".join(f"relleno{i}" for i in range(90)) + " " + PAYOFF
        words = _speak(text, 0.0, wps=1.5)
        dur = words[-1]["end"] + 1.0
        r = compute_clip_bounds(
            words, FIRST, PAYOFF,
            seg_start_abs=0.0, seg_end_abs=dur, video_duration=500.0,
        )
        assert r["end_rel"] - r["start_rel"] <= 60.0
        assert "payoff_not_found" in r["flags"]
        assert r["evidence"].get("payoff_dropped_for_max") is True

    def test_never_start_lowercase_if_sentence_start_within_2s(self):
        # La "primera frase" empieza en minúscula a mitad de oración corta:
        # el inicio salta al comienzo de esa oración (≤ 2 s antes).
        words = _segment([
            ("Relleno inicial que no importa nada.", 0.0),
            ("Mirá, el error del Ferrari fue creer que la velocidad lo era todo.", 0.5),
            ("Y después de eso ya no hay vuelta atrás.", 0.5),
            (PAYOFF, 0.5),
            ("Cierre del bloque con algo más de texto.", 0.5),
        ])
        dur = words[-1]["end"] + 1.0
        r = compute_clip_bounds(
            words, "el error del Ferrari fue creer", PAYOFF,
            seg_start_abs=0.0, seg_end_abs=dur, video_duration=500.0,
        )
        start_word = next(w for w in words if abs(w["start"] - 0.25 - r["start_rel"]) < 0.01)
        assert start_word["word"] == "Mirá,"

    def test_pads_never_cross_adjacent_words(self):
        # Habla continua: Whisper deja las palabras pegadas (end == next.start).
        # El pad de 0,25 s NO debe meter la última sílaba de la oración anterior
        # ni el de 0,40 s la primera de la siguiente (caso real e2e: los clips
        # arrancaban con "pasaba. Entraba en Claude…").
        words = []
        t = 0.0
        for tok in ("relleno relleno relleno pasaba. " + FIRST + " Y algo más. " + PAYOFF + " Para entenderlo hay que verlo.").split():
            words.append({"word": tok, "start": round(t, 3), "end": round(t + 0.7, 3)})
            t += 0.7
        dur = t + 1.0
        r = compute_clip_bounds(
            words, FIRST, PAYOFF,
            seg_start_abs=0.0, seg_end_abs=dur, video_duration=500.0,
        )
        first_w = words[locate_phrase(words, FIRST)["start_idx"]]
        last_w = words[locate_phrase(words, PAYOFF, prefer="last")["end_idx"]]
        assert r["start_rel"] == pytest.approx(first_w["start"], abs=0.001)   # sin pad: pegadas
        assert r["end_rel"] == pytest.approx(last_w["end"], abs=0.001)
        # y con las palabras desplazadas, la primera del clip es "El" y la última "barrés."
        from services.clip_generator import shift_words_timeline, filter_whisper_words
        shifted = filter_whisper_words(
            shift_words_timeline(words, r["start_rel"], clip_duration=r["end_rel"] - r["start_rel"]),
            r["end_rel"] - r["start_rel"],
        )
        assert shifted[0]["word"] == "El"
        assert shifted[-1]["word"] == "barrés."

    def test_numbers_in_digits_match_words(self):
        words = _speak("bueno pasamos ahora al hack número 5 que es el mejor", 0.0)
        r = locate_phrase(words, "pasamos ahora al hack número cinco")
        assert r is not None
        assert words[r["end_idx"]]["word"] == "5"

    def test_no_words_returns_hint_bounds(self):
        r = compute_clip_bounds(
            [], FIRST, PAYOFF,
            seg_start_abs=100.0, seg_end_abs=170.0, video_duration=500.0,
            hint_start_abs=115.0, hint_end_abs=150.0,
        )
        assert r["start_rel"] == 15.0
        assert r["end_rel"] == 50.0
        assert set(r["flags"]) == {"hook_not_found", "payoff_not_found"}


# ── Sin Verificación → flujo legacy intacto ─────────────────────────────────

class TestLegacyPathWithoutVerification:

    def test_moment_without_verification_has_no_phrases(self):
        from main import _moment_phrases
        m = types.SimpleNamespace(verification=None)
        assert _moment_phrases(m) == ("", "")
        m2 = types.SimpleNamespace(verification=types.SimpleNamespace(
            first_phrase_in_audio="  ", last_phrase_in_audio=None,
        ))
        assert _moment_phrases(m2) == ("", "")

    def test_refine_bounds_legacy_healthy_clip_unchanged(self):
        """Clip sano de 30 s: el refinamiento numérico no toca nada (igual que hoy)."""
        from main import _refine_bounds_legacy
        words = _segment([
            ("Hola a todos y bienvenidos a este episodio del podcast.", 0.0),
            ("Hoy vamos a hablar de un tema que me apasiona bastante.", 0.3),
            ("Y al final les cuento la conclusión que saqué de todo esto.", 0.3),
            ("Así que quedate hasta el final porque vale la pena.", 0.3),
            ("Empecemos entonces con la primera idea importante del día.", 0.3),
        ], start=0.4)
        dur = 30.0
        m = types.SimpleNamespace(verification=None, hook="Un tema que me apasiona", viral_overlay="")
        out = _refine_bounds_legacy(
            clip_words=words, clip_duration=dur, clip_segments_whisper=[],
            moment=m, precut_path="/nonexistent/precut.mp4",
            video_id="vid", moment_index=1, whisper_timestamps_suspect=False,
        )
        assert out["precut_path"] == "/nonexistent/precut.mp4"   # no hubo re-corte
        assert out["clip_duration"] == dur
        assert out["snap_trim_start"] == 0.0
        assert out["min_duration_reverted"] is False
        assert len(out["clip_words"]) == len(words)


# ── Márgenes de descarga asimétricos ────────────────────────────────────────

class TestClipMargins:

    def test_defaults(self, monkeypatch):
        from services.downloader import _clip_margins_sec
        for k in ("CLIP_KEYFRAME_MARGIN_SEC", "CLIP_MARGIN_BEFORE_SEC", "CLIP_MARGIN_AFTER_SEC"):
            monkeypatch.delenv(k, raising=False)
        assert _clip_margins_sec() == (15.0, 20.0)

    def test_legacy_alias_sets_both(self, monkeypatch):
        from services.downloader import _clip_margins_sec
        monkeypatch.delenv("CLIP_MARGIN_BEFORE_SEC", raising=False)
        monkeypatch.delenv("CLIP_MARGIN_AFTER_SEC", raising=False)
        monkeypatch.setenv("CLIP_KEYFRAME_MARGIN_SEC", "8")
        assert _clip_margins_sec() == (8.0, 8.0)

    def test_explicit_wins_over_legacy(self, monkeypatch):
        from services.downloader import _clip_margins_sec
        monkeypatch.setenv("CLIP_KEYFRAME_MARGIN_SEC", "8")
        monkeypatch.setenv("CLIP_MARGIN_AFTER_SEC", "30")
        monkeypatch.delenv("CLIP_MARGIN_BEFORE_SEC", raising=False)
        assert _clip_margins_sec() == (8.0, 30.0)

    def test_download_clip_ytdlp_explicit_after_margin(self, monkeypatch):
        from services.downloader import download_clip_ytdlp
        for k in ("CLIP_KEYFRAME_MARGIN_SEC", "CLIP_MARGIN_BEFORE_SEC", "CLIP_MARGIN_AFTER_SEC"):
            monkeypatch.delenv(k, raising=False)
        mock_ydl_cls = MagicMock()
        mock_ydl_cls.return_value.__enter__.return_value = MagicMock()
        with patch("services.downloader.yt_dlp.YoutubeDL", mock_ydl_cls), \
             patch("services.downloader._build_ydl_opts", side_effect=lambda o, **kw: o), \
             patch("services.downloader.Path") as mock_path:
            mock_path.return_value.with_suffix.return_value = mock_path.return_value
            mp4 = MagicMock()
            mp4.exists.return_value = True
            mp4.stat.return_value.st_size = 1024 * 1024
            mock_path.side_effect = lambda *a, **k: mp4 if str(a[0]).endswith(".mp4") else MagicMock()
            result = download_clip_ytdlp(
                "https://youtube.com/watch?v=abc12345678",
                start_sec=100.0, end_sec=160.0, output_path="/tmp/clip",
                margin_after_sec=45.0, video_duration=1000.0,
            )
        assert result.download_start == 85.0
        assert result.download_end == 205.0

    def test_resolve_source_extends_cached_segment(self, tmp_path, monkeypatch):
        """Segmento cacheado corto + extensión → re-descarga con margen después ampliado."""
        import main
        from services.downloader import ClipDownloadResult
        for k in ("CLIP_KEYFRAME_MARGIN_SEC", "CLIP_MARGIN_BEFORE_SEC", "CLIP_MARGIN_AFTER_SEC"):
            monkeypatch.delenv(k, raising=False)
        cached_file = tmp_path / "seg.mp4"
        cached_file.write_bytes(b"x")
        cache = {1: ClipDownloadResult(str(cached_file), 85.0, 180.0)}

        # sin extensión → usa el cache
        src = main._resolve_moment_video_source(
            moment_index=1, start_s=100.0, end_s=160.0, video_url="u", video_id="v",
            video_duration=1000.0, muxed_video_path=None, clip_paths_cache=cache,
            partial_download_failed=False,
        )
        assert src.kind == "cached"
        assert (src.avail_start_abs, src.avail_end_abs) == (85.0, 180.0)
        assert src.src_start == 15.0

        # con extensión +25 → el cache (llega a 180) no alcanza (necesita 205) → yt-dlp
        fake = ClipDownloadResult(str(cached_file), 85.0, 205.0)
        with patch.object(main, "_should_use_ytdlp_for_clips", return_value=True), \
             patch.object(main, "download_clip_ytdlp", return_value=fake) as dl:
            src2 = main._resolve_moment_video_source(
                moment_index=1, start_s=100.0, end_s=160.0, video_url="u", video_id="v",
                video_duration=1000.0, muxed_video_path=None, clip_paths_cache=cache,
                partial_download_failed=False, extend_after_sec=25.0,
            )
        assert src2.kind == "ytdlp"
        assert dl.call_args.kwargs["margin_after_sec"] == 45.0
        assert "_ext25" in dl.call_args.kwargs["output_path"]
        assert src2.avail_end_abs == 205.0


# ── transcribe_with_whisper_openrouter(provider=...) ────────────────────────

class TestWhisperProvider:

    @staticmethod
    def _fake_audio():
        audio = MagicMock()
        audio.__len__ = lambda self: 30_000   # 30 s
        return audio

    def test_provider_openai_skips_groq(self):
        from services import transcriber
        openai_client = MagicMock()
        resp = MagicMock()
        resp.model_dump.return_value = {"text": "hola", "words": [], "segments": []}
        openai_client.audio.transcriptions.create.return_value = resp
        groq_client = MagicMock()
        with patch("pydub.AudioSegment.from_file", return_value=self._fake_audio()), \
             patch.object(transcriber, "_openai_client", return_value=openai_client), \
             patch.object(transcriber, "_groq_client", return_value=groq_client), \
             patch.object(transcriber, "_save_transcript"), \
             patch.object(transcriber, "_record_whisper_result"), \
             patch("builtins.open", MagicMock()):
            result = transcriber.transcribe_with_whisper_openrouter(
                "/tmp/x.mp3", prompt="p", language="es", provider="openai",
            )
        assert result["provider"] == "openai"
        assert openai_client.audio.transcriptions.create.called
        assert not groq_client.audio.transcriptions.create.called
        assert openai_client.audio.transcriptions.create.call_args.kwargs["model"] == "whisper-1"

    def test_provider_groq_without_key_raises(self):
        from services import transcriber
        with patch("pydub.AudioSegment.from_file", return_value=self._fake_audio()), \
             patch.object(transcriber, "_groq_client", return_value=None):
            with pytest.raises(RuntimeError):
                transcriber.transcribe_with_whisper_openrouter("/tmp/x.mp3", provider="groq")

    def test_default_cascade_groq_first(self):
        from services import transcriber
        groq_client = MagicMock()
        resp = MagicMock()
        resp.model_dump.return_value = {"text": "hola", "words": [], "segments": []}
        groq_client.audio.transcriptions.create.return_value = resp
        with patch("pydub.AudioSegment.from_file", return_value=self._fake_audio()), \
             patch.object(transcriber, "_groq_client", return_value=groq_client), \
             patch.object(transcriber, "_openai_client", return_value=MagicMock()), \
             patch.object(transcriber, "_save_transcript"), \
             patch.object(transcriber, "_record_whisper_result"), \
             patch("builtins.open", MagicMock()):
            result = transcriber.transcribe_with_whisper_openrouter("/tmp/x.mp3")
        assert result["provider"] == "groq"

    def test_invalid_provider(self):
        from services import transcriber
        with patch("pydub.AudioSegment.from_file", return_value=self._fake_audio()):
            with pytest.raises(ValueError):
                transcriber.transcribe_with_whisper_openrouter("/tmp/x.mp3", provider="deepgram")


# ── Flags nuevos ─────────────────────────────────────────────────────────────

class TestNewFlags:

    def test_w1_flags_emitted(self):
        issues = build_clip_quality_issues(
            hook_not_found=True, payoff_not_found=True,
            margin_extended=True, subs_disabled_timestamps=True,
        )
        assert issues == [
            "hook_not_found", "payoff_not_found", "margin_extended", "subs_disabled_timestamps",
        ]
