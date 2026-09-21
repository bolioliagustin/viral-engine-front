"""Rankeo con Jev (RANKER=jev): normalización de escala, fallback y flag."""
import json
import os
import urllib.error
from unittest.mock import patch

import pytest

from services import ranker_jev
from services.moment_selector import CandidateEval, score_candidate, _judge_sum


def _respuesta(gancho=3.0, retencion=2.0, compartir=1.0, confianza=0.6, tokens=800):
    return {
        "model": "jev-1.13.0",
        "answers": {
            "gancho": {"type": "score", "score": gancho, "confidence": confianza,
                       "probabilities": {"0": 0.0, "1": 0.1, "2": 0.2, "3": 0.6, "4": 0.1}},
            "retencion": {"type": "score", "score": retencion, "confidence": confianza,
                          "probabilities": {"0": 0.1, "1": 0.2, "2": 0.5, "3": 0.2, "4": 0.0}},
            "compartir": {"type": "score", "score": compartir, "confidence": confianza,
                          "probabilities": {"0": 0.2, "1": 0.6, "2": 0.2, "3": 0.0, "4": 0.0}},
        },
        "usage": {"input_tokens": tokens, "output_tokens": 48},
    }


class TestFlag:
    def test_apagado_por_defecto(self, monkeypatch):
        monkeypatch.delenv("RANKER", raising=False)
        assert ranker_is_jev_false()

    def test_se_enciende_con_ranker_jev(self, monkeypatch):
        monkeypatch.setenv("RANKER", "jev")
        assert ranker_jev.ranker_is_jev() is True

    def test_valor_desconocido_no_enciende(self, monkeypatch):
        monkeypatch.setenv("RANKER", "otro")
        assert ranker_jev.ranker_is_jev() is False


def ranker_is_jev_false() -> bool:
    return ranker_jev.ranker_is_jev() is False


class TestNormalizacionDeEscala:
    """Jev da 0..12; el ranking de W2 vive en 0..30. Sin normalizar, las
    penalizaciones (6/3/1 puntos) borrarían candidatos sanos."""

    def test_suma_maxima_mapea_al_maximo_del_juez(self, monkeypatch):
        monkeypatch.setenv("TYPESAFE_API_KEY", "k")
        with patch.object(ranker_jev, "_post", return_value=(_respuesta(4.0, 4.0, 4.0), 700)):
            r = ranker_jev.jev_rank_scores("texto del clip")
        assert r["sum_0_12"] == 12.0
        assert r["rank_score"] == 30.0

    def test_suma_media_mapea_al_medio(self, monkeypatch):
        monkeypatch.setenv("TYPESAFE_API_KEY", "k")
        with patch.object(ranker_jev, "_post", return_value=(_respuesta(2.0, 2.0, 2.0), 700)):
            r = ranker_jev.jev_rank_scores("texto del clip")
        assert r["sum_0_12"] == 6.0
        assert r["rank_score"] == 15.0  # = DELIVERY_JUDGE_MIN por defecto

    def test_devuelve_confianza_y_latencia(self, monkeypatch):
        monkeypatch.setenv("TYPESAFE_API_KEY", "k")
        with patch.object(ranker_jev, "_post", return_value=(_respuesta(confianza=0.42), 731)):
            r = ranker_jev.jev_rank_scores("texto")
        assert r["confidence_avg"] == 0.42
        assert r["latency_ms"] == 731


class TestFallback:
    """Jev nunca puede dejar un job sin ranking."""

    def test_sin_api_key_devuelve_none(self, monkeypatch):
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
        assert ranker_jev.jev_rank_scores("texto") is None

    def test_texto_vacio_devuelve_none(self, monkeypatch):
        monkeypatch.setenv("TYPESAFE_API_KEY", "k")
        assert ranker_jev.jev_rank_scores("   ") is None

    def test_error_http_devuelve_none(self, monkeypatch):
        monkeypatch.setenv("TYPESAFE_API_KEY", "k")
        err = urllib.error.HTTPError("u", 401, "no auth", {}, None)
        with patch.object(ranker_jev, "_post", side_effect=err):
            assert ranker_jev.jev_rank_scores("texto") is None

    def test_timeout_devuelve_none(self, monkeypatch):
        monkeypatch.setenv("TYPESAFE_API_KEY", "k")
        with patch.object(ranker_jev, "_post", side_effect=TimeoutError("lento")):
            assert ranker_jev.jev_rank_scores("texto") is None

    def test_respuesta_malformada_devuelve_none(self, monkeypatch):
        monkeypatch.setenv("TYPESAFE_API_KEY", "k")
        with patch.object(ranker_jev, "_post", return_value=({"answers": {}}, 10)):
            assert ranker_jev.jev_rank_scores("texto") is None


class TestReintentos:
    def test_429_reintenta_y_despues_sirve(self, monkeypatch):
        monkeypatch.setenv("TYPESAFE_API_KEY", "k")
        monkeypatch.setattr(ranker_jev.time, "sleep", lambda *_: None)
        respuestas = [urllib.error.HTTPError("u", 429, "rate", {}, None), _respuesta()]

        class _Fake:
            def __init__(self, data):
                self._data = data
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self):
                return json.dumps(self._data).encode()

        def _urlopen(req, timeout=None):
            r = respuestas.pop(0)
            if isinstance(r, Exception):
                raise r
            return _Fake(r)

        with patch.object(ranker_jev.urllib.request, "urlopen", _urlopen), \
             patch.object(ranker_jev.json, "load", lambda f: json.loads(f.read())):
            r = ranker_jev.jev_rank_scores("texto")
        assert r is not None and r["sum_0_12"] == 6.0

    def test_401_no_reintenta(self, monkeypatch):
        monkeypatch.setenv("TYPESAFE_API_KEY", "k")
        llamadas = {"n": 0}

        def _urlopen(req, timeout=None):
            llamadas["n"] += 1
            raise urllib.error.HTTPError("u", 401, "no auth", {}, None)

        with patch.object(ranker_jev.urllib.request, "urlopen", _urlopen):
            assert ranker_jev.jev_rank_scores("texto") is None
        assert llamadas["n"] == 1


class TestRankingUsaJevCuandoEsta:
    def _cand(self, **kw):
        base = dict(index=1, start_time=0.0, end_time=30.0, hook="h",
                    judge_scores={"hook": 5, "retention": 5, "shareability": 5},
                    self_score=24.0)
        base.update(kw)
        return CandidateEval(**base)

    def test_sin_jev_manda_el_juez(self):
        c = self._cand()
        assert _judge_sum(c) == 15.0

    def test_con_jev_manda_jev(self):
        c = self._cand(jev_rank_score=22.5)
        assert _judge_sum(c) == 22.5

    def test_las_penalizaciones_siguen_aplicando_sobre_la_nota_de_jev(self):
        limpio = self._cand(jev_rank_score=22.5)
        roto = self._cand(jev_rank_score=22.5, payoff_not_found=True)
        assert score_candidate(limpio) > score_candidate(roto)

    def test_candidato_sin_clip_sigue_descartado(self):
        c = self._cand(jev_rank_score=30.0, usable=False)
        assert score_candidate(c) == -1000.0

    def test_jev_rompe_empates_que_el_juez_no_puede(self):
        """Dos clips que el Juez empata (mismos enteros) quedan ordenados."""
        a = self._cand(index=1, judge_scores={"hook": 6, "retention": 5, "shareability": 5})
        b = self._cand(index=2, judge_scores={"hook": 6, "retention": 5, "shareability": 5})
        assert score_candidate(a) == score_candidate(b)  # el Juez empata
        a2 = self._cand(index=1, jev_rank_score=18.2)
        b2 = self._cand(index=2, jev_rank_score=17.6)
        assert score_candidate(a2) > score_candidate(b2)


class TestContabilidad:
    def test_registra_el_costo_con_el_precio_de_jev(self, monkeypatch):
        monkeypatch.setenv("TYPESAFE_API_KEY", "k")
        registrados = []
        from services import usage_tracker

        def _fake(**kw):
            registrados.append(kw)

        monkeypatch.setattr(usage_tracker, "record_jev_usage", _fake)
        with patch.object(ranker_jev, "_post", return_value=(_respuesta(tokens=1000), 700)):
            ranker_jev.jev_rank_scores("texto", moment_index=3)
        assert registrados and registrados[0]["input_tokens"] == 1000
        assert registrados[0]["moment_index"] == 3

    def test_precio_de_jev_en_la_tabla(self):
        from config.pricing import estimate_llm_cost_usd
        # 1M tokens de entrada = US$0.042; la salida no se factura.
        assert estimate_llm_cost_usd("jev-latest", 1_000_000, 500_000) == pytest.approx(0.042)
