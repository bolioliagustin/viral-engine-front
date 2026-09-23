"""
W18 — Caché íntegra y red de seguridad (docs/briefs/W18-cache-integra.md).

Regresión del job fb287cba: un análisis y un transcript de una corrida vieja
(sobre el doblaje en inglés) se reutilizaron después del fix de la pista de
audio, ningún candidato se ancló y el piso entregó 5 clips rotos.
Todo con mocks: nada toca Supabase ni llama a un modelo.
"""
import fnmatch
import json
import os
from unittest.mock import patch

import pytest


# ─── Supabase falso en memoria ──────────────────────────────────────────────
class _Res:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters, self.op, self.payload, self.on_conflict = [], "select", None, None

    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def delete(self):
        self.op = "delete"
        return self

    def upsert(self, payload, on_conflict="video_id"):
        self.op, self.payload, self.on_conflict = "upsert", payload, on_conflict
        return self

    def eq(self, col, val):
        self.filters.append(lambda r, c=col, v=val: r.get(c) == v)
        return self

    def like(self, col, pattern):
        glob = pattern.replace("%", "*")
        self.filters.append(lambda r, c=col, g=glob: fnmatch.fnmatchcase(str(r.get(c)), g))
        return self

    def limit(self, _n):
        return self

    def execute(self):
        if self.db.fail:
            raise RuntimeError("Supabase caído (test)")
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "upsert":
            keys = [k.strip() for k in (self.on_conflict or "video_id").split(",")]
            self.db.writes.append((self.table, dict(self.payload)))
            for i, r in enumerate(rows):
                if all(r.get(k) == self.payload.get(k) for k in keys):
                    rows[i] = dict(self.payload)
                    return _Res([rows[i]])
            rows.append(dict(self.payload))
            return _Res([self.payload])
        match = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "delete":
            self.db.tables[self.table] = [r for r in rows if r not in match]
        return _Res([dict(r) for r in match])


class FakeSupabase:
    def __init__(self, fail=False):
        self.tables: dict[str, list[dict]] = {}
        self.writes: list[tuple[str, dict]] = []
        self.fail = fail

    def table(self, name):
        return _Query(self, name)


# ─── Transcripts de prueba ──────────────────────────────────────────────────
_ES = ("Y entonces el periodista es el más puteado después del árbitro, porque "
       "cuando vos le pegás a uno que no está, es muy fácil y también hay algo "
       "de eso que tiene que ver con la pasión, pero nada es tan simple como parece.")
_EN = ("And then the journalist is the most hated after the referee, because when "
       "you hit someone that is not there it's just so easy, and there are things "
       "that we don't say, but they were there and it was not like that at all.")


def _lines(text, n=12):
    return [{"id": i, "start": i * 5.0, "end": i * 5.0 + 4.5, "text": text} for i in range(n)]


def _full_transcript(text=_ES, language="es"):
    lines = _lines(text)
    return {
        "text": " ".join(ln["text"] for ln in lines),
        "segments": [dict(ln) for ln in lines],
        "lines": lines,
        "words": [],
        "language": language,
        "duration": 60.0,
        "source": "whisper_full",
        "model": "whisper-large-v3-turbo",
    }


# ─── 1. Huella del transcript ───────────────────────────────────────────────
class TestHuella:
    def test_estable_y_sensible_al_texto(self):
        from services.transcript_cache import transcript_fingerprint

        a = _full_transcript()
        b = json.loads(json.dumps(a))  # otra instancia, mismo contenido
        b["duration"] = 999            # lo que no es texto/fuente/idioma no cambia la huella
        assert transcript_fingerprint(a) == transcript_fingerprint(b)
        assert transcript_fingerprint(a) != transcript_fingerprint(_full_transcript(_EN, "en"))
        assert transcript_fingerprint(a).startswith("whisper_full|whisper-large-v3-turbo|es||")

    def test_captions_sin_modelo_ni_pista(self):
        from services.transcript_cache import transcript_fingerprint

        caps = {"segments": _lines(_ES), "language": "es-419", "model": "x"}
        assert transcript_fingerprint(caps).startswith("supadata||es||")

    def test_effective_prompt_version_acepta_flags(self):
        from services.analysis_cache import effective_prompt_version, PROMPT_VERSION

        assert effective_prompt_version("supadata") == PROMPT_VERSION
        assert effective_prompt_version("whisper_full") == f"{PROMPT_VERSION}+whisper_full"
        v = effective_prompt_version("whisper_full", flags=["ventanas", "formatos", "", "ventanas"])
        assert v == f"{PROMPT_VERSION}+whisper_full+formatos+ventanas"
        # con el flag prendido la versión difiere de la versión con el flag apagado
        assert effective_prompt_version("supadata", flags=["ventanas"]) != effective_prompt_version("supadata")


# ─── 1–2. Análisis cacheado: huella y tono ──────────────────────────────────
def _analysis_dict():
    return {"video_title": "t", "summary": "s", "viral_moments": [], "candidates_all": []}


def _run_analysis(db, transcript, tone, calls):
    from services import processor

    def _fake_select_moments(**kwargs):
        calls.append(kwargs.get("model"))
        return _analysis_dict()

    with patch("services.analysis_cache.get_supabase", return_value=db), \
         patch("services.analysis_cache.get_cached_category", return_value="podcast"), \
         patch("services.moment_selector.select_moments", side_effect=_fake_select_moments), \
         patch.object(processor, "OpenAI"):
        return processor.analyze_with_openrouter(
            transcript, {"id": "B60BHDNFNxM", "title": "T", "duration": 60}, tone=tone,
        )


@pytest.fixture
def _two_pass_env():
    env = {"TWO_PASS_ANALYSIS": "true", "TRANSCRIPT_SOURCE": "whisper_full"}
    with patch.dict(os.environ, env):
        yield


class TestAnalisisCacheado:
    def test_regresion_fb287cba_huella_distinta_recalcula(self, _two_pass_env):
        """El análisis cacheado se hizo sobre el doblaje: la Pasada A se recalcula."""
        db, calls = FakeSupabase(), []
        _run_analysis(db, _full_transcript(_EN, "en"), "profesional", calls)   # corrida vieja
        assert len(calls) == 1
        _run_analysis(db, _full_transcript(_ES, "es"), "profesional", calls)   # tras el fix
        assert len(calls) == 2, "la Pasada A debía recalcularse con el transcript nuevo"
        rows = db.tables["analysis_cache"]
        assert len(rows) == 1, "el recálculo pisa la misma fila"
        from services.transcript_cache import transcript_fingerprint
        saved = json.loads(rows[0]["result"])
        assert saved["_transcript_fingerprint"] == transcript_fingerprint(_full_transcript(_ES, "es"))

    def test_fila_vieja_sin_huella_recalcula(self, _two_pass_env):
        from services.analysis_cache import PASADA_A_TONE, effective_prompt_version
        from config.model_tiers import get_model

        db, calls = FakeSupabase(), []
        db.tables["analysis_cache"] = [{
            "video_id": "B60BHDNFNxM", "model": get_model("analysis"), "tone": PASADA_A_TONE,
            "prompt_version": effective_prompt_version("whisper_full"),
            "result": json.dumps(_analysis_dict()), "category_detected": "podcast",
        }]
        _run_analysis(db, _full_transcript(), "profesional", calls)
        assert len(calls) == 1

    def test_dos_tonos_un_solo_calculo_de_pasada_a(self, _two_pass_env):
        db, calls = FakeSupabase(), []
        _run_analysis(db, _full_transcript(), "profesional", calls)
        _run_analysis(db, _full_transcript(), "casual", calls)
        assert len(calls) == 1, "el segundo tono debía ser cache hit"
        from services.analysis_cache import PASADA_A_TONE
        assert [r["tone"] for r in db.tables["analysis_cache"]] == [PASADA_A_TONE]

    def test_legacy_conserva_el_tono_en_la_clave(self):
        from services.analysis_cache import analysis_cache_tone, PASADA_A_TONE

        assert analysis_cache_tone("casual", two_pass=False) == "casual"
        assert analysis_cache_tone("casual", two_pass=True) == PASADA_A_TONE

    def test_anotar_candidates_all_usa_la_misma_fila(self, _two_pass_env):
        """Las notas del juez se escriben en la fila de la Pasada A (centinela + huella)."""
        import main
        from services.moment_selector import CandidateEval

        db, calls = FakeSupabase(), []
        t = _full_transcript()
        result = _analysis_dict()
        result["candidates_all"] = [{"start_time": 10.0, "end_time": 50.0, "hook": "h"}]

        def _fake_select_moments(**kwargs):
            calls.append(1)
            return result

        from services import processor
        with patch("services.analysis_cache.get_supabase", return_value=db), \
             patch("services.analysis_cache.get_cached_category", return_value="podcast"), \
             patch("services.moment_selector.select_moments", side_effect=_fake_select_moments), \
             patch.object(processor, "OpenAI"):
            processor.analyze_with_openrouter(t, {"id": "B60BHDNFNxM", "title": "T", "duration": 60})
            cand = CandidateEval(index=1, start_time=10.0, end_time=50.0, hook="h",
                                 judge_scores={"hook": 7, "retention": 6, "shareability": 5},
                                 self_score=20)
            main._annotate_candidates_all_with_judge("B60BHDNFNxM", t, [cand], {1})

        rows = db.tables["analysis_cache"]
        assert len(rows) == 1
        saved = json.loads(rows[0]["result"])
        assert saved["candidates_all"][0]["w2_selected"] is True
        assert "_transcript_fingerprint" in saved, "reescribir las notas no pierde la huella"


# ─── 3–5. Transcript cacheado: copia local, idioma ──────────────────────────
class TestTranscriptCacheado:
    def _save_local(self, tc, tmp_path, transcript):
        with patch.object(tc, "get_supabase", return_value=None), patch.object(tc, "_LOCAL_DIR", tmp_path):
            tc.save_transcript("vid", transcript, source="whisper_full", model="m")
        return tmp_path / "vid_transcript_whisper_full_m.json"

    def test_supabase_vacio_no_usa_la_copia_local(self, tmp_path):
        from services import transcript_cache as tc

        local = self._save_local(tc, tmp_path, _full_transcript())
        assert local.exists()
        with patch.object(tc, "get_supabase", return_value=FakeSupabase()), \
             patch.object(tc, "_LOCAL_DIR", tmp_path):
            assert tc.get_cached_transcript("vid", source="whisper_full", model="m") is None
        assert not local.exists(), "la copia local vieja se borra"

    def test_supabase_con_error_usa_la_copia_local(self, tmp_path):
        from services import transcript_cache as tc

        self._save_local(tc, tmp_path, _full_transcript())
        with patch.object(tc, "get_supabase", return_value=FakeSupabase(fail=True)), \
             patch.object(tc, "_LOCAL_DIR", tmp_path):
            got = tc.get_cached_transcript("vid", source="whisper_full", model="m")
        assert got is not None and got["lines"]
        assert got["fingerprint"] == tc.transcript_fingerprint(_full_transcript())

    def test_idioma_distinto_se_descarta(self, tmp_path):
        from services import transcript_cache as tc

        db = FakeSupabase()
        db.tables["transcription_cache"] = [{
            "video_id": "vid:whisper_full:m",
            "transcript": json.dumps(_full_transcript(_EN, "es")),  # etiqueta "es", texto en inglés
        }]
        with patch.dict(os.environ, {"TRANSCRIPT_LANGUAGE": "es"}), \
             patch.object(tc, "get_supabase", return_value=db), patch.object(tc, "_LOCAL_DIR", tmp_path):
            assert tc.get_cached_transcript("vid", source="whisper_full", model="m") is None
        # mismo transcript en español: pasa
        db.tables["transcription_cache"][0]["transcript"] = json.dumps(_full_transcript(_ES, "es"))
        with patch.dict(os.environ, {"TRANSCRIPT_LANGUAGE": "es"}), \
             patch.object(tc, "get_supabase", return_value=db), patch.object(tc, "_LOCAL_DIR", tmp_path):
            assert tc.get_cached_transcript("vid", source="whisper_full", model="m") is not None

    def test_sin_transcript_language_no_hay_guardia(self, tmp_path):
        from services import transcript_cache as tc

        db = FakeSupabase()
        db.tables["transcription_cache"] = [{
            "video_id": "vid:whisper_full:m", "transcript": json.dumps(_full_transcript(_EN, "en")),
        }]
        env = {k: v for k, v in os.environ.items() if k != "TRANSCRIPT_LANGUAGE"}
        with patch.dict(os.environ, env, clear=True), \
             patch.object(tc, "get_supabase", return_value=db), patch.object(tc, "_LOCAL_DIR", tmp_path):
            assert tc.get_cached_transcript("vid", source="whisper_full", model="m") is not None

    def test_dominant_language_porcentaje(self):
        from services.transcript_cache import dominant_language, language_mismatch

        lang, share, total = dominant_language(_full_transcript(_EN))
        assert lang == "en" and share > 0.8 and total >= 30
        assert "idioma dominante en" in language_mismatch(_full_transcript(_EN), "es")


# ─── 4. whisper_full no se escribe bajo la clave pelada ─────────────────────
class TestClavePelada:
    def test_whisper_full_no_va_a_la_clave_pelada(self):
        import main

        with patch("services.transcript_cache.save_transcript") as save:
            main._save_captions_transcript("vid", _full_transcript(), {"duration": 60})
        save.assert_not_called()

    def test_captions_si_van_a_la_clave_pelada(self):
        import main

        caps = {"text": "hola", "segments": _lines("hola"), "language": "es"}
        with patch("services.transcript_cache.save_transcript") as save:
            main._save_captions_transcript("vid", caps, {"duration": 60})
        save.assert_called_once()
        assert save.call_args.kwargs["video_id"] == "vid"
        assert "source" not in save.call_args.kwargs  # clave pelada

    def test_hybrid_guarda_solo_los_captions(self):
        import main

        hyb = dict(_full_transcript(), source="hybrid")
        with patch("services.transcript_cache.save_transcript") as save:
            main._save_captions_transcript("vid", hyb, {"duration": 60})
        saved = save.call_args.kwargs["transcript"]
        assert "lines" not in saved and "words" not in saved and "source" not in saved
        assert saved["segments"]


# ─── 6. Purga por video ─────────────────────────────────────────────────────
def _db_con_video(vid="B60BHDNFNxM"):
    db = FakeSupabase()
    db.tables["analysis_cache"] = [
        {"video_id": vid, "model": "m", "tone": "_pasada_a", "prompt_version": "v8+whisper_full"},
        {"video_id": "otroVideo01", "model": "m", "tone": "_pasada_a", "prompt_version": "v8"},
    ]
    db.tables["category_cache"] = [{"video_id": vid, "model": "m", "category": "podcast"}]
    db.tables["transcription_cache"] = [
        {"video_id": vid}, {"video_id": f"{vid}:whisper_full:whisper-large-v3-turbo"},
        {"video_id": "otroVideo01"},
    ]
    return db


def _archivos(tmp_path, vid="B60BHDNFNxM"):
    for name in (f"{vid}_transcript_whisper_full_m.json", f"{vid}_audio_only.m4a", "otroVideo01_audio.m4a"):
        (tmp_path / name).write_text("x")
    (tmp_path / f".{vid}_audio_chunks").mkdir()


class TestPurga:
    def test_dry_run_lista_sin_borrar(self, tmp_path, capsys):
        import importlib.util
        from services import cache_purge

        db = _db_con_video()
        _archivos(tmp_path)
        spec = importlib.util.spec_from_file_location(
            "purge_script", os.path.join(os.path.dirname(__file__), "..", "scripts", "purge-video-cache.py"),
        )
        script = importlib.util.module_from_spec(spec)
        with patch.object(cache_purge, "get_supabase", return_value=db), \
             patch.object(cache_purge, "DOWNLOADS_DIR", tmp_path):
            spec.loader.exec_module(script)
            assert script.main(["B60BHDNFNxM", "--dry-run"]) == 0

        out = capsys.readouterr().out
        assert "B60BHDNFNxM:whisper_full:whisper-large-v3-turbo" in out
        assert "B60BHDNFNxM_audio_only.m4a" in out and "borraría" in out
        assert "otroVideo01" not in out
        # nada borrado
        assert len(db.tables["analysis_cache"]) == 2
        assert len(db.tables["transcription_cache"]) == 3
        assert (tmp_path / "B60BHDNFNxM_audio_only.m4a").exists()

    def test_purga_borra_solo_ese_video(self, tmp_path):
        from services import cache_purge

        db = _db_con_video()
        _archivos(tmp_path)
        with patch.object(cache_purge, "get_supabase", return_value=db):
            report = cache_purge.purge_video_cache("B60BHDNFNxM", downloads_dir=tmp_path)
        assert not report["errors"]
        assert [r["video_id"] for r in db.tables["analysis_cache"]] == ["otroVideo01"]
        assert db.tables["category_cache"] == []
        assert [r["video_id"] for r in db.tables["transcription_cache"]] == ["otroVideo01"]
        assert sorted(p.name for p in tmp_path.iterdir()) == ["otroVideo01_audio.m4a"]

    def test_supabase_caido_no_frena_la_purga_local(self, tmp_path):
        from services import cache_purge

        _archivos(tmp_path)
        with patch.object(cache_purge, "get_supabase", return_value=FakeSupabase(fail=True)):
            report = cache_purge.purge_video_cache("B60BHDNFNxM", downloads_dir=tmp_path)
        assert report["errors"]
        assert not (tmp_path / "B60BHDNFNxM_audio_only.m4a").exists()


# ─── 8. El piso no entrega rotos ────────────────────────────────────────────
def _cand(i, *, broken=False, score=10):
    from services.moment_selector import CandidateEval

    return CandidateEval(
        index=i, start_time=i * 200.0, end_time=i * 200.0 + 40, hook=f"hook distinto {i} " + "x" * i,
        judge_scores={"hook": score / 3, "retention": score / 3, "shareability": score / 3},
        self_score=20, hook_not_found=broken, payoff_not_found=broken,
    )


class TestPisoSinRotos:
    def test_cero_sanos_diez_rotos_entrega_cero(self):
        from services.moment_selector import select_finalists

        selected, discarded = select_finalists([_cand(i, broken=True) for i in range(1, 11)], target=5)
        assert selected == []
        assert len(discarded) == 10

    def test_dos_sanos_diez_rotos_entrega_dos(self):
        from services.moment_selector import select_finalists

        cands = [_cand(i, broken=True, score=24) for i in range(1, 11)]
        cands += [_cand(11), _cand(12)]  # sanos pero bajo el umbral: entran por el piso
        selected, _ = select_finalists(cands, target=5)
        assert sorted(c.index for c in selected) == [11, 12]

    def test_bad_segment_tampoco_rellena(self):
        from services.moment_selector import select_finalists

        c = _cand(1)
        c.bad_segment = True
        selected, _ = select_finalists([c], target=1)
        assert selected == []


# ─── 9. Sin recorte por palabras del hook ───────────────────────────────────
class TestSinRecortePorHook:
    def _moment(self, first="", last=""):
        from types import SimpleNamespace

        verification = SimpleNamespace(first_phrase_in_audio=first, last_phrase_in_audio=last)
        return SimpleNamespace(hook="pongan el grabador", viral_overlay="", verification=verification)

    def _words(self):
        # planteo largo antes del hook: el "Head filler trim" lo recortaría
        toks = ("Maradona nos llama y dice. Pongan el grabador que les cuento todo lo que pasó "
                "en Nápoles aquella noche del noventa con la gente").split()
        return [{"word": w, "start": i * 0.6, "end": i * 0.6 + 0.5} for i, w in enumerate(toks)] + [
            {"word": f"palabra{i}", "start": 20 + i * 0.6, "end": 20 + i * 0.6 + 0.5} for i in range(40)
        ]

    def test_anclaje_fallido_con_frases_no_recorta(self, tmp_path):
        import main

        with patch.object(main, "cut_clip") as cut:
            out = main._refine_bounds_legacy(
                clip_words=self._words(), clip_duration=45.0, clip_segments_whisper=[],
                moment=self._moment(first="Maradona nos llama", last="con la gente"),
                precut_path=tmp_path / "p.mp4", video_id="vid", moment_index=1,
                whisper_timestamps_suspect=False,
            )
        assert out["snap_trim_start"] == 0
        assert out["clip_duration"] == 45.0
        assert out["anchor_failed"] is True
        cut.assert_not_called()

    def test_sin_frases_sigue_el_refinamiento_legacy(self, tmp_path):
        import main

        with patch.object(main, "cut_clip"):
            out = main._refine_bounds_legacy(
                clip_words=self._words(), clip_duration=45.0, clip_segments_whisper=[],
                moment=self._moment(), precut_path=tmp_path / "p.mp4", video_id="vid",
                moment_index=1, whisper_timestamps_suspect=False,
            )
        assert out["anchor_failed"] is False


# ─── 7. Cortacircuitos ──────────────────────────────────────────────────────
def _moment_dict(i):
    start = i * 200
    return {
        "start_time": start, "end_time": start + 40, "hook": f"hook número {i} " + "y" * i,
        "emotional_trigger": "curiosidad", "viral_overlay": f"OVERLAY {i}",
        "scores": {"hook": 8, "retention": 8, "shareability": 8},
        "verification": {"first_phrase_in_audio": "hola", "last_phrase_in_audio": "chau"},
        "content_pieces": {},
    }


class _JobHarness:
    """Corre `_process_job_inner` con transcript, Pasada A, evaluación y
    entrega mockeados. `broken_by_attempt[i]` dice si los candidatos del
    intento i salen sin anclar."""

    def __init__(self, broken_by_attempt, n_moments=8):
        self.broken_by_attempt = broken_by_attempt
        self.n_moments = n_moments
        self.transcripts = 0
        self.analyses = 0
        self.prepared_per_attempt = []

    def run(self, job_data=None):
        import main
        from models.schemas import AnalysisResult

        def _transcript(_url):
            self.transcripts += 1
            self.prepared_per_attempt.append(0)
            return _full_transcript(), {"id": "B60BHDNFNxM", "title": "T", "duration": 3000}

        def _analysis(*_a, **_k):
            self.analyses += 1
            return AnalysisResult(video_title="t", summary="s",
                                  viral_moments=[_moment_dict(i) for i in range(1, self.n_moments + 1)])

        def _prepare(moment, idx, **_k):
            attempt = len(self.prepared_per_attempt) - 1
            self.prepared_per_attempt[attempt] += 1
            broken = self.broken_by_attempt[attempt]
            return main._PreparedClip(
                ok=True, clip_duration=40.0, clip_text_final="texto del clip", wps_val=2.5,
                hook_not_found=broken, payoff_not_found=broken,
            )

        self.mocks = {}
        patches = [
            patch("services.yt_transcript.get_video_metadata", return_value={"duration": 3000}),
            patch("services.yt_transcript.get_youtube_transcript", side_effect=_transcript),
            patch("services.processor.analyze_with_openrouter", side_effect=_analysis),
            patch("services.scorer.judge_moment_scores",
                  return_value={"hook": 9, "retention": 9, "shareability": 9, "reasoning": ""}),
            patch("services.ranker_jev.ranker_is_jev", return_value=False),
            patch("services.supabase_client.update_job_progress"),
            patch.object(main, "_select_download_strategy", return_value="ninguna"),
            patch.object(main, "_log_download_strategy"),
            patch.object(main, "_prepare_moment_clip", side_effect=_prepare),
            patch.object(main, "get_supabase", return_value=None),
            patch.object(main, "cleanup_all"),
            patch.object(main, "cleanup_clips"),
            patch.object(main, "_save_captions_transcript"),
            patch.object(main, "_annotate_candidates_all_with_judge"),
            patch.object(main, "record_download_usage"),
        ]
        named = {
            "status": patch.object(main, "update_job_status"),
            "error": patch.object(main, "update_job_error"),
            "purge": patch("services.cache_purge.purge_video_cache"),
            "usage": patch.object(main, "finalize_job_usage"),
            "log": patch.object(main, "_log_cortacircuitos"),
            "deliver": patch.object(main, "_deliver_moment", return_value=True),
        }
        for p in patches:
            p.start()
        for k, p in named.items():
            self.mocks[k] = p.start()
        try:
            main.process_job(job_data or {
                "id": "job-w18", "videoUrl": "https://youtu.be/B60BHDNFNxM", "userId": None,
            })
        finally:
            for p in patches + list(named.values()):
                p.stop()
        return self


class TestCortacircuitos:
    def test_motivo_4_de_5(self):
        import main

        rotos = [_cand(i, broken=True) for i in range(1, 5)]
        assert main.cortacircuitos_motivo(rotos[:3], completo=False) is None
        assert "4 de los primeros 4" in main.cortacircuitos_motivo(rotos, completo=False)
        mezcla = [_cand(1), _cand(2)] + [_cand(i, broken=True) for i in range(3, 6)]
        assert main.cortacircuitos_motivo(mezcla, completo=False) is None  # 3 de 5

    def test_motivo_mitad_del_total_con_minimo_4(self):
        import main

        sanos_primero = [_cand(i) for i in range(1, 6)] + [_cand(i, broken=True) for i in range(6, 11)]
        assert main.cortacircuitos_motivo(sanos_primero, completo=False) is None
        assert "5 de 10" in main.cortacircuitos_motivo(sanos_primero, completo=True)
        pocos = [_cand(1), _cand(2), _cand(3, broken=True), _cand(4, broken=True)]
        assert main.cortacircuitos_motivo(pocos, completo=True) is None  # 50 % pero < 4 rotos

    def test_dispara_rehace_una_vez_y_el_segundo_disparo_falla(self):
        import main

        h = _JobHarness(broken_by_attempt=[True, True]).run()
        assert h.transcripts == 2 and h.analyses == 2, "transcript y Pasada A se rehacen una vez"
        assert h.prepared_per_attempt == [4, 4], "corta a los 4 rotos, sin evaluar los 8"
        h.mocks["purge"].assert_called_once_with("B60BHDNFNxM")
        h.mocks["error"].assert_called_once_with("job-w18", main.CORTACIRCUITOS_ERROR)
        assert ("job-w18", "completed") not in [c.args[:2] for c in h.mocks["status"].call_args_list]
        assert h.mocks["log"].call_count == 2
        h.mocks["usage"].assert_called_once()  # el uso de los dos intentos va al mismo rollup
        # se dispara antes de select_finalists: nada entregado, 0 filas en content_results
        h.mocks["deliver"].assert_not_called()

    def test_dispara_y_el_reintento_sano_completa(self):
        h = _JobHarness(broken_by_attempt=[True, False]).run()
        assert h.transcripts == 2
        assert h.prepared_per_attempt == [4, 8]
        h.mocks["purge"].assert_called_once()
        h.mocks["error"].assert_not_called()
        assert ("job-w18", "completed") in [c.args[:2] for c in h.mocks["status"].call_args_list]

    def test_job_sano_no_dispara(self):
        h = _JobHarness(broken_by_attempt=[False]).run()
        assert h.transcripts == 1 and h.prepared_per_attempt == [8]
        h.mocks["purge"].assert_not_called()
        h.mocks["log"].assert_not_called()

    def test_la_purga_del_cortacircuitos_borra_transcript_y_audio(self, tmp_path):
        """Lo que invalida el cortacircuitos es lo mismo que el script de purga,
        audio incluido: el reintento no puede releer el transcript envenenado."""
        from services import cache_purge

        db = _db_con_video()
        _archivos(tmp_path)
        with patch.object(cache_purge, "get_supabase", return_value=db), \
             patch.object(cache_purge, "DOWNLOADS_DIR", tmp_path):
            cache_purge.purge_video_cache("B60BHDNFNxM")
        assert not any(r["video_id"].startswith("B60BHDNFNxM") for r in db.tables["transcription_cache"])
        assert not (tmp_path / "B60BHDNFNxM_audio_only.m4a").exists()
        assert not (tmp_path / "B60BHDNFNxM_transcript_whisper_full_m.json").exists()
