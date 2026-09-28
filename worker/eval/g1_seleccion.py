"""
Tabla antes → después por video de dos corridas del tier `seleccion` y
chequeo de los criterios de G1 (docs/PLAN_MEJORA.md §7, ajustado el 28-sep
con la vara validada). No llama a ninguna API: lee los JSON de `eval/runs/`.

    python eval/g1_seleccion.py eval/runs/2026-09-28-seleccion-baseline-validado.json \\
        eval/runs/<fecha>-seleccion-<variante>.json

**Misma vara para las dos corridas (W22):** las métricas guardadas en cada
JSON son las de cuando se corrió (el baseline se midió contra borradores).
Acá se **recalculan las dos** con los candidatos guardados y las
Referencias actuales, **solo validadas** (`seleccion.calcular_metricas`,
`incluir_borradores=False`). Así el "antes" y el "después" se miden igual.

Criterios de G1 (sobre la corrida "después"; costo y regresión, contra "antes"):
- `recall_completo` ≥ 55 % y `min_cuarto` ≥ 15 % en videos > 60 min (media);
- `historias_partidas` ≤ 1 por video;
- 0 candidatos en publicidad (tramos `excluir` cuyo motivo es un aviso);
- costo de la Pasada A ≤ +35 %;
- sin regresión > 5 pp de `recall_completo` en videos cortos (< 30 min),
  salvo que la regresión sea ≤ 1 Referencia A.

Costo de la Pasada A = `costo_por_tarea.analysis` (sin el clasificador).
La latencia, el recall por Formato y el acierto del clasificador (si la
corrida trae `formato` por repetición) se muestran como información.
"""
from __future__ import annotations

import copy
import json
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LARGO_MIN = 60.0
CORTO_MIN = 30.0

UMBRAL_RECALL_LARGOS = 0.55
UMBRAL_MIN_CUARTO_LARGOS = 0.15
UMBRAL_PARTIDAS = 1
UMBRAL_COSTO_DELTA = 0.35
UMBRAL_REGRESION_PP = 5.0
UMBRAL_REGRESION_REFS = 1.0

# Un tramo `excluir` es publicidad si su motivo lo dice (los demás son
# intros, logística o despedidas: no cuentan para "0 candidatos en publicidad").
_RE_PUBLICIDAD = re.compile(r"publicidad|aviso|auspici|chivo|sponsor|promoci", re.IGNORECASE)


def _media(xs):
    xs = [x for x in xs if x is not None]
    return statistics.mean(xs) if xs else None


def _m(v: dict, clave: str):
    return ((v.get("agregado") or {}).get(clave) or {}).get("media")


def _reps_ok(v: dict) -> list[dict]:
    return [r for r in v.get("reps") or [] if not r.get("error")]


def _costo_a(v: dict):
    return _media([(r.get("costo_por_tarea") or {}).get("analysis") for r in _reps_ok(v)])


def _seg(v: dict):
    return _media([r.get("segundos") for r in _reps_ok(v)])


def _sin_fusion(v: dict, clave: str):
    vals = [((r.get("metricas") or {}).get("sin_fusion") or {}).get(clave) for r in v["reps"] if r.get("metricas")]
    return _media(vals) if any(x is not None for x in vals) else None


def _n_refs_a(v: dict) -> int:
    return next((r["metricas"]["n_referencias_a"] for r in v.get("reps") or [] if r.get("metricas")), 0)


def _pct(x):
    return "—" if x is None else f"{x:.0%}"


def _num(x, fmt=".1f"):
    return "—" if x is None else format(x, fmt)


def tramos_publicidad(doc: dict | None) -> list[dict]:
    return [e for e in (doc or {}).get("excluir") or [] if _RE_PUBLICIDAD.search(e.get("motivo") or "")]


def candidatos_en_publicidad(v: dict, doc: dict | None) -> int:
    """Total de candidatos (sumando repeticiones) > 50 % dentro de un aviso."""
    import eval_metrics as em

    tramos = tramos_publicidad(doc)
    if not tramos:
        return 0
    return sum(
        1 for r in _reps_ok(v) for c in r.get("candidatos") or [] if em.en_exclusion(c, tramos)
    )


def misma_vara(corrida: dict, docs: dict[str, dict] | None = None) -> dict:
    """
    Copia de la corrida con las métricas recalculadas contra las Referencias
    validadas (sin borradores). `docs` (youtube_id → Referencias) permite
    medir sin leer disco (tests); sin `docs`, se leen de `eval/referencias/`.
    """
    import seleccion

    copia = copy.deepcopy(corrida)
    return seleccion.calcular_metricas(copia, incluir_borradores=False, docs=docs)


def _normalizar_formato(valor):
    from services.formatos import normalizar_formato
    return normalizar_formato(valor)


def acierto_clasificador(corrida: dict) -> tuple[int, int] | None:
    """(aciertos, total) del Formato por repetición contra `formato` del golden set."""
    pares = [
        (_normalizar_formato(v.get("formato")), _normalizar_formato(r.get("formato")))
        for v in corrida["videos"] for r in _reps_ok(v) if r.get("formato")
    ]
    if not pares:
        return None
    return sum(1 for esperado, obtenido in pares if esperado and esperado == obtenido), len(pares)


def comparar(antes: dict, despues: dict, docs: dict[str, dict] | None = None) -> tuple[list[str], dict]:
    """
    Recalcula las dos corridas con la misma vara y devuelve (filas de la
    tabla en Markdown, dict con las métricas de G1 y `pasa` por criterio).
    """
    import referencias as refs

    antes, despues = misma_vara(antes, docs), misma_vara(despues, docs)
    por_id = {v["id"]: v for v in antes["videos"]}

    def _doc(v):
        if docs is not None:
            return docs.get(v.get("youtube_id"))
        return refs.cargar_referencias(v.get("youtube_id") or "")

    filas = [
        "| Video | Formato | min | refs A | recall_completo A | min_cuarto | historias_partidas (sin fusión) "
        "| en publicidad | costo Pasada A US$ | latencia s |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    largos, cortos, partidas, costos, lat = [], [], [], [], []
    publicidad = 0
    por_formato: dict[str, list[tuple[float, float]]] = {}
    for d in despues["videos"]:
        a = por_id.get(d["id"])
        if not a or not d.get("agregado") or not a.get("agregado"):
            continue
        mins = (d.get("duracion_sec") or a.get("duracion_sec") or 0) / 60
        ra, rd = _m(a, "recall_completo"), _m(d, "recall_completo")
        qa, qd = _m(a, "min_cuarto"), _m(d, "min_cuarto")
        pa, pd, psf = _m(a, "historias_partidas"), _m(d, "historias_partidas"), _sin_fusion(d, "historias_partidas")
        ca, cd = _costo_a(a), _costo_a(d)
        sa, sd = _seg(a), _seg(d)
        doc = _doc(d)
        pub_a, pub_d = candidatos_en_publicidad(a, doc), candidatos_en_publicidad(d, doc)
        n_a = _n_refs_a(d)
        formato = _normalizar_formato(d.get("formato")) or d.get("formato") or "?"
        filas.append(
            f"| {d['id']} | {formato} | {mins:.0f} | {n_a} | {_pct(ra)} → **{_pct(rd)}** | "
            f"{_pct(qa)} → **{_pct(qd)}** | {_num(pa)} → **{_num(pd)}** ({_num(psf)}) | "
            f"{pub_a} → **{pub_d}** | {_num(ca, '.3f')} → {_num(cd, '.3f')} | {_num(sa, '.0f')} → {_num(sd, '.0f')} |"
        )
        if mins > LARGO_MIN:
            largos.append((rd, qd))
        if mins < CORTO_MIN and ra is not None and rd is not None:
            cortos.append({"id": d["id"], "pp": (ra - rd) * 100, "refs": (ra - rd) * n_a})
        partidas.append((d["id"], pd))
        publicidad += pub_d
        if ca is not None and cd is not None:
            costos.append((ca, cd))
        if sa is not None and sd is not None:
            lat.append((sa, sd))
        if ra is not None and rd is not None:
            por_formato.setdefault(formato, []).append((ra, rd))

    peor = max(cortos, key=lambda c: c["pp"], default=None)
    regresion_ok = all(
        c["pp"] <= UMBRAL_REGRESION_PP or c["refs"] <= UMBRAL_REGRESION_REFS + 1e-9 for c in cortos
    )
    costo_delta = (_media([d for _, d in costos]) / _media([a for a, _ in costos]) - 1) if costos else None
    g1 = {
        "recall_completo_largos": _media([r for r, _ in largos]),
        "min_cuarto_largos": _media([q for _, q in largos]),
        "n_largos": len(largos),
        "historias_partidas_max": max((p for _, p in partidas if p is not None), default=None),
        "candidatos_en_publicidad": publicidad,
        "costo_pasada_a_delta": costo_delta,
        "regresion_cortos_pp": peor["pp"] if peor else 0.0,
        "regresion_cortos_refs": peor["refs"] if peor else 0.0,
        "latencia_delta_s": (_media([d for _, d in lat]) - _media([a for a, _ in lat])) if lat else None,
        "recall_macro_antes": (antes.get("agregado") or {}).get("recall_completo", {}).get("media"),
        "recall_macro_despues": (despues.get("agregado") or {}).get("recall_completo", {}).get("media"),
        "recall_por_formato": {
            f: {"antes": _media([a for a, _ in xs]), "despues": _media([d for _, d in xs]), "videos": len(xs)}
            for f, xs in sorted(por_formato.items())
        },
        "acierto_clasificador": acierto_clasificador(despues),
    }
    g1["pasa"] = {
        f"recall_completo ≥ {UMBRAL_RECALL_LARGOS:.0%} (> 60 min)":
            bool(largos) and (g1["recall_completo_largos"] or 0) >= UMBRAL_RECALL_LARGOS,
        f"min_cuarto ≥ {UMBRAL_MIN_CUARTO_LARGOS:.0%} (> 60 min)":
            bool(largos) and (g1["min_cuarto_largos"] or 0) >= UMBRAL_MIN_CUARTO_LARGOS,
        f"historias_partidas ≤ {UMBRAL_PARTIDAS} por video":
            bool(partidas) and all(p is not None and p <= UMBRAL_PARTIDAS for _, p in partidas),
        "0 candidatos en publicidad": publicidad == 0,
        f"costo Pasada A ≤ +{UMBRAL_COSTO_DELTA:.0%}":
            costo_delta is not None and costo_delta <= UMBRAL_COSTO_DELTA + 1e-9,
        f"sin regresión > {UMBRAL_REGRESION_PP:.0f} pp en cortos (< 30 min), salvo ≤ 1 Referencia": regresion_ok,
    }
    return filas, g1


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    antes, despues = (json.load(open(p, encoding="utf-8")) for p in sys.argv[1:])
    filas, g1 = comparar(antes, despues)
    print("Métricas recalculadas en las dos corridas contra Referencias validadas (misma vara).\n")
    print("\n".join(filas))
    print()
    costo = g1["costo_pasada_a_delta"]
    lat = g1["latencia_delta_s"]
    print(f"recall_completo A macro: {_pct(g1['recall_macro_antes'])} → {_pct(g1['recall_macro_despues'])}")
    print(f"G1 · recall_completo A (> 60 min, {g1['n_largos']} videos): {_pct(g1['recall_completo_largos'])} · "
          f"min_cuarto (> 60 min): {_pct(g1['min_cuarto_largos'])} · "
          f"costo Pasada A: {'—' if costo is None else f'{costo:+.0%}'} · "
          f"latencia: {'—' if lat is None else f'{lat:+.0f} s'} · "
          f"peor regresión en cortos: {g1['regresion_cortos_pp']:.0f} pp ({g1['regresion_cortos_refs']:.1f} Referencias) · "
          f"candidatos en publicidad: {g1['candidatos_en_publicidad']}")
    for f, x in g1["recall_por_formato"].items():
        print(f"  recall_completo A · {f} ({x['videos']} videos): {_pct(x['antes'])} → {_pct(x['despues'])}")
    if g1["acierto_clasificador"]:
        ok, n = g1["acierto_clasificador"]
        print(f"  clasificador de Formato: {ok}/{n} ({ok / n:.0%}) contra el golden set")
    for criterio, ok in g1["pasa"].items():
        print(f"  {'✅' if ok else '❌'} {criterio}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
