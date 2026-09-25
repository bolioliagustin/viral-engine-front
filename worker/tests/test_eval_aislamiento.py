"""
W19 (PLAN_MEJORA §4.1): los tiers `seleccion` y `e2e` no leen ni escriben
la caché de análisis/categoría y no escriben ninguna caché de producción.

Se reemplaza Supabase por un cliente falso que anota cada tabla y operación,
y se corre el camino real (processor.analyze_with_openrouter) con el LLM
simulado. Así el test también atrapa un camino de escritura nuevo que no
pase por las funciones parcheadas.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

WORKER_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(WORKER_DIR))
sys.path.insert(0, str(WORKER_DIR / "eval"))

import aislamiento  # noqa: E402

TABLAS_CACHE = {"analysis_cache", "category_cache", "transcription_cache"}


class _Query:
    def __init__(self, registro, tabla):
        self.registro, self.tabla = registro, tabla

    def _anotar(self, op):
        self.registro.append((self.tabla, op))
        return self

    def select(self, *a, **k):
        return self._anotar("select")

    def upsert(self, *a, **k):
        return self._anotar("upsert")

    def insert(self, *a, **k):
        return self._anotar("insert")

    def update(self, *a, **k):
        return self._anotar("update")

    def delete(self, *a, **k):
        return self._anotar("delete")

    def __getattr__(self, _):  # eq, limit, order, …
        return lambda *a, **k: self

    def execute(self):
        return SimpleNamespace(data=[])


class _FakeSupabase:
    def __init__(self):
        self.registro = []

    def table(self, nombre):
        return _Query(self.registro, nombre)


@pytest.fixture
def fake_sb(monkeypatch):
    sb = _FakeSupabase()
    from services import analysis_cache, supabase_client, transcript_cache
    for mod in (analysis_cache, transcript_cache, supabase_client):
        monkeypatch.setattr(mod, "get_supabase", lambda: sb)
    monkeypatch.delenv("EVAL_CACHE_PRODUCCION", raising=False)
    monkeypatch.setenv("EVAL_DRY_RUN", "1")
    return sb


def _momento(i, start):
    return {
        "start_time": start, "end_time": start + 40, "hook": f"Hook {i}",
        "emotional_trigger": "humor", "viral_overlay": "OVERLAY",
        "scores": {"hook": 8, "retention": 7, "shareability": 7 + i % 2},
        "verification": {"first_phrase_in_audio": "hola", "last_phrase_in_audio": "chau"},
    }


class _FakeOpenAI:
    def __init__(self, *a, **k):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        if kwargs.get("response_format"):
            contenido = json.dumps({
                "video_title": "t", "summary": "s",
                "viral_moments": [_momento(i, 100 + 200 * i) for i in range(6)],
            })
        else:
            contenido = "podcast"
        usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=500, total_tokens=1500,
                                completion_tokens_details=None)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=contenido), finish_reason="stop")],
            usage=usage,
        )


def _transcript():
    lines = [{"id": i, "start": i * 10.0, "end": i * 10.0 + 9, "text": f"Frase {i}. hola chau"} for i in range(150)]
    return {
        "segments": lines, "lines": lines, "words": [], "language": "es",
        "duration": 1500.0, "source": "whisper_full", "model": "whisper-large-v3-turbo",
    }


def _cache_ops(sb):
    return [(t, op) for t, op in sb.registro if t in TABLAS_CACHE]


class TestSinCacheDeProduccion:
    def test_pasada_a_no_toca_cache(self, fake_sb, monkeypatch):
        import seleccion
        from services import processor
        monkeypatch.setattr(processor, "OpenAI", _FakeOpenAI)

        restaurar = seleccion._instalar_capturas()
        try:
            with aislamiento.sin_cache_de_produccion() as intentos:
                r = seleccion.correr_pasada_a(_transcript(), {"id": "VIDX", "title": "t", "duration": 1500})
                intentos = dict(intentos)
        finally:
            restaurar()

        assert _cache_ops(fake_sb) == []
        # se intentó consultar y guardar, y el aislamiento lo cortó
        assert intentos.get("get_cached_analysis") == 1
        assert intentos.get("get_cached_category") == 1
        assert intentos.get("save_analysis") == 1
        assert intentos.get("save_category") == 1
        assert r["categoria"] == "podcast"
        assert len(r["candidatos"]) == 6
        assert all(c["rank_score"] is not None for c in r["candidatos"])
        assert r["costo_usd"] > 0

    def test_control_sin_aislamiento_si_toca_la_cache(self, fake_sb, monkeypatch):
        """Sin el aislamiento, el mismo camino consulta y escribe las cachés (el test de arriba no es trivial)."""
        from services import processor
        monkeypatch.setattr(processor, "OpenAI", _FakeOpenAI)
        processor.analyze_with_openrouter(_transcript(), {"id": "VIDX", "title": "t", "duration": 1500})
        tablas = {t for t, _ in _cache_ops(fake_sb)}
        assert {"analysis_cache", "category_cache"} <= tablas

    def test_save_transcript_solo_local(self, fake_sb, monkeypatch, tmp_path):
        monkeypatch.setattr(aislamiento, "EVAL_TRANSCRIPTS_DIR", tmp_path)
        from services import transcript_cache as tc
        with aislamiento.sin_cache_de_produccion():
            tc.save_transcript("VIDX", _transcript(), language="es", duration_seconds=1500,
                               source="whisper_full", model="whisper-large-v3-turbo")
            tc.save_transcript("VIDX", {"segments": []})  # captions: ni local ni Supabase
            leido = tc.get_cached_transcript("VIDX", source="whisper_full", model="whisper-large-v3-turbo")
        assert _cache_ops(fake_sb) == []
        assert (tmp_path / "VIDX.json").exists()
        assert leido["duration"] == 1500.0

    def test_lectura_de_transcript_en_supabase_permitida(self, fake_sb, monkeypatch, tmp_path):
        monkeypatch.setattr(aislamiento, "EVAL_TRANSCRIPTS_DIR", tmp_path)
        from services import transcript_cache as tc
        with aislamiento.sin_cache_de_produccion():
            tc.get_cached_transcript("OTRO", source="whisper_full", model="whisper-large-v3-turbo")
        assert _cache_ops(fake_sb) == [("transcription_cache", "select")]

    def test_parches_se_restauran(self, fake_sb):
        from services import analysis_cache as ac
        original = ac.save_analysis
        with aislamiento.sin_cache_de_produccion():
            assert ac.save_analysis is not original
        assert ac.save_analysis is original

    def test_env_desactiva(self, fake_sb, monkeypatch):
        monkeypatch.setenv("EVAL_CACHE_PRODUCCION", "1")
        from services import analysis_cache as ac
        original = ac.save_analysis
        with aislamiento.sin_cache_de_produccion():
            assert ac.save_analysis is original


class TestTierSeleccionCompleto:
    def test_correr_seleccion_sin_red_ni_cache(self, fake_sb, monkeypatch, tmp_path):
        import referencias as refs
        import seleccion
        from services import processor
        monkeypatch.setattr(processor, "OpenAI", _FakeOpenAI)
        monkeypatch.setattr(aislamiento, "EVAL_TRANSCRIPTS_DIR", tmp_path / "tr")
        monkeypatch.setattr(refs, "REFERENCIAS_DIR", tmp_path / "refs")
        aislamiento.guardar_transcript_local("VIDX", _transcript(), {"id": "VIDX", "title": "t", "duration": 1500})
        doc = refs.documento_vacio("VIDX", "video_x", 1500.0)
        doc["momentos"] = [
            {"id": "R01", "inicio": 95, "fin": 150, "nucleo_inicio": 105, "nucleo_fin": 135, "tipo": "anécdota",
             "calidad": "A", "por_que": "x", "autor": "t", "validado_por": "agustin", "fecha": "2026-09-23"},
            {"id": "R02", "inicio": 690, "fin": 760, "nucleo_inicio": 700, "nucleo_fin": 750, "tipo": "anécdota",
             "calidad": "A", "por_que": "x", "autor": "t", "validado_por": "agustin", "fecha": "2026-09-23"},
        ]
        refs.guardar_referencias(doc)

        corrida = seleccion.correr_seleccion(
            [{"id": "video_x", "youtube_id": "VIDX", "formato": "charla"}],
            reps=2, incluir_borradores=False, workers=2, log=lambda *_: None,
        )
        assert _cache_ops(fake_sb) == []
        v = corrida["videos"][0]
        assert len(v["reps"]) == 2
        # candidatos en 100–140, 300–340, 500–540, 700–740…: R01 completa, R02 parcial (termina 740 < 748)
        assert v["agregado"]["recall_completo"]["media"] == 0.5
        assert v["agregado"]["recall_completo"]["desvio"] == 0.0
        assert corrida["aislamiento"]["activo"] is True
        assert corrida["aislamiento"]["intentos_bloqueados"]["save_analysis"] == 2
        assert corrida["costo_total_usd"] > 0

        # recalcular con Referencias nuevas no llama a la API
        doc["momentos"][1]["nucleo_fin"] = 740
        recalculada = seleccion.calcular_metricas(
            json.loads(json.dumps(corrida)), incluir_borradores=False, docs={"VIDX": doc},
        )
        assert recalculada["videos"][0]["agregado"]["recall_completo"]["media"] == 1.0


class TestTierE2E:
    def test_e2e_corre_aislado(self, fake_sb, monkeypatch, capsys):
        """El loop del e2e envuelve cada job en el aislamiento: lo que el pipeline intente contra las cachés no llega."""
        import run_golden_set as rgs
        from eval_metrics import aggregate_e2e_results, check_e2e_thresholds

        def _job_falso(video, **kwargs):
            from services import analysis_cache as ac
            from services import transcript_cache as tc
            assert ac.get_cached_analysis("VIDX", "m", "profesional") is None
            ac.save_analysis(video_id="VIDX", model="m", result={}, tone="profesional")
            ac.save_category("VIDX", "m", "podcast")
            tc.save_transcript("VIDX", {"segments": []})
            return {"id": video["id"], "youtube_id": "VIDX", "ok": True, "clips": [], "errors": []}

        monkeypatch.setattr(rgs, "evaluate_video_e2e", _job_falso)
        rgs._main_e2e(
            [{"id": "video_x", "youtube_id": "VIDX"}], {}, json_mode=True, budget_sec=10,
            aggregate=aggregate_e2e_results, check=check_e2e_thresholds, blocking=False,
        )
        assert _cache_ops(fake_sb) == []
        salida = json.loads(capsys.readouterr().out)
        assert salida["aislamiento"]["activo"] is True
        assert salida["aislamiento"]["intentos_bloqueados"]["save_analysis"] == 1


class TestRutasW18:
    """Rutas nuevas de W18 (huella, purga, captions): ninguna escritura llega a Supabase."""

    def test_extremo_a_extremo_rutas_w18(self, fake_sb, monkeypatch, tmp_path):
        import main
        from services import analysis_cache as ac
        from services import cache_purge as cp
        from services import processor
        from services import transcript_cache as tc

        monkeypatch.setattr(processor, "OpenAI", _FakeOpenAI)
        monkeypatch.setattr(aislamiento, "EVAL_TRANSCRIPTS_DIR", tmp_path / "tr")
        monkeypatch.setattr(cp, "DOWNLOADS_DIR", tmp_path / "downloads")
        monkeypatch.setattr(cp, "get_supabase", lambda: fake_sb)
        (tmp_path / "downloads").mkdir()
        (tmp_path / "downloads" / "VIDX_transcript_whisper_full.json").write_text("{}")
        transcript = _transcript()
        aislamiento.guardar_transcript_local("VIDX", transcript, {"id": "VIDX"})

        with aislamiento.sin_cache_de_produccion() as intentos:
            # Huella: la consulta con fingerprint=… tampoco sale
            huella = tc.transcript_fingerprint(transcript)
            assert ac.get_cached_analysis("VIDX", "m", "profesional", fingerprint=huella) is None
            assert ac.get_cached_analysis_row("VIDX", "m", "profesional", fingerprint=huella) is None
            # Pasada A completa (usa la huella por dentro)
            processor.analyze_with_openrouter(transcript, {"id": "VIDX", "title": "t", "duration": 1500})
            # Paso S3 de main: captions a la clave pelada
            main._save_captions_transcript("VIDX", {"segments": [], "source": "supadata", "language": "es"}, {"duration": 1500})
            # Cortacircuitos: purga con los defaults (include_supabase=True)
            reporte = cp.purge_video_cache("VIDX")
            # Después de la purga, la copia local del eval no se sirve…
            assert tc.get_cached_transcript("VIDX", source="whisper_full", model="whisper-large-v3-turbo") is None
            # …hasta que el transcript rehecho se guarda (solo local)
            tc.save_transcript("VIDX", transcript, source="whisper_full", model="whisper-large-v3-turbo")
            assert tc.get_cached_transcript("VIDX", source="whisper_full", model="whisper-large-v3-turbo") is not None
            intentos = dict(intentos)

        escrituras = [(t, op) for t, op in fake_sb.registro if op != "select"]
        assert escrituras == []
        assert not {t for t, _ in fake_sb.registro} & {"analysis_cache", "category_cache"}
        assert reporte["supabase"] == {}
        assert not (tmp_path / "downloads" / "VIDX_transcript_whisper_full.json").exists()  # la purga local sí corre
        assert intentos["purge_video_cache"] == 1
        assert intentos["save_transcript"] == 2


class TestRepeticionesInvalidas:
    def test_fallback_a_mega_prompt_invalida_la_rep(self, fake_sb, monkeypatch):
        import seleccion
        from services import moment_selector, processor
        monkeypatch.setattr(processor, "OpenAI", _FakeOpenAI)

        def _falla(*a, **k):
            raise RuntimeError("402")
        monkeypatch.setattr(moment_selector, "select_moments", _falla)
        restaurar = seleccion._instalar_capturas()
        try:
            with aislamiento.sin_cache_de_produccion():
                with pytest.raises(RuntimeError, match="mega-prompt"):
                    seleccion.correr_pasada_a(_transcript(), {"id": "VIDX", "title": "t", "duration": 1500})
        finally:
            restaurar()

    def test_completar_rehace_solo_las_fallidas(self, fake_sb, monkeypatch, tmp_path):
        import referencias as refs
        import seleccion
        from services import processor
        monkeypatch.setattr(processor, "OpenAI", _FakeOpenAI)
        monkeypatch.setattr(aislamiento, "EVAL_TRANSCRIPTS_DIR", tmp_path / "tr")
        monkeypatch.setattr(refs, "REFERENCIAS_DIR", tmp_path / "refs")
        aislamiento.guardar_transcript_local("VIDX", _transcript(), {"id": "VIDX", "title": "t", "duration": 1500})
        doc = refs.documento_vacio("VIDX", "video_x", 1500.0)
        doc["momentos"] = [{"id": "R01", "inicio": 95, "fin": 150, "nucleo_inicio": 105, "nucleo_fin": 135,
                            "tipo": "anécdota", "calidad": "A", "por_que": "x", "autor": "t",
                            "validado_por": "agustin", "fecha": "2026-09-23"}]
        refs.guardar_referencias(doc)
        buena = {"rep": 1, "candidatos": [{"start_time": 0, "end_time": 30, "rank_score": 1}], "costo_usd": 0.5}
        corrida = {"reps": 2, "incluir_borradores": False, "videos": [{
            "id": "video_x", "youtube_id": "VIDX", "duracion_sec": 1500.0,
            "reps": [buena, {"rep": 2, "error": "APIStatusError: 402"}],
        }]}
        out = seleccion.completar_corrida(corrida, [{"id": "video_x", "youtube_id": "VIDX"}], workers=1, log=lambda *_: None)
        reps = out["videos"][0]["reps"]
        assert reps[0]["costo_usd"] == 0.5 and not reps[1].get("error")
        assert out["videos"][0]["agregado"]["recall_completo"]["n"] == 2
        assert _cache_ops(fake_sb) == []
