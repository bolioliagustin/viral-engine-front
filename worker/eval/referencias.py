"""
Referencias (W19, docs/PLAN_MEJORA.md §3 y brief W19): momentos de un video
que un humano publicaría, con su Núcleo. Son la verdad humana contra la que
se mide la selección (tier `seleccion`) y la entrega (tier `e2e`).

Un archivo por video en `eval/referencias/<youtube_id>.json`. El esquema es
contrato para W21–W25 y está documentado en `eval/README.md`.

Este módulo es puro (sin red): carga, valida, fusiona, genera el markdown de
validación y aplica las correcciones de ese markdown al JSON.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
REFERENCIAS_DIR = EVAL_DIR / "referencias"
VALIDAR_DIR = REFERENCIAS_DIR / "validar"
PROPUESTAS_DIR = REFERENCIAS_DIR / "propuestas"

ESQUEMA_VERSION = 1

TIPOS = (
    "anécdota",
    "opinión",
    "frase citable",
    "cruce con el público",
    "imitación",
    "dato",
    "explicación",
)
CALIDADES = ("A", "B")

# Dos momentos son "el mismo" si sus núcleos se solapan en al menos esta
# fracción del núcleo más corto (fusión borrador ↔ semilla o validadas).
SOLAPE_DUPLICADO = 0.5


# ─── Carga y guardado ────────────────────────────────────────────────────────

def ruta_referencias(youtube_id: str, base: Path | None = None) -> Path:
    return (base or REFERENCIAS_DIR) / f"{youtube_id}.json"


def cargar_referencias(youtube_id: str, base: Path | None = None) -> dict | None:
    ruta = ruta_referencias(youtube_id, base)
    if not ruta.exists():
        return None
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def guardar_referencias(doc: dict, base: Path | None = None) -> Path:
    ruta = ruta_referencias(doc["youtube_id"], base)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    doc["momentos"] = sorted(doc.get("momentos") or [], key=lambda m: (m["nucleo_inicio"], m["id"]))
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return ruta


def documento_vacio(youtube_id: str, video_id: str | None = None, duracion_sec: float | None = None) -> dict:
    return {
        "esquema": ESQUEMA_VERSION,
        "video_id": video_id,
        "youtube_id": youtube_id,
        "duracion_sec": duracion_sec,
        "borradores": [],
        "excluir": [],
        "momentos": [],
        "descartados": [],
    }


# ─── Validación del esquema ──────────────────────────────────────────────────

def validar_documento(doc: dict) -> list[str]:
    """Lista de errores de esquema (vacía = válido)."""
    errores: list[str] = []
    if not doc.get("youtube_id"):
        errores.append("falta youtube_id")
    dur = doc.get("duracion_sec")
    ids: set[str] = set()
    for m in doc.get("momentos") or []:
        mid = m.get("id") or "?"
        if mid in ids:
            errores.append(f"{mid}: id duplicado")
        ids.add(mid)
        try:
            ini, fin = float(m["inicio"]), float(m["fin"])
            ni, nf = float(m["nucleo_inicio"]), float(m["nucleo_fin"])
        except (KeyError, TypeError, ValueError):
            errores.append(f"{mid}: tiempos faltantes o no numéricos")
            continue
        if not (ini <= ni < nf <= fin):
            errores.append(f"{mid}: se espera inicio ≤ nucleo_inicio < nucleo_fin ≤ fin ({ini}, {ni}, {nf}, {fin})")
        if dur and fin > float(dur) + 1:
            errores.append(f"{mid}: fin {fin} pasa la duración del video ({dur})")
        if m.get("tipo") not in TIPOS:
            errores.append(f"{mid}: tipo {m.get('tipo')!r} no está en {TIPOS}")
        if m.get("calidad") not in CALIDADES:
            errores.append(f"{mid}: calidad {m.get('calidad')!r} no es A ni B")
        if not (m.get("por_que") or "").strip():
            errores.append(f"{mid}: falta por_que")
        if not m.get("autor"):
            errores.append(f"{mid}: falta autor")
    for e in doc.get("excluir") or []:
        try:
            if float(e["inicio"]) >= float(e["fin"]):
                errores.append(f"excluir {e}: inicio ≥ fin")
        except (KeyError, TypeError, ValueError):
            errores.append(f"excluir {e}: tiempos inválidos")
    return errores


def momentos_validados(doc: dict | None, *, incluir_borradores: bool = False) -> list[dict]:
    """Momentos que cuentan para medir: validados por un humano (o todos, si se pide)."""
    if not doc:
        return []
    return [
        m for m in doc.get("momentos") or []
        if incluir_borradores or m.get("validado_por")
    ]


def estado_validacion(doc: dict | None) -> dict[str, Any]:
    momentos = (doc or {}).get("momentos") or []
    validados = [m for m in momentos if m.get("validado_por")]
    return {
        "momentos": len(momentos),
        "validados": len(validados),
        "validados_a": sum(1 for m in validados if m.get("calidad") == "A"),
        "pendientes": len(momentos) - len(validados),
        "validado_por": sorted({m["validado_por"] for m in validados}),
    }


# ─── Fusión (borrador ↔ semilla/validadas) ──────────────────────────────────

def solape_nucleos(a: dict, b: dict) -> float:
    """Solape de núcleos sobre el núcleo más corto (0–1)."""
    ai, af = float(a["nucleo_inicio"]), float(a["nucleo_fin"])
    bi, bf = float(b["nucleo_inicio"]), float(b["nucleo_fin"])
    inter = max(0.0, min(af, bf) - max(ai, bi))
    corto = min(af - ai, bf - bi)
    return inter / corto if corto > 0 else 0.0


def _siguiente_id(existentes: list[dict]) -> int:
    nums = [int(m["id"][1:]) for m in existentes if re.fullmatch(r"R\d+", m.get("id") or "")]
    return (max(nums) + 1) if nums else 1


def fusionar_momentos(doc: dict, propuestos: list[dict], *, fuente: str) -> dict[str, int]:
    """
    Suma al documento los momentos propuestos que no duplican uno existente.
    Lo existente (semilla o validado) gana siempre: si un propuesto se solapa
    ≥ SOLAPE_DUPLICADO con un momento del documento, se descarta y se anota
    `tambien_propuesto_por` en el existente. Los nuevos reciben ids R<n>
    correlativos. Tampoco se re-agrega algo que el humano ya descartó.
    """
    existentes = doc.setdefault("momentos", [])
    descartados = doc.get("descartados") or []
    nuevos = duplicados = 0
    siguiente = _siguiente_id(existentes + descartados)
    for p in sorted(propuestos, key=lambda m: float(m["nucleo_inicio"])):
        dup = next((m for m in existentes if solape_nucleos(m, p) >= SOLAPE_DUPLICADO), None)
        if dup is None:
            dup = next((m for m in descartados if solape_nucleos(m, p) >= SOLAPE_DUPLICADO), None)
        if dup is not None:
            fuentes = dup.setdefault("tambien_propuesto_por", [])
            if fuente not in fuentes:
                fuentes.append(fuente)
            duplicados += 1
            continue
        nuevo = dict(p)
        nuevo["id"] = f"R{siguiente:02d}"
        siguiente += 1
        existentes.append(nuevo)
        nuevos += 1
    return {"nuevos": nuevos, "duplicados": duplicados}


def guardar_propuesta(propuesta: dict, base: Path | None = None) -> Path:
    """Propuesta de un borrador sin fusionar (validación en curso)."""
    ruta = (base or PROPUESTAS_DIR) / f"{propuesta['youtube_id']}.json"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(propuesta, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return ruta


def fusionar_propuesta(doc: dict | None, propuesta: dict) -> dict:
    """
    Suma la propuesta de un borrador al documento (lo crea si no existe):
    momentos sin duplicar, exclusiones que no se solapan con las existentes y
    el registro del borrador (modelo, costo, conteos) en `borradores[]`.
    """
    yt = propuesta["youtube_id"]
    doc = doc or documento_vacio(yt, propuesta.get("video_id"), propuesta.get("duracion_sec"))
    doc["video_id"] = doc.get("video_id") or propuesta.get("video_id")
    doc["duracion_sec"] = doc.get("duracion_sec") or propuesta.get("duracion_sec")
    if propuesta.get("transcript"):
        doc.setdefault("transcript", propuesta["transcript"])
    info = propuesta.get("borrador") or {}
    fuente = f"borrador:{info.get('modelo') or '?'}"
    conteo = fusionar_momentos(doc, propuesta.get("momentos") or [], fuente=fuente)
    for e in propuesta.get("excluir") or []:
        ei, ef = float(e["inicio"]), float(e["fin"])
        if ef > ei and not any(min(ef, float(x["fin"])) > max(ei, float(x["inicio"])) for x in doc.get("excluir") or []):
            doc.setdefault("excluir", []).append({"inicio": ei, "fin": ef, "motivo": e.get("motivo"), "autor": fuente})
    doc.setdefault("borradores", []).append({**info, **conteo})
    return doc


# ─── Markdown de validación ─────────────────────────────────────────────────

def fmt_tiempo(seg: float) -> str:
    s = int(round(float(seg)))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_seg(seg: float) -> str:
    """Segundos absolutos para editar: entero si lo es, si no un decimal."""
    v = float(seg)
    return str(int(v)) if v == int(v) else f"{v:.1f}"


def parse_tiempo(txt: str) -> float:
    """Acepta segundos (`557`, `557.5`) o `m:ss` / `h:mm:ss`."""
    txt = txt.strip()
    if ":" not in txt:
        return float(txt)
    total = 0.0
    for parte in txt.split(":"):
        total = total * 60 + float(parte)
    return total


def link_youtube(youtube_id: str, seg: float) -> str:
    return f"https://youtu.be/{youtube_id}?t={int(float(seg))}"


def texto_de_lineas(lines: list[dict] | None, inicio: float, fin: float) -> str:
    if not lines:
        return ""
    return " ".join(
        (l.get("text") or "").strip() for l in lines
        if float(l.get("end", 0)) > inicio and float(l.get("start", 0)) < fin
    ).strip()


def _recorte(texto: str, palabras: int = 28) -> str:
    ws = texto.split()
    if len(ws) <= palabras * 2:
        return texto
    return " ".join(ws[:palabras]) + " … " + " ".join(ws[-palabras:])


DECISIONES = ("pendiente", "si", "no")

_INSTRUCCIONES = """\
Cómo validar (una pasada, sin apuro):

1. Mirá cada momento desde el link. El **núcleo** es lo mínimo que el clip tiene
   que contener: del planteo al remate. El **tramo** es el clip ideal completo.
2. En `decision:` poné `si` (lo publicarías: queda validado), `no` (se borra) o
   dejá `pendiente`.
3. Corregí lo que haga falta: `tramo`, `nucleo` (segundos o m:ss, **absolutos**),
   `calidad` (A = lo publicaría seguro; B = probablemente), `tipo`, `por_que`.
4. Para agregar un momento, copiá un bloque al final con `### NUEVO` como título.
5. Tramos a excluir (publicidad): una línea `- EXCLUIR <inicio>–<fin> | motivo`.

Después: `python eval/referencias_cli.py aplicar {md} --validador agustin`
"""


def generar_markdown(doc: dict, lines: list[dict] | None = None) -> str:
    yt = doc["youtube_id"]
    est = estado_validacion(doc)
    out = [
        f"# Referencias — {doc.get('video_id') or yt} ({yt})",
        "",
        f"Video: https://youtu.be/{yt} · duración {fmt_tiempo(doc.get('duracion_sec') or 0)} · "
        f"{est['momentos']} momentos ({est['validados']} validados, {est['pendientes']} pendientes)",
        "",
        _INSTRUCCIONES.format(md=f"eval/referencias/validar/{yt}.md"),
        "## Excluir",
        "",
    ]
    for e in doc.get("excluir") or []:
        out.append(f"- EXCLUIR {fmt_seg(e['inicio'])}–{fmt_seg(e['fin'])} | {e.get('motivo') or ''}".rstrip())
    out += ["", "## Momentos", ""]
    for m in sorted(doc.get("momentos") or [], key=lambda x: float(x["nucleo_inicio"])):
        decision = "si" if m.get("validado_por") else "pendiente"
        titulo = m.get("titulo") or (m.get("por_que") or "")[:60]
        out += [
            f"### {m['id']} · {m['calidad']} · {titulo}",
            "",
            f"[▶ tramo {fmt_tiempo(m['inicio'])}]({link_youtube(yt, m['inicio'])}) · "
            f"[▶ núcleo {fmt_tiempo(m['nucleo_inicio'])}]({link_youtube(yt, m['nucleo_inicio'])}) · "
            f"autor: {m.get('autor')}"
            + (f" · también lo propuso: {', '.join(m['tambien_propuesto_por'])}" if m.get("tambien_propuesto_por") else ""),
            "",
            f"- decision: {decision}",
            f"- tramo: {fmt_seg(m['inicio'])} – {fmt_seg(m['fin'])}",
            f"- nucleo: {fmt_seg(m['nucleo_inicio'])} – {fmt_seg(m['nucleo_fin'])}"
            f"   ({fmt_tiempo(m['nucleo_inicio'])}–{fmt_tiempo(m['nucleo_fin'])}, "
            f"{float(m['nucleo_fin']) - float(m['nucleo_inicio']):.0f} s)",
            f"- calidad: {m['calidad']}",
            f"- tipo: {m['tipo']}",
            f"- titulo: {m.get('titulo') or ''}",
            f"- por_que: {m.get('por_que') or ''}",
        ]
        texto = texto_de_lineas(lines, float(m["nucleo_inicio"]), float(m["nucleo_fin"]))
        if texto:
            out += ["", f"> {_recorte(texto)}"]
        out.append("")
    return "\n".join(out).rstrip() + "\n"


_RE_BLOQUE = re.compile(r"^###\s+(\S+)", re.M)
_RE_CAMPO = re.compile(r"^-[ \t]*(decision|tramo|nucleo|calidad|tipo|titulo|por_que)[ \t]*:[ \t]*(.*)$", re.M)
_RE_RANGO = re.compile(r"^\s*([\d:.]+)\s*[–\-]+\s*([\d:.]+)\s*(?:\(.*\))?\s*$")
_RE_EXCLUIR = re.compile(r"^-[ \t]*EXCLUIR[ \t]+([\d:.]+)[ \t]*[–\-]+[ \t]*([\d:.]+)[ \t]*(?:\|[ \t]*(.*))?$", re.M)


def _parse_rango(txt: str, campo: str, mid: str) -> tuple[float, float]:
    m = _RE_RANGO.match(txt)
    if not m:
        raise ValueError(f"{mid}: {campo} ilegible: {txt!r} (esperado 'inicio – fin')")
    return parse_tiempo(m.group(1)), parse_tiempo(m.group(2))


def parsear_markdown(md: str) -> dict[str, Any]:
    """Devuelve {'excluir': [...], 'bloques': [{id, decision, ...}]} del markdown editado."""
    excluir = [
        {"inicio": parse_tiempo(a), "fin": parse_tiempo(b), "motivo": (motivo or "").strip() or None}
        for a, b, motivo in _RE_EXCLUIR.findall(md)
    ]
    bloques = []
    posiciones = [(m.start(), m.group(1)) for m in _RE_BLOQUE.finditer(md)]
    for i, (pos, mid) in enumerate(posiciones):
        fin = posiciones[i + 1][0] if i + 1 < len(posiciones) else len(md)
        cuerpo = md[pos:fin]
        campos = {k: v.strip() for k, v in _RE_CAMPO.findall(cuerpo)}
        b: dict[str, Any] = {"id": mid}
        dec = (campos.get("decision") or "pendiente").lower()
        dec = {"sí": "si", "mantener": "si", "borrar": "no"}.get(dec, dec)
        if dec not in DECISIONES:
            raise ValueError(f"{mid}: decision {dec!r} no es si / no / pendiente")
        b["decision"] = dec
        if "tramo" in campos:
            b["inicio"], b["fin"] = _parse_rango(campos["tramo"], "tramo", mid)
        if "nucleo" in campos:
            b["nucleo_inicio"], b["nucleo_fin"] = _parse_rango(campos["nucleo"], "nucleo", mid)
        for k in ("calidad", "tipo", "titulo", "por_que"):
            if k in campos:
                b[k] = campos[k]
        if "calidad" in b:
            b["calidad"] = b["calidad"].upper()
        bloques.append(b)
    return {"excluir": excluir, "bloques": bloques}


def aplicar_validacion(doc: dict, md: str, *, validador: str, fecha: str | None = None) -> dict[str, int]:
    """
    Aplica al documento las decisiones y correcciones del markdown:
    `si` → valida (con los tiempos/campos corregidos), `no` → pasa a
    `descartados`, `pendiente` → solo aplica correcciones. `### NUEVO`
    agrega un momento (autor = validador). La lista `excluir` del markdown
    reemplaza a la del documento. Lanza ValueError si el resultado no valida.
    """
    fecha = fecha or date.today().isoformat()
    parsed = parsear_markdown(md)
    por_id = {m["id"]: m for m in doc.get("momentos") or []}
    conteo = {"validados": 0, "borrados": 0, "pendientes": 0, "agregados": 0, "corregidos": 0}
    siguiente = _siguiente_id(list(por_id.values()) + (doc.get("descartados") or []))
    vistos: set[str] = set()

    for b in parsed["bloques"]:
        if b["id"].upper() == "NUEVO":
            if b["decision"] == "no":
                continue
            nuevo = {
                "id": f"R{siguiente:02d}", "autor": validador, "fecha": fecha,
                "validado_por": None,
            }
            siguiente += 1
            nuevo.update({k: v for k, v in b.items() if k not in ("id", "decision")})
            if b["decision"] == "si":
                nuevo["validado_por"] = validador
            doc.setdefault("momentos", []).append(nuevo)
            por_id[nuevo["id"]] = nuevo
            vistos.add(nuevo["id"])
            conteo["agregados"] += 1
            continue
        m = por_id.get(b["id"])
        if m is None:
            raise ValueError(f"{b['id']}: no existe en el JSON (para agregar usá '### NUEVO')")
        vistos.add(b["id"])
        cambios = {k: v for k, v in b.items() if k not in ("id", "decision") and m.get(k) != v}
        # m:ss redondea a segundos: no contar como corrección un cambio < 1 s
        cambios = {
            k: v for k, v in cambios.items()
            if not (isinstance(v, float) and isinstance(m.get(k), (int, float)) and abs(v - float(m[k])) < 1)
            and not (v == "" and not m.get(k))
        }
        if cambios:
            m.update(cambios)
            m.setdefault("corregido_por", validador)
            conteo["corregidos"] += 1
        if b["decision"] == "si":
            m["validado_por"] = validador
            m["fecha_validacion"] = fecha
            conteo["validados"] += 1
        elif b["decision"] == "no":
            doc["momentos"].remove(m)
            doc.setdefault("descartados", []).append(
                {**m, "descartado_por": validador, "fecha_descarte": fecha}
            )
            conteo["borrados"] += 1
        else:
            conteo["pendientes"] += 1

    faltan = set(por_id) - vistos
    if faltan:
        raise ValueError(f"el markdown no trae los momentos {sorted(faltan)}: ¿se borró un bloque? usá 'decision: no'")
    doc["excluir"] = parsed["excluir"]
    errores = validar_documento(doc)
    if errores:
        raise ValueError("el resultado no valida:\n  " + "\n  ".join(errores))
    return conteo
