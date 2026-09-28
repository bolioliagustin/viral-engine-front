"""
W21 — Pasada A por Ventanas (docs/briefs/W21-pasada-a-por-ventanas.md, ADR 0009).

Con el transcript completo en una sola llamada, la Pasada A devuelve los
candidatos en orden cronológico y agota el cupo antes del final del video
(B60BHDNFNxM: cuartos [17, 13, 0, 0]). Acá están las piezas puras de la
alternativa: partir las Líneas en Ventanas con solape, repartir el cupo de
candidatos entre ellas y unir lo que devuelve cada llamada (deduplicación
por solape y fusión de la misma historia partida en dos).

La orquestación (llamadas en paralelo, respaldo) vive en
`moment_selector.select_moments`. Todo queda detrás de
`SELECCION_POR_VENTANAS` (default off hasta pasar G1).
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

# Una última Ventana con menos minutos que esto se une a la anterior.
VENTANA_ULTIMA_MIN_MIN = 8.0
# Un video de hasta VENTANA_MIN + esto usa una sola Ventana (comportamiento de hoy).
VENTANA_UNICA_MARGEN_MIN = 5.0
# Candidatos mínimos por Ventana.
CUPO_MINIMO = 3
# Deduplicación: solape mayor a esta fracción de la duración del más corto.
DEDUP_SOLAPE_MIN = 0.5
# Fusión: hueco máximo entre el final de uno y el inicio del siguiente.
FUSION_HUECO_MAX_SEG = 5.0
# Llamadas simultáneas de la Pasada A.
VENTANAS_CONCURRENCIA_MAX = 4


def _on(valor: str | None) -> bool:
    return (valor or "").strip().lower() in ("on", "true", "1", "yes", "si", "sí")


def seleccion_por_ventanas_enabled() -> bool:
    """`SELECCION_POR_VENTANAS=on|off` (default off hasta pasar G1)."""
    return _on(os.getenv("SELECCION_POR_VENTANAS", "off"))


def fusion_enabled() -> bool:
    """`VENTANAS_FUSION=on|off` (default on): fusión de la misma historia al unir Ventanas."""
    return _on(os.getenv("VENTANAS_FUSION", "on"))


def ventana_min() -> float:
    return float(os.getenv("VENTANA_MIN", "20"))


def ventana_solape_seg() -> float:
    return float(os.getenv("VENTANA_SOLAPE_SEG", "180"))


@dataclass
class Ventana:
    """Tramo del transcript que ve una llamada de la Pasada A (CONTEXT.md: Ventana)."""
    indice: int
    inicio: float          # segundos absolutos, incluye el solape con la anterior
    fin: float             # segundos absolutos, incluye el solape con la siguiente
    nucleo_inicio: float   # tramo propio, sin solapes (los núcleos parten el video)
    nucleo_fin: float
    lineas: list[dict] = field(default_factory=list)

    @property
    def minutos_nucleo(self) -> float:
        return max(0.0, self.nucleo_fin - self.nucleo_inicio) / 60.0


def armar_ventanas(
    lineas: list[dict] | None,
    duracion: float,
    *,
    minutos: float | None = None,
    solape_seg: float | None = None,
) -> list[Ventana]:
    """
    Parte las Líneas en Ventanas de `minutos` (VENTANA_MIN) con `solape_seg`
    (VENTANA_SOLAPE_SEG) de solape: la Ventana k tiene núcleo
    [k·V, (k+1)·V) y ve además `solape_seg / 2` antes y después, así dos
    Ventanas vecinas comparten `solape_seg` segundos alrededor del borde.

    - Una última Ventana de menos de 8 min se une a la anterior.
    - Un video de hasta V + 5 min usa una sola Ventana.
    - Sin Líneas (transcript de captions), una sola Ventana sin Líneas: el
      caller usa el transcript formateado de siempre.

    Una Línea entra en una Ventana si empieza dentro de su rango; los
    timestamps nunca se reescriben (siguen siendo absolutos).
    """
    lineas = [ln for ln in (lineas or []) if isinstance(ln, dict)]
    minutos = ventana_min() if minutos is None else float(minutos)
    solape_seg = ventana_solape_seg() if solape_seg is None else float(solape_seg)
    fin_lineas = max((float(ln.get("end", ln.get("start", 0)) or 0) for ln in lineas), default=0.0)
    duracion = max(float(duracion or 0), fin_lineas)
    largo = minutos * 60.0

    if not lineas or largo <= 0 or duracion <= largo + VENTANA_UNICA_MARGEN_MIN * 60:
        return [Ventana(0, 0.0, duracion, 0.0, duracion, list(lineas))]

    n = math.ceil(duracion / largo)
    if n > 1 and duracion - (n - 1) * largo < VENTANA_ULTIMA_MIN_MIN * 60:
        n -= 1

    medio = solape_seg / 2.0
    ventanas = []
    for k in range(n):
        n_ini = k * largo
        n_fin = duracion if k == n - 1 else (k + 1) * largo
        ini = 0.0 if k == 0 else max(0.0, n_ini - medio)
        fin = duracion if k == n - 1 else min(duracion, n_fin + medio)
        propias = [ln for ln in lineas if ini <= float(ln.get("start", 0) or 0) < fin
                   or (k == n - 1 and float(ln.get("start", 0) or 0) >= fin)]
        ventanas.append(Ventana(k, ini, fin, n_ini, n_fin, propias))
    return ventanas


def repartir_cupo(ventanas: list[Ventana], total: int, minimo: int = CUPO_MINIMO) -> list[int]:
    """
    Cupo de candidatos por Ventana, proporcional a los minutos de su núcleo
    (resto mayor). Cada Ventana recibe al menos `minimo`; el total respeta
    `total` (que viene de `candidate_count`, ≤ 30) salvo que el mínimo no
    entre (más de total/3 Ventanas: > 200 min con los defaults, fuera del
    tope de duración de hoy).
    """
    n = len(ventanas)
    if n == 0:
        return []
    if n == 1:
        return [max(1, int(total))]
    total = max(int(total), minimo * n)
    pesos = [max(v.minutos_nucleo, 1e-6) for v in ventanas]
    suma = sum(pesos)
    extra = total - minimo * n
    # El mínimo va primero; el resto se reparte proporcional a lo que le
    # falta a cada Ventana para llegar a su parte ideal.
    ideal = [total * p / suma for p in pesos]
    faltante = [max(0.0, i - minimo) for i in ideal]
    suma_f = sum(faltante)
    if suma_f <= 0:
        brutos = [extra * p / suma for p in pesos]
    else:
        brutos = [extra * f / suma_f for f in faltante]
    cupos = [minimo + int(math.floor(b)) for b in brutos]
    resto = total - sum(cupos)
    orden = sorted(range(n), key=lambda i: (-(brutos[i] - math.floor(brutos[i])), i))
    for i in orden[:resto]:
        cupos[i] += 1
    return cupos


# ─── Unión ───────────────────────────────────────────────────────────────────

def _intervalo(m: dict) -> tuple[float, float] | None:
    try:
        ini, fin = float(m.get("start_time")), float(m.get("end_time"))
    except (TypeError, ValueError):
        return None
    return (ini, fin) if fin > ini else None


def _contiene(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] <= b[0] and a[1] >= b[1]


def _solape_relativo(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    corto = min(a[1] - a[0], b[1] - b[0])
    return inter / corto if corto > 0 else 0.0


def _posicion(m: dict) -> tuple[int, int]:
    """Mejor posicionado = menor puesto dentro de su Ventana; empate, Ventana anterior."""
    return int(m.get("puesto_en_ventana", 0) or 0), int(m.get("ventana", 0) or 0)


def deduplicar(candidatos: list[dict], umbral: float = DEDUP_SOLAPE_MIN) -> tuple[list[dict], list[dict]]:
    """
    Dos candidatos que se solapan más de `umbral` de la duración del más
    corto se reducen a uno: el que contiene al otro o, si ninguno contiene
    al otro, el mejor posicionado por su Ventana (`puesto_en_ventana`).
    Devuelve (sobrevivientes, descartados). Conserva el orden de entrada.
    """
    vivos = [m for m in candidatos if _intervalo(m) is not None]
    descartados: list[dict] = []
    cambio = True
    while cambio:
        cambio = False
        for i in range(len(vivos)):
            for j in range(i + 1, len(vivos)):
                a, b = vivos[i], vivos[j]
                ia, ib = _intervalo(a), _intervalo(b)
                if _solape_relativo(ia, ib) <= umbral:
                    continue
                if _contiene(ia, ib) and not _contiene(ib, ia):
                    perdedor = b
                elif _contiene(ib, ia) and not _contiene(ia, ib):
                    perdedor = a
                else:
                    perdedor = b if _posicion(a) <= _posicion(b) else a
                vivos.remove(perdedor)
                descartados.append(perdedor)
                cambio = True
                break
            if cambio:
                break
    return vivos, descartados


def _en_solape(ventanas: list[Ventana], va: int, vb: int, punto: float, hueco: float) -> bool:
    """¿`punto` cae en el tramo que ven las dos Ventanas (a ± `hueco`)?"""
    por_indice = {v.indice: v for v in ventanas}
    if va not in por_indice or vb not in por_indice:
        return False
    a, b = por_indice[va], por_indice[vb]
    ini, fin = max(a.inicio, b.inicio), min(a.fin, b.fin)
    return fin > ini and ini - hueco <= punto <= fin + hueco


def _fusionar_par(a: dict, b: dict) -> dict:
    """`a` va antes que `b`: el fusionado va del inicio de a al final de b."""
    base = a if _posicion(a) <= _posicion(b) else b
    out = dict(base)
    out["start_time"], out["end_time"] = a["start_time"], b["end_time"]
    va, vb = a.get("verification") or {}, b.get("verification") or {}
    if isinstance(va, dict) and isinstance(vb, dict) and (va or vb):
        ver = dict(base.get("verification") or {})
        if va.get("first_phrase_in_audio"):
            ver["first_phrase_in_audio"] = va["first_phrase_in_audio"]
        if vb.get("last_phrase_in_audio"):
            ver["last_phrase_in_audio"] = vb["last_phrase_in_audio"]
        out["verification"] = ver
    partes = (a.get("fusionado_de") or [[a["start_time"], a["end_time"]]]) + \
             (b.get("fusionado_de") or [[b["start_time"], b["end_time"]]])
    out["fusionado_de"] = partes
    out["puesto_en_ventana"] = min(_posicion(a)[0], _posicion(b)[0])
    return out


def fusionar_historias(
    candidatos: list[dict],
    ventanas: list[Ventana],
    duracion_max: float,
    hueco_max: float = FUSION_HUECO_MAX_SEG,
) -> tuple[list[dict], int]:
    """
    Fusiona dos candidatos contiguos (el segundo empieza a ≤ `hueco_max` s
    del final del primero, sin solaparse más que eso) cuya duración
    combinada no pasa `duracion_max`, si vienen de la misma Ventana o si el
    empalme cae en el solape de sus dos Ventanas. Encadena (A+B+C) mientras
    entre. El fusionado lleva `fusionado_de` con los intervalos originales.
    Devuelve (candidatos, fusiones hechas).
    """
    orden = sorted(
        (m for m in candidatos if _intervalo(m) is not None),
        key=lambda m: (float(m["start_time"]), float(m["end_time"])),
    )
    out: list[dict] = []
    fusiones = 0
    for m in orden:
        if out:
            prev = out[-1]
            pa, pb = _intervalo(prev), _intervalo(m)
            hueco = pb[0] - pa[1]
            contiguo = -hueco_max <= hueco <= hueco_max and pb[1] > pa[1]
            cabe = pb[1] - pa[0] <= duracion_max
            va, vb = int(prev.get("ventana", 0) or 0), int(m.get("ventana", 0) or 0)
            misma_historia = va == vb or _en_solape(ventanas, va, vb, (pa[1] + pb[0]) / 2, hueco_max)
            if contiguo and cabe and misma_historia:
                out[-1] = _fusionar_par(prev, m)
                fusiones += 1
                continue
        out.append(m)
    return out, fusiones


def ordenar_union(candidatos: list[dict]) -> list[dict]:
    """
    Orden de salida: por puesto dentro de su Ventana y después por Ventana
    (todos los primeros, después todos los segundos, …). Aproxima "del mejor
    al peor" sin favorecer el principio del video.
    """
    return sorted(candidatos, key=_posicion)
