"""
Tabla antes → después por video de dos corridas del tier `seleccion` y
chequeo de los criterios de G1 (W21, docs/briefs/W21-pasada-a-por-ventanas.md).
No llama a ninguna API: lee los JSON de `eval/runs/`.

    python eval/g1_seleccion.py eval/runs/2026-09-28-seleccion-baseline-validado.json \\
        eval/runs/<fecha>-seleccion-ventanas.json

Costo de la Pasada A = `costo_por_tarea.analysis` (sin el clasificador);
latencia = segundos de la repetición (clasificador + Pasada A, igual en las
dos corridas; si la corrida trae `pasada_a.segundos`, se muestra aparte).
"""
from __future__ import annotations

import json
import statistics
import sys

LARGO_MIN = 60.0
CORTO_MIN = 30.0


def _media(xs):
    xs = [x for x in xs if x is not None]
    return statistics.mean(xs) if xs else None


def _m(v: dict, clave: str):
    return ((v.get("agregado") or {}).get(clave) or {}).get("media")


def _costo_a(v: dict):
    return _media([(r.get("costo_por_tarea") or {}).get("analysis") for r in v["reps"] if not r.get("error")])


def _seg(v: dict):
    return _media([r.get("segundos") for r in v["reps"] if not r.get("error")])


def _sin_fusion(v: dict, clave: str):
    vals = [((r.get("metricas") or {}).get("sin_fusion") or {}).get(clave) for r in v["reps"] if r.get("metricas")]
    return _media(vals) if any(x is not None for x in vals) else None


def _pct(x):
    return "—" if x is None else f"{x:.0%}"


def comparar(antes: dict, despues: dict) -> tuple[list[str], dict]:
    por_id = {v["id"]: v for v in antes["videos"]}
    filas = [
        "| Video | min | recall_completo A | min_cuarto | historias_partidas (sin fusión) | costo Pasada A US$ | latencia s |",
        "|---|---|---|---|---|---|---|",
    ]
    largos, cortos, ok_partidas, costos, lat = [], [], True, [], []
    for d in despues["videos"]:
        a = por_id.get(d["id"])
        if not a or not d.get("agregado") or not a.get("agregado"):
            continue
        mins = (d.get("duracion_sec") or 0) / 60
        ra, rd = _m(a, "recall_completo"), _m(d, "recall_completo")
        qa, qd = _m(a, "min_cuarto"), _m(d, "min_cuarto")
        pa, pd, psf = _m(a, "historias_partidas"), _m(d, "historias_partidas"), _sin_fusion(d, "historias_partidas")
        ca, cd = _costo_a(a), _costo_a(d)
        sa, sd = _seg(a), _seg(d)
        filas.append(
            f"| {d['id']} | {mins:.0f} | {_pct(ra)} → **{_pct(rd)}** | {_pct(qa)} → **{_pct(qd)}** | "
            f"{pa:.1f} → **{pd:.1f}** ({'—' if psf is None else f'{psf:.1f}'}) | "
            f"{ca:.3f} → {cd:.3f} | {sa:.0f} → {sd:.0f} |"
        )
        if mins > LARGO_MIN:
            largos.append((rd, qd))
        if mins < CORTO_MIN:
            cortos.append((ra, rd))
        ok_partidas &= pd is not None and pd <= 1
        costos.append((ca, cd))
        lat.append((sa, sd))
    g1 = {
        "recall_completo_largos": _media([r for r, _ in largos]),
        "min_cuarto_largos": _media([q for _, q in largos]),
        "historias_partidas_max_1": ok_partidas,
        "regresion_cortos_pp": max(((a - d) * 100 for a, d in cortos), default=0.0),
        "costo_pasada_a_delta": (_media([d for _, d in costos]) / _media([a for a, _ in costos]) - 1) if costos else None,
        "latencia_delta_s": (_media([d for _, d in lat]) - _media([a for a, _ in lat])) if lat else None,
    }
    g1["pasa"] = {
        "recall_completo ≥ 60 % (> 60 min)": (g1["recall_completo_largos"] or 0) >= 0.60,
        "min_cuarto ≥ 15 % (> 60 min)": (g1["min_cuarto_largos"] or 0) >= 0.15,
        "historias_partidas ≤ 1 por video": ok_partidas,
        "sin regresión > 5 pp (< 30 min)": g1["regresion_cortos_pp"] <= 5,
        "costo Pasada A ≤ +25 %": g1["costo_pasada_a_delta"] is not None and g1["costo_pasada_a_delta"] <= 0.25,
        "latencia ≤ +20 s": g1["latencia_delta_s"] is not None and g1["latencia_delta_s"] <= 20,
    }
    return filas, g1


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    antes, despues = (json.load(open(p, encoding="utf-8")) for p in sys.argv[1:])
    filas, g1 = comparar(antes, despues)
    print("\n".join(filas))
    print()
    print(f"G1 · recall_completo A (> 60 min): {_pct(g1['recall_completo_largos'])} · "
          f"min_cuarto (> 60 min): {_pct(g1['min_cuarto_largos'])} · "
          f"costo Pasada A: {g1['costo_pasada_a_delta']:+.0%} · latencia: {g1['latencia_delta_s']:+.0f} s · "
          f"peor regresión en cortos: {g1['regresion_cortos_pp']:.0f} pp")
    for criterio, ok in g1["pasa"].items():
        print(f"  {'✅' if ok else '❌'} {criterio}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
