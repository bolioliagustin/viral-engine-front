"""
W19: Referencias (esquema, fusión, markdown de validación) y métricas de
cobertura contra Referencias. Todo sin red.
"""
import copy
import sys
from pathlib import Path

import pytest

WORKER_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(WORKER_DIR))
sys.path.insert(0, str(WORKER_DIR / "eval"))

import eval_metrics as em  # noqa: E402
import referencias as refs  # noqa: E402


def _ref(rid, ni, nf, calidad="A", ini=None, fin=None, **extra):
    return {
        "id": rid, "inicio": ini if ini is not None else ni - 5, "fin": fin if fin is not None else nf + 5,
        "nucleo_inicio": ni, "nucleo_fin": nf, "tipo": "anécdota", "calidad": calidad,
        "por_que": "porque sí", "autor": "claude", "validado_por": None, "fecha": "2026-09-23",
        **extra,
    }


def _c(start, end, rank=None):
    d = {"start_time": start, "end_time": end}
    if rank is not None:
        d["rank_score"] = rank
    return d


# ─── Métricas ────────────────────────────────────────────────────────────────

class TestClasificacion:
    def test_contenido_con_tolerancia(self):
        r = _ref("R1", 100, 160)
        assert em.clasificar_referencia(r, [_c(90, 170)]) == "completa"
        # arranca 2 s tarde y termina 2 s antes: sigue contando (±2 s)
        assert em.clasificar_referencia(r, [_c(102, 158)]) == "completa"
        # 3 s tarde: ya no
        assert em.clasificar_referencia(r, [_c(103, 170)]) != "completa"

    def test_parcial(self):
        r = _ref("R1", 100, 160)
        # cubre 40 de 60 s (67 %): parcial, no completa
        assert em.clasificar_referencia(r, [_c(120, 200)]) == "parcial"
        assert em.cobertura_nucleo(_c(120, 200), r) == pytest.approx(40 / 60)

    def test_ausente_si_cubre_poco(self):
        r = _ref("R1", 100, 160)
        assert em.clasificar_referencia(r, [_c(150, 200)]) == "ausente"
        assert em.clasificar_referencia(r, []) == "ausente"

    def test_historia_partida(self):
        r = _ref("R1", 100, 160)
        # dos candidatos que juntos cubren 100 % pero ninguno la contiene
        assert em.clasificar_referencia(r, [_c(90, 130), _c(130, 170)]) == "partida"
        # juntos cubren solo 70 %: no es partida (queda parcial por el de 40 s)
        assert em.clasificar_referencia(r, [_c(90, 110), _c(128, 160)]) == "parcial"

    def test_un_solo_candidato_parcial_no_es_partida(self):
        r = _ref("R1", 100, 160)
        assert em.clasificar_referencia(r, [_c(90, 150)]) == "parcial"


class TestCuartosYExclusion:
    def test_fuera_de_cuarto(self):
        # video de 400 s: cuartos de 100 s; nada en el último
        cands = [_c(10, 40), _c(20, 60), _c(150, 180), _c(210, 240)]
        assert em.candidatos_por_cuarto(cands, 400) == [2, 1, 1, 0]
        m = em.metricas_referencias(cands, [], duracion_sec=400)
        assert m["min_cuarto"] == 0.0
        assert m["candidatos_por_cuarto"] == [2, 1, 1, 0]

    def test_cuarto_por_punto_medio_y_borde(self):
        assert em.candidatos_por_cuarto([_c(90, 130)], 400) == [0, 1, 0, 0]
        assert em.candidatos_por_cuarto([_c(390, 400)], 400) == [0, 0, 0, 1]

    def test_en_exclusion(self):
        excluir = [{"inicio": 3100, "fin": 3168}]
        assert em.en_exclusion(_c(3110, 3160), excluir)          # 100 % adentro
        assert not em.en_exclusion(_c(3060, 3120), excluir)      # 20 de 60 s
        m = em.metricas_referencias([_c(3110, 3160), _c(0, 30)], [], duracion_sec=6000, excluir=excluir)
        assert m["candidatos_en_exclusion"] == 1


class TestMetricasReferencias:
    def test_recall_a_y_ab(self):
        r = [_ref("R1", 100, 160), _ref("R2", 300, 330), _ref("R3", 500, 520, calidad="B")]
        cands = [_c(95, 165), _c(310, 400), _c(495, 525)]
        m = em.metricas_referencias(cands, r, duracion_sec=800)
        assert m["recall_completo"] == 0.5          # R1 sí, R2 parcial
        assert m["recall_completo_ab"] == pytest.approx(2 / 3, abs=1e-3)
        assert m["recall_parcial"] == 1.0
        assert m["estado_por_referencia"] == {"R1": "completa", "R2": "parcial", "R3": "completa"}

    def test_precision_ref_usa_ranking(self):
        r = [_ref("R1", 100, 160)]
        cands = [_c(0, 30, rank=10), _c(95, 165, rank=25), _c(400, 430, rank=20)]
        m = em.metricas_referencias(cands, r, duracion_sec=800, k_precision=(1, 2))
        assert m["precision_ref@1"] == 1.0
        assert m["precision_ref@2"] == 0.5

    def test_sin_referencias_a(self):
        m = em.metricas_referencias([_c(0, 30)], [_ref("R1", 100, 160, calidad="B")], duracion_sec=800)
        assert m["recall_completo"] is None
        assert m["recall_completo_ab"] == 0.0


class TestCapturaDeLoMejor:
    def test_con_etiquetas_solo_cuenta_posteables(self):
        r = [_ref("R1", 100, 160), _ref("R2", 300, 330)]
        entregados = [_c(95, 165), _c(295, 335)]
        out = em.captura_de_lo_mejor(r, entregados, posteable=[True, False])
        assert out["captura_de_lo_mejor"] == 0.5
        assert out["captura_tipo"] == "posteable"
        assert out["captura_ids"] == ["R1"]

    def test_sin_etiquetas_marca_la_version_degradada(self):
        r = [_ref("R1", 100, 160), _ref("R2", 300, 330)]
        out = em.captura_de_lo_mejor(r, [_c(95, 165), _c(295, 335)])
        assert out["captura_de_lo_mejor"] == 1.0
        assert out["captura_tipo"] == "contenido_en_entregado_sin_etiquetas"


class TestAgregados:
    def test_media_y_desvio_entre_reps(self):
        reps = [
            {"metricas": {"recall_completo": 0.2, "estado_por_referencia": {"R1": "completa"}}},
            {"metricas": {"recall_completo": 0.4, "estado_por_referencia": {"R1": "parcial"}}},
            {"metricas": None, "error": "boom"},
        ]
        agg = em.agregar_repeticiones(reps)
        assert agg["recall_completo"]["media"] == pytest.approx(0.3)
        assert agg["recall_completo"]["n"] == 2
        assert agg["recall_completo"]["desvio"] == pytest.approx(0.1414, abs=1e-3)
        assert agg["completa_en_reps"] == {"R1": 1}


# ─── Referencias: esquema, fusión, markdown ─────────────────────────────────

def _doc(momentos, excluir=None):
    d = refs.documento_vacio("VID123", "charla_x", 1000.0)
    d["momentos"] = momentos
    d["excluir"] = excluir or []
    return d


class TestEsquema:
    def test_documento_valido(self):
        assert refs.validar_documento(_doc([_ref("R01", 100, 160)])) == []

    def test_errores(self):
        malo = _ref("R01", 100, 160, ini=120)  # núcleo arranca antes del tramo
        malo["tipo"] = "chiste"
        errores = refs.validar_documento(_doc([malo, _ref("R01", 200, 230, calidad="C")]))
        texto = " ".join(errores)
        assert "nucleo_inicio" in texto and "tipo" in texto and "duplicado" in texto and "calidad" in texto

    def test_semilla_commiteada_valida(self):
        doc = refs.cargar_referencias("B60BHDNFNxM")
        assert doc is not None
        assert refs.validar_documento(doc) == []
        ids_semilla = {m["id"] for m in doc["momentos"] if m.get("autor") == "claude-anexo-b"}
        assert len(ids_semilla) == 25
        assert doc["excluir"][0]["inicio"] == 3100


class TestFusion:
    def test_no_duplica_y_existente_gana(self):
        d = _doc([_ref("R01", 100, 160), _ref("R02", 300, 330)])
        propuestos = [
            _ref("D1", 110, 150, autor="modelo"),   # duplica R01
            _ref("D2", 600, 640, autor="modelo"),   # nuevo
        ]
        conteo = refs.fusionar_momentos(d, propuestos, fuente="borrador:modelo")
        assert conteo == {"nuevos": 1, "duplicados": 1}
        ids = sorted(m["id"] for m in d["momentos"])
        assert ids == ["R01", "R02", "R03"]
        r01 = next(m for m in d["momentos"] if m["id"] == "R01")
        assert r01["nucleo_inicio"] == 100
        assert r01["tambien_propuesto_por"] == ["borrador:modelo"]

    def test_no_resucita_descartados(self):
        d = _doc([])
        d["descartados"] = [_ref("R01", 100, 160)]
        conteo = refs.fusionar_momentos(d, [_ref("D1", 105, 155)], fuente="x")
        assert conteo["nuevos"] == 0 and d["momentos"] == []


class TestMarkdown:
    def test_ida_y_vuelta_sin_cambios_no_toca_nada(self):
        d = _doc([_ref("R01", 100, 160), _ref("R02", 700.5, 760)], [{"inicio": 310, "fin": 368, "motivo": "DiDi"}])
        md = refs.generar_markdown(d, lines=[{"start": 100, "end": 160, "text": "hola mundo"}])
        assert "https://youtu.be/VID123?t=95" in md
        assert "https://youtu.be/VID123?t=700" in md
        antes = copy.deepcopy(d)
        conteo = refs.aplicar_validacion(d, md, validador="agustin")
        assert conteo["corregidos"] == 0 and conteo["pendientes"] == 2
        assert d == antes  # 310 == 310.0: mismos valores

    def test_aplica_si_no_correcciones_y_nuevo(self):
        d = _doc([_ref("R01", 100, 160), _ref("R02", 300, 330), _ref("R03", 500, 520)])
        md = refs.generar_markdown(d)
        bloques = md.split("\n### ")
        # R01: sí con núcleo corregido en m:ss; R02: no; R03: queda pendiente
        bloques[1] = bloques[1].replace("- decision: pendiente", "- decision: si").replace(
            "- nucleo: 100 – 160", "- nucleo: 1:42 – 2:38").replace("- calidad: A", "- calidad: b")
        bloques[2] = bloques[2].replace("- decision: pendiente", "- decision: no")
        md2 = "\n### ".join(bloques) + (
            "\n### NUEVO\n- decision: si\n- tramo: 700 – 760\n- nucleo: 705 – 750\n"
            "- calidad: A\n- tipo: opinión\n- por_que: remate fuerte\n"
        )
        md2 = md2.replace("## Excluir\n", "## Excluir\n\n- EXCLUIR 3:00–3:30 | aviso\n")
        conteo = refs.aplicar_validacion(d, md2, validador="agustin", fecha="2026-09-24")
        assert conteo == {"validados": 1, "borrados": 1, "pendientes": 1, "agregados": 1, "corregidos": 1}
        por_id = {m["id"]: m for m in d["momentos"]}
        assert por_id["R01"]["validado_por"] == "agustin"
        assert (por_id["R01"]["nucleo_inicio"], por_id["R01"]["nucleo_fin"]) == (102, 158)
        assert por_id["R01"]["calidad"] == "B"
        assert "R02" not in por_id and d["descartados"][0]["id"] == "R02"
        assert por_id["R03"]["validado_por"] is None
        nuevo = por_id["R04"]
        assert nuevo["autor"] == "agustin" and nuevo["validado_por"] == "agustin" and nuevo["tipo"] == "opinión"
        assert d["excluir"] == [{"inicio": 180.0, "fin": 210.0, "motivo": "aviso"}]
        assert refs.estado_validacion(d)["validados"] == 2

    def test_bloque_borrado_es_error(self):
        d = _doc([_ref("R01", 100, 160), _ref("R02", 300, 330)])
        md = refs.generar_markdown(d).split("### R02")[0]
        with pytest.raises(ValueError, match="R02"):
            refs.aplicar_validacion(d, md, validador="agustin")

    def test_correccion_invalida_es_error(self):
        d = _doc([_ref("R01", 100, 160)])
        md = refs.generar_markdown(d).replace("- nucleo: 100 – 160", "- nucleo: 200 – 160")
        with pytest.raises(ValueError, match="no valida"):
            refs.aplicar_validacion(d, md, validador="agustin")
