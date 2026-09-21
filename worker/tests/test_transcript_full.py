"""
W4 — Transcript puntuado por palabra, con silencios (docs/PLAN_CALIDAD.md §4 W4 y §9).

Cubre: unión de tramos sin duplicados, alineación de puntuación sobre
palabras, agrupado en Líneas por puntuación y por pausa, tokens de silencio y
`words_without_silence()`, formato de entrada de la Pasada A con el flag, y
que con `TRANSCRIPT_SOURCE=supadata` nada cambia.
"""
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from services import transcript_lines as tl
from services import transcriber


# ── Helpers ──────────────────────────────────────────────────────────────────
def _w(word, start, end, **extra):
    d = {"word": word, "start": float(start), "end": float(end)}
    d.update(extra)
    return d


def _words_from_text(text, t0=0.0, dur=0.4, gap=0.1):
    """Palabras sin puntuación (como las devuelve Groq), una cada dur+gap s."""
    out, t = [], t0
    for tok in text.split():
        core = tl._split_token(tok)[1].lower()
        out.append(_w(core, t, t + dur))
        t += dur + gap
    return out


# ── Tokens de silencio y words_without_silence ──────────────────────────────
class TestSilence:
    def test_insert_silence_tokens_only_for_gaps_over_threshold(self):
        words = [_w("hola", 0.0, 0.4), _w("mundo", 0.5, 0.9), _w("bien", 1.5, 1.9)]
        out = tl.insert_silence_tokens(words, min_gap=0.3)
        assert [w["word"] for w in out] == ["hola", "mundo", tl.SILENCE_TOKEN, "bien"]
        sil = out[2]
        assert sil["start"] == 0.9 and sil["end"] == 1.5
        assert tl.is_silence(sil)

    def test_insert_silence_is_idempotent(self):
        words = [_w("hola", 0.0, 0.4), _w("bien", 1.5, 1.9)]
        once = tl.insert_silence_tokens(words)
        twice = tl.insert_silence_tokens(once)
        assert once == twice
        assert sum(tl.is_silence(w) for w in twice) == 1

    def test_words_without_silence_removes_only_silence(self):
        words = [_w("hola", 0.0, 0.4), _w("bien", 1.5, 1.9)]
        with_sil = tl.insert_silence_tokens(words)
        assert tl.words_without_silence(with_sil) == words
        assert tl.words_without_silence(None) == []
        assert tl.words_without_silence(words) == words  # sin silencios: idéntico

    def test_silence_stats(self):
        words = [_w("a", 0.0, 0.4), _w("b", 1.0, 1.4), _w("c", 3.4, 3.8)]
        st = tl.silence_stats(tl.insert_silence_tokens(words))
        assert st["count"] == 2
        assert st["total_sec"] == pytest.approx(0.6 + 2.0, abs=1e-3)
        assert st["median_sec"] == pytest.approx(1.3, abs=1e-3)

    def test_consumers_ignore_silence_tokens(self):
        """Los consumidores actuales de palabras no cambian por los silencios:
        Pasada B (texto del clip), densidad/guardas W3 y anclas W1 ven lo mismo."""
        from services.processor import _clip_text_from_words
        from services.validation import assess_whisper_words, locate_phrase

        words = _words_from_text("hola mundo esto es una prueba de silencios en el clip final")
        words[3]["start"] += 1.0  # hueco de 1,1 s antes de "es"
        for w in words[3:]:
            w["start"] += 0.0
        with_sil = tl.insert_silence_tokens(words)
        assert any(tl.is_silence(w) for w in with_sil)

        assert _clip_text_from_words(with_sil) == _clip_text_from_words(words)
        clean = tl.words_without_silence(with_sil)
        assert assess_whisper_words(clean, 8.0) == assess_whisper_words(words, 8.0)
        assert locate_phrase(clean, "una prueba de silencios") == locate_phrase(words, "una prueba de silencios")


# ── Alineación de puntuación sobre palabras ────────────────────────────────
class TestAlignPunctuation:
    def test_punctuation_and_case_are_copied_from_segments(self):
        words = _words_from_text("la enfermedad más contagiosa es el sarampión y el covid no")
        segments = [
            {"id": 0, "start": 0.0, "end": 3.5, "text": "La enfermedad más contagiosa es el sarampión."},
            {"id": 1, "start": 3.5, "end": 5.0, "text": "¿Y el COVID? No."},
        ]
        out = tl.align_punctuation(words, segments)
        assert [w["word"] for w in out] == [
            "La", "enfermedad", "más", "contagiosa", "es", "el", "sarampión.",
            "¿Y", "el", "COVID?", "No.",
        ]
        # Tiempos intactos y segmento asignado
        assert [w["start"] for w in out] == [w["start"] for w in words]
        assert [w["segment"] for w in out] == [0] * 7 + [1] * 4

    def test_alignment_survives_missing_and_extra_words(self):
        # Whisper escribió "2020" en words y "dos mil veinte" en el segmento, y
        # el segmento trae una palabra que no está en words.
        words = _words_from_text("en 2020 empezó todo y después terminó")
        segments = [{"id": 0, "start": 0, "end": 5, "text": "En dos mil veinte empezó todo, y después terminó."}]
        out = tl.align_punctuation(words, segments)
        texts = [w["word"] for w in out]
        assert texts[0] == "En"
        assert texts[2] == "empezó" and texts[3] == "todo," and texts[-1] == "terminó."
        assert len(out) == len(words)

    def test_standalone_punctuation_token_attaches_to_previous(self):
        words = _words_from_text("bueno eso es todo")
        segments = [{"id": 0, "start": 0, "end": 2, "text": "Bueno... eso es todo …"}]
        out = tl.align_punctuation(words, segments)
        assert out[0]["word"] == "Bueno..."
        assert out[-1]["word"] == "todo …" or out[-1]["word"].endswith("…")

    def test_words_already_punctuated_are_kept(self):
        words = [_w("Hola,", 0, 0.3), _w("mundo.", 0.4, 0.8)]
        segments = [{"id": 0, "start": 0, "end": 1, "text": "Hola, mundo."}]
        out = tl.align_punctuation(words, segments)
        assert [w["word"] for w in out] == ["Hola,", "mundo."]

    def test_rich_words_win_over_degraded_segment_text(self):
        """Caso real de Groq (podcast_general_01, 17:23): `words[]` trae
        'sarampión.' y 'empezó' pero el `text` del segmento perdió acentos y
        puntuación ('sarampi El COVID El COVID cuando empez'). La palabra manda;
        del token solo se toma lo que falta (mayúscula inicial, signos)."""
        words = _words_from_text("ese es el r0 del sarampión. el covid. el covid cuando empezó en wuhan")
        for w, tok in zip(words, "Ese es el R0 del sarampión. El COVID. El COVID cuando empezó en Wuhan".split()):
            w["word"] = tok
        segments = [{"id": 0, "start": 0, "end": 9, "text": "Ese es el R0 del sarampi El COVID El COVID cuando empez en Wuhan"}]
        out = tl.align_punctuation(words, segments)
        assert [w["word"] for w in out] == "Ese es el R0 del sarampión. El COVID. El COVID cuando empezó en Wuhan".split()

    def test_merge_word_and_token(self):
        assert tl._merge_word_and_token("ese", "Ese.") == "Ese."          # palabra pelada: toma mayúscula y signo
        assert tl._merge_word_and_token("Ese", "Ese.") == "Ese."          # le faltaba el punto
        assert tl._merge_word_and_token("sarampión.", "sarampi") == "sarampión."  # token degradado
        assert tl._merge_word_and_token("qué", "¿Qué") == "¿Qué"          # signo inicial + mayúscula
        assert tl._merge_word_and_token("covid", "COVID,") == "COVID,"
        assert tl._merge_word_and_token("inmunización", "inmunizaci") == "inmunización"

    def test_has_punctuation_detects_unpunctuated_provider(self):
        assert tl.has_punctuation([{"text": "Hola. ¿Qué tal? Bien."}])
        assert not tl.has_punctuation([{"text": " ".join(["palabra"] * 200)}])


# ── Líneas ──────────────────────────────────────────────────────────────────
class TestBuildLines:
    def test_lines_split_on_sentence_punctuation(self):
        words = _words_from_text("Hola a todos. Hoy hablamos del sarampión. ¿Es peligroso? Sí!")
        # ponerle la puntuación como la dejaría align_punctuation
        for w, tok in zip(words, "Hola a todos. Hoy hablamos del sarampión. ¿Es peligroso? Sí!".split()):
            w["word"] = tok
        lines = tl.build_lines(words)
        assert [ln["text"] for ln in lines] == [
            "Hola a todos.", "Hoy hablamos del sarampión.", "¿Es peligroso?", "Sí!",
        ]
        assert lines[0]["start"] == words[0]["start"]
        assert lines[0]["end"] == words[2]["end"]
        assert lines[1]["start"] == words[3]["start"]
        assert [ln["id"] for ln in lines] == [0, 1, 2, 3]
        assert tl.lines_punctuation_rate(lines) == 1.0

    def test_lines_split_on_pause(self):
        words = _words_from_text("una idea sin puntuación y otra idea después")
        for w in words[4:]:
            w["start"] += 1.0
            w["end"] += 1.0
        lines = tl.build_lines(words, pause_sec=0.8)
        assert [ln["text"] for ln in lines] == ["una idea sin puntuación", "y otra idea después"]
        assert tl.lines_punctuation_rate(lines) == 0.0

    def test_lines_split_on_segment_change_only_at_clause_end(self):
        # Los segmentos de Whisper cortan oraciones por la mitad: por defecto
        # un cambio de segmento no cierra la Línea; si se activa, cierra solo
        # cuando la palabra terminaba una cláusula.
        words = _words_from_text("primera parte, segunda parte tercera parte")
        words[1]["word"] = "parte,"
        for i, w in enumerate(words):
            w["segment"] = 0 if i < 2 else (1 if i < 4 else 2)
        assert [ln["text"] for ln in tl.build_lines(words)] == [
            "primera parte, segunda parte tercera parte",
        ]
        assert [ln["text"] for ln in tl.build_lines(words, split_on_segment_change=True)] == [
            "primera parte,", "segunda parte tercera parte",
        ]

    def test_lines_ignore_silence_tokens_and_keep_segments_shape(self):
        words = _words_from_text("Hola. Chau.")
        words[0]["word"], words[1]["word"] = "Hola.", "Chau."
        words[1]["start"] += 2.0
        words[1]["end"] += 2.0
        lines = tl.build_lines(tl.insert_silence_tokens(words))
        assert [ln["text"] for ln in lines] == ["Hola.", "Chau."]
        for ln in lines:
            assert {"id", "start", "end", "text"} <= set(ln)

    def test_long_run_without_punctuation_breaks_at_comma_after_soft_max(self):
        text = " ".join(["palabra"] * 10) + ", " + " ".join(["otra"] * 5)
        words = _words_from_text(text)
        words[9]["word"] = "palabra,"
        lines = tl.build_lines(words, soft_max_words=8)
        assert len(lines) == 2 and lines[0]["text"].endswith("palabra,")

    def test_words_per_minute(self):
        words = _words_from_text(" ".join(["w"] * 60), dur=0.4, gap=0.1)  # 60 palabras en 30 s
        assert tl.words_per_minute(words, duration_sec=30.0) == 120.0
        assert tl.words_per_minute(tl.insert_silence_tokens(words), duration_sec=60.0) == 60.0
        assert tl.words_per_minute([], 10) == 0.0


# ── Transcript completo y formato de la Pasada A ────────────────────────────
class TestBuildFullTranscript:
    def _raw(self):
        words = _words_from_text("la enfermedad más contagiosa es el sarampión y el covid no", dur=0.4, gap=0.1)
        # pausa larga antes de "y"
        for w in words[7:]:
            w["start"] += 1.0
            w["end"] += 1.0
        segments = [
            {"id": 0, "start": 0.0, "end": 3.5, "text": "La enfermedad más contagiosa es el sarampión."},
            {"id": 1, "start": 4.5, "end": 6.5, "text": "¿Y el COVID? No."},
        ]
        return {"words": words, "segments": segments, "language": "es", "duration": 60.0}

    def test_build_full_transcript_shape(self):
        t = tl.build_full_transcript(self._raw(), source="whisper_full", model="whisper-large-v3-turbo", provider="groq")
        assert t["source"] == "whisper_full" and t["model"] == "whisper-large-v3-turbo"
        assert [ln["text"] for ln in t["lines"]] == [
            "La enfermedad más contagiosa es el sarampión.", "¿Y el COVID?", "No.",
        ]
        assert t["segments"] == t["lines"]          # consumidores actuales ven Líneas
        assert t["text"].startswith("La enfermedad")
        assert any(tl.is_silence(w) for w in t["words"])
        assert len(tl.words_without_silence(t["words"])) == 11
        assert t["wpm"] == 11.0                     # 11 palabras / 1 min
        assert t["duration"] == 60.0 and t["language"] == "es"
        assert tl.has_full_transcript(t)
        json.dumps(t)  # cacheable

    def test_build_full_transcript_runs_punctuation_hook_after_alignment(self):
        seen = {}

        def _hook(ws):
            seen["words"] = [w["word"] for w in ws]
            return ws

        t = tl.build_full_transcript(self._raw(), source="whisper_full", model="m", punctuate_runs=_hook)
        assert seen["words"][0] == "La" and seen["words"][6] == "sarampión."  # ya alineadas
        assert len(t["lines"]) == 3

    def test_has_full_transcript_false_for_captions(self):
        assert not tl.has_full_transcript({"segments": [{"text": "x"}]})
        assert not tl.has_full_transcript({"source": "whisper_full", "lines": []})
        assert not tl.has_full_transcript(None)

    def test_format_lines_for_prompt_seconds(self):
        """Default: `[inicio-fin]` en segundos. Medido: con `[mm:ss]`
        gemini-3.5-flash concatena minutos y segundos (`[57:16]` → 5716)."""
        lines = [
            {"id": 0, "start": 0.4, "end": 3.0, "text": "Hola a todos."},
            {"id": 1, "start": 1030.4, "end": 1040.0, "text": "La enfermedad más contagiosa."},
            {"id": 2, "start": 4623.0, "end": 4630.0, "text": "Chau."},
            {"id": 3, "start": 5.0, "end": 6.0, "text": "   "},
        ]
        out = tl.format_lines_for_prompt(lines, header=False)
        assert out.splitlines() == [
            "[0-3] Hola a todos.",
            "[1030-1040] La enfermedad más contagiosa.",
            "[4623-4630] Chau.",
        ]
        with_header = tl.format_lines_for_prompt(lines)
        assert with_header.splitlines()[0] == tl.LINES_PROMPT_HEADER
        assert "SEGUNDOS" in with_header
        assert with_header.splitlines()[1] == "[0-3] Hola a todos."

    def test_format_lines_for_prompt_mmss_style(self):
        lines = [
            {"id": 0, "start": 0.4, "end": 3.0, "text": "Hola a todos."},
            {"id": 1, "start": 1030.4, "end": 1040.0, "text": "La enfermedad más contagiosa."},
            {"id": 2, "start": 4623.0, "end": 4630.0, "text": "Chau."},
        ]
        out = tl.format_lines_for_prompt(lines, header=False, style="mmss")
        assert out.splitlines() == [
            "[0:00] Hola a todos.",
            "[17:10] La enfermedad más contagiosa.",
            "[77:03] Chau.",
        ]
        assert tl.format_mmss(59.6) == "1:00"
        assert tl.format_lines_for_prompt(lines, style="mmss").splitlines()[0] == tl.LINES_PROMPT_HEADER_MMSS
        with pytest.raises(ValueError):
            tl.format_lines_for_prompt(lines, style="iso")

    def test_pasada_a_receives_lines_when_flag_active(self):
        """Con el flag activo la Pasada A recibe `[mm:ss] Oración.` en vez de bloques."""
        from services import processor

        t = tl.build_full_transcript(self._raw(), source="whisper_full", model="m", provider="groq")
        captured = {}

        def _fake_select_moments(**kwargs):
            captured["transcript_text"] = kwargs["transcript_text"]
            raise RuntimeError("stop")  # no seguir con el mega-prompt real

        with patch.dict(os.environ, {"TRANSCRIPT_SOURCE": "whisper_full", "TWO_PASS_ANALYSIS": "true"}), \
             patch("time.sleep"), \
             patch("services.analysis_cache.get_cached_analysis", return_value=None), \
             patch("services.analysis_cache.get_cached_category", return_value="podcast"), \
             patch("services.moment_selector.select_moments", side_effect=_fake_select_moments), \
             patch.object(processor, "OpenAI") as fake_openai:
            fake_openai.return_value.chat.completions.create.side_effect = RuntimeError("no llamar al modelo")
            with pytest.raises(Exception):
                processor.analyze_with_openrouter(t, {"id": "vid123", "title": "T"})

        text = captured["transcript_text"]
        assert text.splitlines()[0] == tl.LINES_PROMPT_HEADER
        assert text.splitlines()[1] == "[0-3] La enfermedad más contagiosa es el sarampión."
        assert "¿Y el COVID?" in text
        assert "]:" not in text  # no es el formato de bloques `[s-e]: texto`

        # TRANSCRIPT_LINE_STYLE=mmss vuelve al formato [mm:ss]
        captured.clear()
        with patch.dict(os.environ, {"TRANSCRIPT_SOURCE": "whisper_full", "TRANSCRIPT_LINE_STYLE": "mmss"}), \
             patch("time.sleep"), \
             patch("services.analysis_cache.get_cached_analysis", return_value=None), \
             patch("services.analysis_cache.get_cached_category", return_value="podcast"), \
             patch("services.moment_selector.select_moments", side_effect=_fake_select_moments), \
             patch.object(processor, "OpenAI") as fake_openai:
            fake_openai.return_value.chat.completions.create.side_effect = RuntimeError("no llamar al modelo")
            with pytest.raises(Exception):
                processor.analyze_with_openrouter(t, {"id": "vid123", "title": "T"})
        assert captured["transcript_text"].splitlines()[1] == "[0:00] La enfermedad más contagiosa es el sarampión."

    def test_pasada_a_keeps_blocks_with_supadata(self):
        """Con `supadata` (default) el formato compacto de bloques no cambia."""
        from services import processor
        from services.transcriber import format_transcript_for_prompt_compact

        transcript = {
            "text": "hola a todos hoy hablamos",
            "segments": [
                {"id": 0, "start": 0.0, "end": 3.0, "text": "hola a todos"},
                {"id": 1, "start": 3.0, "end": 6.0, "text": "hoy hablamos"},
            ],
            "language": "es",
        }
        captured = {}

        def _fake_select_moments(**kwargs):
            captured["transcript_text"] = kwargs["transcript_text"]
            raise RuntimeError("stop")

        env = {k: v for k, v in os.environ.items() if k != "TRANSCRIPT_SOURCE"}
        with patch.dict(os.environ, env, clear=True), \
             patch("time.sleep"), \
             patch("services.analysis_cache.get_cached_analysis", return_value=None), \
             patch("services.analysis_cache.get_cached_category", return_value="business"), \
             patch("services.moment_selector.select_moments", side_effect=_fake_select_moments), \
             patch.object(processor, "OpenAI") as fake_openai:
            fake_openai.return_value.chat.completions.create.side_effect = RuntimeError("no llamar al modelo")
            with pytest.raises(Exception):
                processor.analyze_with_openrouter(transcript, {"id": "vid123", "title": "T"})

        assert captured["transcript_text"] == format_transcript_for_prompt_compact(transcript)
        assert captured["transcript_text"].startswith("[0-6]:")


# ── Unión de tramos sin duplicados ──────────────────────────────────────────
class TestMergeChunks:
    def _chunk(self, text, t0, seg_text=None):
        words = _words_from_text(text, t0=t0, dur=0.4, gap=0.1)
        segs = [{"id": 0, "start": words[0]["start"], "end": words[-1]["end"], "text": seg_text or text}]
        return {"words": words, "segments": segs, "language": "es", "duration": words[-1]["end"] + 0.5}

    def test_words_in_overlap_appear_once(self):
        # Tramo 0: [0, 15) local; tramo 1: arranca en 10 (solape 5 s). Las
        # palabras entre 10 y 15 aparecen en ambos tramos con jitter de 50 ms.
        a = self._chunk(" ".join(f"w{i}" for i in range(30)), t0=0.0)           # 0..15 s
        b_words = [dict(w, start=w["start"] - 10.0 + 0.05, end=w["end"] - 10.0 + 0.05)
                   for w in a["words"] if w["start"] >= 10.0]
        b_words += _words_from_text(" ".join(f"x{i}" for i in range(20)), t0=5.0, dur=0.4, gap=0.1)
        b = {"words": b_words, "segments": [{"id": 0, "start": 0, "end": 15, "text": "b"}],
             "language": "es", "duration": 15.0}

        merged = transcriber.merge_chunk_transcripts([(0.0, a), (10.0, b)], overlap_sec=5.0)
        texts = [w["word"] for w in merged["words"]]
        assert len(texts) == len(set(texts)), "hay palabras duplicadas en la costura"
        assert texts[:30] == [f"w{i}" for i in range(30)]
        assert texts[30:] == [f"x{i}" for i in range(20)]
        # tiempos en la línea del video (los x arrancan en 10+5=15 s)
        assert merged["words"][30]["start"] == pytest.approx(15.0, abs=0.01)
        assert merged["language"] == "es"
        assert merged["duration"] >= merged["words"][-1]["end"]

    def test_word_exactly_on_seam_is_not_lost(self):
        # costura en 10 + 2.5 = 12.5 s; palabra centrada ahí con jitter distinto en cada tramo
        a = {"words": [_w("antes", 11.0, 11.4), _w("costura", 12.3, 12.7)],
             "segments": [], "language": "es", "duration": 15}
        b = {"words": [_w("costura", 2.35, 2.75), _w("despues", 4.0, 4.4)],
             "segments": [], "language": "es", "duration": 15}
        merged = transcriber.merge_chunk_transcripts([(0.0, a), (10.0, b)], overlap_sec=5.0)
        assert [w["word"] for w in merged["words"]] == ["antes", "costura", "despues"]

    def test_merge_and_build_keep_provider_word_order_despite_time_jitter(self):
        # Groq: "universo" con start anterior a "Star" por jitter; el orden del
        # texto manda (ordenar por tiempo daba "Star universo Wars").
        a = {"words": [_w("tipo", 1.0, 1.3), _w("Star", 1.5, 1.8), _w("universo", 1.45, 1.9), _w("Wars", 1.9, 2.2)],
             "segments": [{"id": 0, "start": 1.0, "end": 2.2, "text": "tipo Star universo Wars"}],
             "language": "es", "duration": 3}
        merged = transcriber.merge_chunk_transcripts([(0.0, a)], overlap_sec=5.0)
        assert [w["word"] for w in merged["words"]] == ["tipo", "Star", "universo", "Wars"]
        t = tl.build_full_transcript(merged, source="whisper_full", model="m")
        assert t["lines"][0]["text"] == "tipo Star universo Wars"

    def test_repeated_words_far_from_seams_are_kept(self):
        # "qué es lo que" tiene dos "que" a < 0,5 s: lejos de la costura no se deduplica
        a = {"words": [_w("qué", 1.0, 1.1), _w("es", 1.1, 1.2), _w("lo", 1.2, 1.3), _w("que", 1.3, 1.4), _w("puede", 1.4, 1.7)],
             "segments": [], "language": "es", "duration": 3}
        merged = transcriber.merge_chunk_transcripts([(0.0, a), (10.0, {"words": [], "segments": [], "duration": 3})], overlap_sec=5.0)
        assert [w["word"] for w in merged["words"]] == ["qué", "es", "lo", "que", "puede"]

    def test_single_chunk_passthrough(self):
        a = self._chunk("hola mundo", t0=0.0)
        merged = transcriber.merge_chunk_transcripts([(0.0, a)], overlap_sec=5.0)
        assert [w["word"] for w in merged["words"]] == ["hola", "mundo"]
        assert merged["segments"][0]["text"] == "hola mundo"

    def test_transcribe_full_audio_runs_chunks_and_merges(self, tmp_path):
        """Orquestación: parte, transcribe cada tramo con la cascada de W1
        (usage_task=transcript_full), detecta idioma en el 1º y une."""
        chunks = [(str(tmp_path / "c0.mp3"), 0.0), (str(tmp_path / "c1.mp3"), 600.0)]
        for path, _ in chunks:
            open(path, "wb").close()
        calls = []

        def _fake_transcribe(path, prompt=None, language=None, provider=None, usage_task="whisper"):
            calls.append({"path": path, "language": language, "usage_task": usage_task, "provider": provider})
            idx = chunks.index((path, 0.0 if path.endswith("c0.mp3") else 600.0))
            # Palabras lejos de la costura (602,5 s): en 1 s del tramo 0 y en
            # 4 s (= 604 s global) del tramo 1.
            t0 = 1.0 if idx == 0 else 4.0
            return {
                "words": _words_from_text(f"tramo{idx} palabra final", t0=t0),
                "segments": [{"id": 0, "start": t0, "end": t0 + 1.5, "text": f"Tramo{idx} palabra final."}],
                "language": "es", "duration": 605.0, "provider": "groq",
            }

        with patch("services.audio_utils.split_audio_ffmpeg", return_value=chunks), \
             patch("services.audio_utils.cleanup_chunks"), \
             patch.object(transcriber, "transcribe_with_whisper_openrouter", side_effect=_fake_transcribe):
            merged = transcriber.transcribe_full_audio(str(tmp_path / "full.m4a"), prompt="Título", max_parallel=2)

        assert [c["usage_task"] for c in calls] == ["transcript_full"] * 2
        assert calls[0]["language"] is None and calls[1]["language"] == "es"  # idioma fijado tras el 1º
        assert [w["word"] for w in merged["words"]] == ["tramo0", "palabra", "final", "tramo1", "palabra", "final"]
        assert merged["words"][3]["start"] == pytest.approx(604.0)
        assert merged["providers"] == ["groq"] and merged["n_chunks"] == 2
        assert merged["audio_seconds"] == pytest.approx(1210.0)
        assert merged["cost_usd"] > 0


# ── Flag en yt_transcript ───────────────────────────────────────────────────
class TestTranscriptSourceFlag:
    def test_default_is_supadata_and_invalid_falls_back(self):
        from services import yt_transcript
        env = {k: v for k, v in os.environ.items() if k != "TRANSCRIPT_SOURCE"}
        with patch.dict(os.environ, env, clear=True):
            assert yt_transcript.transcript_source() == "supadata"
        with patch.dict(os.environ, {"TRANSCRIPT_SOURCE": "deepgram"}):
            assert yt_transcript.transcript_source() == "supadata"
        with patch.dict(os.environ, {"TRANSCRIPT_SOURCE": "Whisper_Full"}):
            assert yt_transcript.transcript_source() == "whisper_full"

    def test_supadata_flag_does_not_touch_whisper(self):
        from services import yt_transcript
        captions = ({"text": "x", "segments": [{"id": 0, "start": 0, "end": 1, "text": "x"}], "language": "es"},
                    {"id": "abc12345678", "title": "T", "duration": 0})
        env = {k: v for k, v in os.environ.items() if k != "TRANSCRIPT_SOURCE"}
        with patch.dict(os.environ, env, clear=True), \
             patch.object(yt_transcript, "_get_captions_transcript", return_value=captions) as cap, \
             patch.object(yt_transcript, "_get_transcript_via_whisper_full") as full:
            t, info = yt_transcript.get_youtube_transcript("https://youtu.be/abc12345678")
        assert cap.called and not full.called
        assert t is captions[0] and "source" not in t and "lines" not in t

    def test_whisper_full_flag_uses_whisper_and_falls_back_to_captions(self):
        from services import yt_transcript
        captions = ({"text": "x", "segments": [], "language": "es"}, {"id": "abc12345678", "title": "T"})
        full = ({"source": "whisper_full", "lines": [{"id": 0}], "words": [], "wpm": 1, "segments": []},
                {"id": "abc12345678", "title": "T"})
        with patch.dict(os.environ, {"TRANSCRIPT_SOURCE": "whisper_full"}), \
             patch.object(yt_transcript, "_get_captions_transcript", return_value=captions) as cap, \
             patch.object(yt_transcript, "_get_transcript_via_whisper_full", return_value=full) as wf:
            t, _ = yt_transcript.get_youtube_transcript("https://youtu.be/abc12345678")
        assert wf.called and not cap.called and t["source"] == "whisper_full"

        with patch.dict(os.environ, {"TRANSCRIPT_SOURCE": "whisper_full"}), \
             patch.object(yt_transcript, "_get_captions_transcript", return_value=captions), \
             patch.object(yt_transcript, "_get_transcript_via_whisper_full", side_effect=RuntimeError("sin audio")):
            t, _ = yt_transcript.get_youtube_transcript("https://youtu.be/abc12345678")
        assert t.get("source") is None and t["source_fallback_from"] == "whisper_full"

    def test_hybrid_keeps_captions_segments_and_adds_lines(self):
        from services import yt_transcript
        cap_segments = [{"id": 0, "start": 0, "end": 3, "text": "hola a todos"}]
        captions = ({"text": "hola a todos", "segments": cap_segments, "language": "es-419"},
                    {"id": "abc12345678", "title": "T"})
        full = ({"source": "whisper_full", "lines": [{"id": 0, "start": 0, "end": 3, "text": "Hola a todos."}],
                 "words": [_w("Hola", 0, 0.3)], "wpm": 150.0, "segments": [], "model": "m", "provider": "groq"},
                {"id": "abc12345678", "title": "T"})
        with patch.dict(os.environ, {"TRANSCRIPT_SOURCE": "hybrid"}), \
             patch.object(yt_transcript, "_get_captions_transcript", return_value=captions), \
             patch.object(yt_transcript, "_get_transcript_via_whisper_full", return_value=full) as wf:
            t, _ = yt_transcript.get_youtube_transcript("https://youtu.be/abc12345678")
        assert wf.call_args.kwargs["language"] == "es-419"
        assert t["segments"] is cap_segments            # clasificador y validaciones: captions
        assert t["source"] == "hybrid" and t["lines"] == full[0]["lines"] and t["wpm"] == 150.0
        assert tl.has_full_transcript(t)

    def test_whisper_full_path_reads_cache_before_transcribing(self):
        from services import yt_transcript
        cached = {"source": "whisper_full", "lines": [{"id": 0, "start": 0, "end": 1, "text": "Hola."}],
                  "words": [], "segments": [], "wpm": 1.0, "duration": 100.0}
        with patch("services.transcript_cache.get_cached_transcript", return_value=cached) as gc, \
             patch("services.transcriber.transcribe_full_audio") as tfa, \
             patch("services.downloader.download_audio_only") as dl, \
             patch.object(yt_transcript, "get_video_metadata", return_value={"id": "v", "title": "T", "duration": 0}):
            t, info = yt_transcript._get_transcript_via_whisper_full("https://youtu.be/abc12345678", "abc12345678")
        assert t is cached and not tfa.called and not dl.called
        assert gc.call_args.kwargs["source"] == "whisper_full"
        assert info["duration"] == 101

    def test_whisper_full_path_transcribes_and_saves_cache(self, tmp_path):
        from services import yt_transcript
        raw = {
            "words": _words_from_text("hola a todos hoy hablamos del sarampión"),
            "segments": [{"id": 0, "start": 0, "end": 4, "text": "Hola a todos. Hoy hablamos del sarampión."}],
            "language": "es", "duration": 120.0, "providers": ["groq"],
            "audio_seconds": 120.0, "cost_usd": 0.0013, "elapsed_sec": 3.0, "n_chunks": 1,
        }
        saved = {}

        def _save(video_id, transcript, **kw):
            saved.update({"video_id": video_id, "transcript": transcript, **kw})
            return True

        with patch("services.transcript_cache.get_cached_transcript", return_value=None), \
             patch("services.transcript_cache.save_transcript", side_effect=_save), \
             patch("services.transcriber.transcribe_full_audio", return_value=raw) as tfa, \
             patch("services.downloader.download_audio_only", return_value=str(tmp_path / "a.m4a")), \
             patch.dict(os.environ, {"GROQ_API_KEY": "k"}), \
             patch.object(yt_transcript, "get_video_metadata", return_value={"id": "v", "title": "Wild", "duration": 0}):
            t, info = yt_transcript._get_transcript_via_whisper_full("https://youtu.be/abc12345678", "abc12345678")

        assert tfa.call_args.kwargs["prompt"] is None  # el título como prompt hace alucinar a Whisper
        assert [ln["text"] for ln in t["lines"]] == ["Hola a todos.", "Hoy hablamos del sarampión."]
        assert t["source"] == "whisper_full" and t["model"] == "whisper-large-v3-turbo"
        assert t["cost_usd"] == 0.0013 and t["n_chunks"] == 1
        assert saved["source"] == "whisper_full" and saved["model"] == "whisper-large-v3-turbo"
        assert saved["transcript"] is t
        assert info["duration"] == 121


# ── Cache por (video_id, fuente, modelo) ────────────────────────────────────
class TestTranscriptCache:
    def test_cache_key_keeps_supadata_unchanged(self):
        from services.transcript_cache import transcript_cache_key
        assert transcript_cache_key("vid") == "vid"
        assert transcript_cache_key("vid", "supadata") == "vid"
        assert transcript_cache_key("vid", "whisper_full", "whisper-large-v3-turbo") == "vid:whisper_full:whisper-large-v3-turbo"

    def test_local_cache_roundtrip_without_supabase(self, tmp_path):
        from services import transcript_cache as tc
        with patch.object(tc, "get_supabase", return_value=None), patch.object(tc, "_LOCAL_DIR", tmp_path):
            assert tc.get_cached_transcript("vid", source="whisper_full", model="m") is None
            assert tc.save_transcript("vid", {"lines": [1]}, source="whisper_full", model="m")
            assert tc.get_cached_transcript("vid", source="whisper_full", model="m") == {"lines": [1]}
            # supadata: sin Supabase no hay cache local (comportamiento de siempre)
            assert not tc.save_transcript("vid", {"segments": []})
            assert tc.get_cached_transcript("vid") is None

    def test_analysis_cache_version_is_source_aware(self):
        from services.analysis_cache import PROMPT_VERSION, effective_prompt_version
        env = {k: v for k, v in os.environ.items() if k != "TRANSCRIPT_SOURCE"}
        with patch.dict(os.environ, env, clear=True):
            assert effective_prompt_version() == PROMPT_VERSION
            assert effective_prompt_version("supadata") == PROMPT_VERSION
            assert effective_prompt_version("whisper_full") == f"{PROMPT_VERSION}+whisper_full"
        with patch.dict(os.environ, {"TRANSCRIPT_SOURCE": "hybrid"}):
            assert effective_prompt_version() == f"{PROMPT_VERSION}+hybrid"
            assert effective_prompt_version("supadata") == PROMPT_VERSION  # la fuente real manda


# ── El pipeline tolera timestamps en mm:ss ──────────────────────────────────
class TestMomentTimestamps:
    def test_viral_moment_accepts_mmss(self):
        """Red: si el modelo copia la marca `[17:10]` del transcript de W4 en
        vez de los segundos, el momento igual queda en segundos."""
        from models.schemas import ViralMoment

        m = ViralMoment(
            start_time="17:10", end_time="18:32",
            hook="h", emotional_trigger="Curiosidad",
        )
        assert (m.start_time, m.end_time) == (1030, 1112)
        assert ViralMoment(start_time="1:17:03", end_time=4630, hook="h",
                           emotional_trigger="x").start_time == 4623
        assert ViralMoment(start_time=1030.4, end_time=1112, hook="h",
                           emotional_trigger="x").start_time == 1030


# ── Usage tracker: task=transcript_full ─────────────────────────────────────
class TestUsageTask:
    def test_record_whisper_usage_task_transcript_full(self):
        from services import usage_tracker as ut
        events = []
        with patch.object(ut, "_insert_event", side_effect=events.append), \
             patch.object(ut, "get_job_context", return_value={"job_id": "job1", "user_id": "u"}):
            ut.record_whisper_usage("groq", "whisper-large-v3-turbo", 4638.0, task="transcript_full")
            ut.record_whisper_usage("groq", "whisper-large-v3-turbo", 30.0)
        assert events[0]["task"] == "transcript_full" and events[0]["event_type"] == "whisper"
        assert events[0]["estimated_cost_usd"] == pytest.approx(4638.0 / 3600 * 0.04, rel=1e-3)
        assert events[1]["task"] == "whisper"

    def test_unpunctuated_runs_detects_long_stretches(self):
        words = _words_from_text("Hola. " + " ".join(["palabra"] * 50) + " fin. Corto. " + " ".join(["x"] * 10))
        words[0]["word"] = "Hola."
        words[51]["word"] = "fin."
        words[52]["word"] = "Corto."
        runs = tl.unpunctuated_runs(words, min_run_words=40)
        assert runs == [(1, 52)]
        assert tl.unpunctuated_runs(_words_from_text(" ".join(["x"] * 100)), min_run_words=40) == [(0, 100)]

    def test_punctuate_unpunctuated_runs_touches_only_the_runs(self):
        words = _words_from_text("Hola. " + " ".join(["palabra"] * 45) + " fin. Chau.")
        words[0]["word"], words[46]["word"], words[47]["word"] = "Hola.", "fin.", "Chau."
        client = MagicMock()
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content=" ".join(["Palabra."] * 45) + " Fin."))]
        resp.usage = None
        client.chat.completions.create.return_value = resp
        out = tl.punctuate_unpunctuated_runs(words, min_run_words=40, client=client, model="cheap")
        assert len(out) == len(words)
        assert out[0]["word"] == "Hola." and out[-1]["word"] == "Chau."
        assert out[1]["word"] == "Palabra." and out[46]["word"] == "Fin."
        assert client.chat.completions.create.call_count == 1
        # sin tramos largos no se llama al modelo
        client.reset_mock()
        short = _words_from_text("Hola. Chau.")
        short[0]["word"], short[1]["word"] = "Hola.", "Chau."
        assert tl.punctuate_unpunctuated_runs(short, client=client) == short
        assert not client.chat.completions.create.called

    def test_parallel_chunks_keep_the_job_context(self, tmp_path):
        """Los tramos corren en un ThreadPoolExecutor: sin propagar el contexto,
        `usage_tracker` descarta sus eventos y el costo del Whisper no se
        atribuye al job (medido en el e2e: US$0.02 registrados de US$0.146)."""
        from context.job_context import clear_job_context, set_job_context, get_job_context

        chunks = [(str(tmp_path / f"c{i}.mp3"), i * 600.0) for i in range(3)]
        for path, _ in chunks:
            open(path, "wb").close()
        seen = []

        def _fake_transcribe(path, prompt=None, language=None, provider=None, usage_task="whisper"):
            seen.append(get_job_context().get("job_id"))
            return {"words": _words_from_text("hola mundo", t0=1.0), "segments": [],
                    "language": "es", "duration": 605.0, "provider": "groq"}

        set_job_context(job_id="job-abc", user_id="u1")
        try:
            with patch("services.audio_utils.split_audio_ffmpeg", return_value=chunks), \
                 patch("services.audio_utils.cleanup_chunks"), \
                 patch.object(transcriber, "transcribe_with_whisper_openrouter", side_effect=_fake_transcribe):
                transcriber.transcribe_full_audio(str(tmp_path / "full.m4a"), max_parallel=3)
        finally:
            clear_job_context()

        assert len(seen) == 3
        assert seen == ["job-abc"] * 3, "los tramos paralelos perdieron el contexto del job"

    def test_punctuation_fallback_keeps_the_job_context(self):
        from context.job_context import clear_job_context, set_job_context, get_job_context

        words = _words_from_text("Hola. " + " ".join(["palabra"] * 100) + " fin.")
        words[0]["word"], words[-1]["word"] = "Hola.", "fin."
        seen = []
        client = MagicMock()

        def _create(**kwargs):
            seen.append(get_job_context().get("job_id"))
            resp = MagicMock()
            resp.choices = [MagicMock(message=MagicMock(content="Palabra. " * 40))]
            resp.usage = None
            return resp

        client.chat.completions.create.side_effect = _create
        set_job_context(job_id="job-xyz")
        try:
            tl.punctuate_unpunctuated_runs(words, min_run_words=40, client=client, model="cheap")
        finally:
            clear_job_context()
        assert seen and set(seen) == {"job-xyz"}

    def test_punctuate_with_llm_aligns_and_survives_failure(self):
        words = _words_from_text("hola a todos hoy hablamos del sarampión")
        client = MagicMock()
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content="Hola a todos. Hoy hablamos del sarampión."))]
        resp.usage = None
        client.chat.completions.create.return_value = resp
        out = tl.punctuate_words_with_llm(words, language="es", client=client, model="cheap")
        assert [w["word"] for w in out] == ["Hola", "a", "todos.", "Hoy", "hablamos", "del", "sarampión."]
        assert client.chat.completions.create.call_args.kwargs["model"] == "cheap"

        client.chat.completions.create.side_effect = RuntimeError("caído")
        out = tl.punctuate_words_with_llm(words, client=client, model="cheap")
        assert [w["word"] for w in out] == [w["word"] for w in words]
