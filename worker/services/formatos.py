"""
W22 — Formato de contenido (docs/briefs/W22-formatos-de-contenido.md, ADR 0010).

El clasificador de siempre elegía podcast o business con los primeros 1500
caracteres del transcript: un programa de humor con 5 panelistas y público
salía "podcast" y recibía el foco de una entrevista. Acá están las piezas
puras del reemplazo:

- los cuatro **Formatos** (`entrevista`, `charla`, `monologo`, `clase`) y su
  mapeo a la categoría vieja (`category`), que siguen leyendo el juez,
  `content_results` y el eval;
- los tres extractos (inicio, mitad y final) que ve el clasificador;
- el parseo de la respuesta del clasificador (valor inválido → `entrevista`);
- la verificación posterior de publicidad: detecta tramos de aviso por
  palabras clave y descarta los candidatos que caen mayormente adentro.

La llamada al modelo vive en `processor.clasificar_formato` y el foco por
Formato en `moment_selector.get_selection_prompt`. Todo queda detrás de
`FORMATOS` (default off hasta pasar G1).
"""
from __future__ import annotations

import os
import re
import unicodedata
from collections import Counter

FORMATOS = ("entrevista", "charla", "monologo", "clase")
FORMATO_POR_DEFECTO = "entrevista"

# Mapeo a la categoría vieja (CONTEXT.md: Categoría, obsoleta).
_CATEGORIA_POR_FORMATO = {
    "entrevista": "podcast",
    "charla": "podcast",
    "monologo": "business",
    "clase": "business",
}

# Caracteres por extracto del clasificador (inicio, mitad y final).
EXTRACTO_CHARS = 800


def _on(valor: str | None) -> bool:
    return (valor or "").strip().lower() in ("on", "true", "1", "yes", "si", "sí")


def formatos_enabled() -> bool:
    """`FORMATOS=on|off` (default off hasta pasar G1)."""
    return _on(os.getenv("FORMATOS", "off"))


def _sin_acentos(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto or "") if unicodedata.category(c) != "Mn"
    )


def normalizar_formato(valor: str | None) -> str | None:
    """
    `"Monólogo."` → `"monologo"`. Devuelve None si no es uno de los cuatro.
    Acepta la primera palabra que sea un Formato (el modelo a veces agrega
    una explicación), no una que aparezca más adelante.
    """
    texto = _sin_acentos(valor or "").lower()
    palabras = re.findall(r"[a-z]+", texto)
    if not palabras:
        return None
    return palabras[0] if palabras[0] in FORMATOS else None


def parsear_formato(respuesta: str | None) -> str:
    """Respuesta del clasificador → Formato; inválida o vacía → `entrevista` con log."""
    formato = normalizar_formato(respuesta)
    if formato:
        return formato
    print(f"⚠️ Formato inválido del clasificador ({(respuesta or '')[:60]!r}) — uso '{FORMATO_POR_DEFECTO}'")
    return FORMATO_POR_DEFECTO


def formato_a_categoria(formato: str | None) -> str:
    """`entrevista|charla → podcast`, `monologo|clase → business` (default podcast)."""
    return _CATEGORIA_POR_FORMATO.get(normalizar_formato(formato) or FORMATO_POR_DEFECTO, "podcast")


def _renglones(transcript: dict | None) -> list[str]:
    t = transcript or {}
    fuente = t.get("lines") or t.get("segments") or []
    return [(r.get("text") or "").strip() for r in fuente if (r.get("text") or "").strip()]


def extractos_para_clasificar(transcript: dict | None, chars: int = EXTRACTO_CHARS) -> dict[str, str]:
    """
    Tres extractos de ~`chars` caracteres: inicio, mitad y final del video.
    Un video corto (texto total ≤ 3 extractos) devuelve todo en `inicio`.
    """
    texto = " ".join(_renglones(transcript))
    if not texto and transcript:
        texto = (transcript.get("text") or "").strip()
    if len(texto) <= 3 * chars:
        return {"inicio": texto, "mitad": "", "final": ""}
    medio = len(texto) // 2 - chars // 2
    return {
        "inicio": texto[:chars],
        "mitad": texto[medio:medio + chars],
        "final": texto[-chars:],
    }


# ─── Verificación posterior de publicidad ────────────────────────────────────
#
# La Pasada A tiene la instrucción de no proponer avisos, pero la verificación
# es barata y no depende del modelo: se marcan las Líneas con palabras clave
# de aviso, se arman tramos y se descarta el candidato que cae > 50 % adentro.
#
# Una marca **fuerte** alcanza sola para abrir un tramo ("voy a tirar un
# chivo", "auspiciado por", "código de descuento"). "Publicidad" suelta no es
# marca: en una charla se habla de publicidades sin estar pasando una. Las
# **débiles** ("descargá la app", "la conseguís en", "comprá tus entradas")
# necesitan ser dos distintas cerca, porque sueltas aparecen en una clase de
# tecnología. Después el tramo se extiende por la marca del aviso: una
# palabra con mayúscula que se repite en la zona, casi no aparece en el resto
# del video y se nombra en una Línea de aviso (DiDi, Oslava).

_FUERTES = [
    # "voy a tirar un chivo", "permitime hacer publicidad", "vamos a pasar el auspicio"
    r"\b(voy a|vamos a|dejame|dejenme|permitime|permitanme|tengo que|hay que) "
    r"(hacer|tirar|pasar|meter|leer) (un |una |el |la )?(poco de |chivo |chivado )?"
    r"(publicidad|chivo|chivado|chivito|pnt|auspicio|aviso)\b",
    r"\btirar un chiv",
    r"\bauspiciad[oa] por\b",
    r"\bnos auspicia\b",
    r"\bauspiciante",
    r"\bpatrocinad[oa] por\b",
    r"\bpatrocinador",
    r"\bnuestro sponsor\b",
    r"\bcodigo (de descuento|promocional)\b",
    r"\bcupon de descuento\b",
    r"\blink en la descripcion\b",
]
_DEBILES = [
    r"\bdescarg(a|ate|ala|alo)\b.{0,25}\bapp\b",
    r"\bla app\b",
    r"\bapp de\b",
    r"\bconseg(u|ui)s\b",
    r"\bbusca(la|lo)\b",
    r"\bpedi(la|lo)\b",
    r"\bcompra (tus|tu|las|la) (entradas|entrada)\b",
    r"\bparticipa (ahora|ya|desde)\b",
    r"\binscribi(te)?\b.{0,25}\bapp\b",
    r"\b\w+\.com\b",
    r"\bya esta en (los )?cines\b",
    r"\ben tu tienda\b",
    r"\bdescuento\b",
    r"\bte permite\b",
    r"\btus puntos\b",
    r"\bsi sos (cliente|conductor|usuario)\b",
    r"\bpodes (acceder|ganar|participar|pedir)\b",
    r"\b(nos|te|les) trajo la gente de\b",
]
_RE_FUERTES = [re.compile(p) for p in _FUERTES]
_RE_DEBILES = [re.compile(p) for p in _DEBILES]

# Tramos: marcas separadas por menos que esto se unen.
PUBLICIDAD_HUECO_SEG = 20.0
# Extensión por marca: cuánto antes y después del tramo se busca la marca.
PUBLICIDAD_MARCA_ANTES_SEG = 30.0
PUBLICIDAD_MARCA_DESPUES_SEG = 90.0
# Solape del candidato con los tramos de aviso para descartarlo.
PUBLICIDAD_SOLAPE_MAX = 0.5

_NO_MARCA = {
    "que", "qué", "como", "cómo", "con", "para", "pero", "por", "porque", "los", "las",
    "una", "uno", "este", "esta", "eso", "esto", "ese", "esa", "ahí", "aca", "acá",
    "bueno", "mirá", "mira", "vamos", "gracias", "chicos", "che", "sí", "si", "yo",
    "vos", "nos", "ustedes", "buenas", "hola", "dale", "ahora", "cuando", "donde",
    "dónde", "también", "tambien", "todo", "todos", "nada", "muy", "más", "mas",
    "the", "and", "you", "este", "otro", "otros", "otra", "sin", "desde", "hasta",
    "entonces", "después", "despues", "ya", "hay", "fue", "era", "son", "soy",
}


def _norm(texto: str) -> str:
    return _sin_acentos(texto or "").lower()


def _marcas_de_aviso(texto: str) -> tuple[bool, set[int]]:
    """(¿tiene una marca fuerte?, índices de las marcas débiles presentes)."""
    t = _norm(texto)
    return (
        any(r.search(t) for r in _RE_FUERTES),
        {i for i, r in enumerate(_RE_DEBILES) if r.search(t)},
    )


def _es_linea_de_aviso(texto: str) -> bool:
    fuerte, debiles = _marcas_de_aviso(texto)
    return fuerte or bool(debiles)


def _es_semilla(textos: list[str]) -> bool:
    """Un grupo abre un tramo con una marca fuerte o con dos débiles distintas."""
    debiles: set[int] = set()
    for t in textos:
        fuerte, d = _marcas_de_aviso(t)
        if fuerte:
            return True
        debiles |= d
    return len(debiles) >= 2


def _marcas(texto: str) -> set[str]:
    """Palabras con mayúscula inicial (candidatas a marca comercial)."""
    out = set()
    for w in re.findall(r"\b[A-ZÁÉÍÓÚÑ][\wáéíóúñ]{2,}\b", texto or ""):
        if w.lower() not in _NO_MARCA:
            out.add(w.lower())
    return out


def _lineas(transcript_o_lineas) -> list[dict]:
    if isinstance(transcript_o_lineas, dict):
        return transcript_o_lineas.get("lines") or transcript_o_lineas.get("segments") or []
    return list(transcript_o_lineas or [])


def detectar_publicidad(transcript_o_lineas) -> list[tuple[float, float]]:
    """
    Tramos `(inicio, fin)` en segundos que parecen un aviso o un auspicio,
    a partir de las Líneas (o segmentos) del transcript. Solo palabras clave:
    sin red ni modelo.
    """
    lineas = [
        (float(l.get("start", 0)), float(l.get("end", l.get("start", 0))), l.get("text") or "")
        for l in _lineas(transcript_o_lineas)
        if l.get("start") is not None
    ]
    if not lineas:
        return []
    lineas.sort(key=lambda x: x[0])

    # 1. Grupos de Líneas marcadas, cerca unas de otras.
    grupos: list[list[int]] = []
    for i, (ini, _fin, txt) in enumerate(lineas):
        if not _es_linea_de_aviso(txt):
            continue
        if grupos and ini - lineas[grupos[-1][-1]][1] <= PUBLICIDAD_HUECO_SEG:
            grupos[-1].append(i)
        else:
            grupos.append([i])
    semillas = [g for g in grupos if _es_semilla([lineas[i][2] for i in g])]
    if not semillas:
        return []

    total_marcas = Counter()
    for _ini, _fin, txt in lineas:
        total_marcas.update(_marcas(txt))

    tramos: list[tuple[float, float]] = []
    for g in semillas:
        ini, fin = lineas[g[0]][0], lineas[g[-1]][1]
        # 2. Extensión por marca: repetida en la zona, concentrada ahí y
        # nombrada en alguna Línea de aviso (un nombre propio que solo se
        # repite en la charla no extiende el tramo).
        zona = [
            (a, b, t) for a, b, t in lineas
            if ini - PUBLICIDAD_MARCA_ANTES_SEG <= a <= fin + PUBLICIDAD_MARCA_DESPUES_SEG
        ]
        en_zona = Counter()
        en_avisos: set[str] = set()
        for _a, _b, t in zona:
            en_zona.update(_marcas(t))
            if _es_linea_de_aviso(t):
                en_avisos |= _marcas(t)
        marcas = {
            m for m, n in en_zona.items()
            if n >= 2 and n >= 0.6 * total_marcas[m] and m in en_avisos
        }
        miembros = [(a, b) for a, b, t in zona if (_marcas(t) & marcas)]
        miembros += [(lineas[i][0], lineas[i][1]) for i in g]
        miembros.sort()
        # 3. Cadena de miembros con huecos cortos que incluye la semilla.
        cadena_ini, cadena_fin = ini, fin
        cambio = True
        while cambio:
            cambio = False
            for a, b in miembros:
                if a <= cadena_fin + PUBLICIDAD_HUECO_SEG and b >= cadena_ini - PUBLICIDAD_HUECO_SEG:
                    if a < cadena_ini or b > cadena_fin:
                        cadena_ini, cadena_fin = min(cadena_ini, a), max(cadena_fin, b)
                        cambio = True
        tramos.append((cadena_ini, cadena_fin))

    tramos.sort()
    unidos: list[tuple[float, float]] = []
    for a, b in tramos:
        if unidos and a <= unidos[-1][1]:
            unidos[-1] = (unidos[-1][0], max(unidos[-1][1], b))
        else:
            unidos.append((a, b))
    return unidos


def solape_con_publicidad(inicio: float, fin: float, tramos: list[tuple[float, float]]) -> float:
    """Fracción de [inicio, fin] que cae dentro de tramos de publicidad (0–1)."""
    dur = fin - inicio
    if dur <= 0:
        return 0.0
    dentro = sum(max(0.0, min(fin, b) - max(inicio, a)) for a, b in tramos)
    return min(1.0, dentro / dur)


def descartar_publicidad(
    momentos: list[dict],
    transcript_o_lineas,
    umbral: float = PUBLICIDAD_SOLAPE_MAX,
) -> tuple[list[dict], list[dict]]:
    """
    Separa los candidatos con más de `umbral` de su duración dentro de
    tramos de publicidad detectados. Devuelve (conservados, descartados) y
    loguea cada descarte.
    """
    tramos = detectar_publicidad(transcript_o_lineas)
    if not tramos:
        return list(momentos), []
    conservados, descartados = [], []
    for m in momentos:
        try:
            ini, fin = float(m.get("start_time")), float(m.get("end_time"))
        except (TypeError, ValueError, AttributeError):
            conservados.append(m)
            continue
        solape = solape_con_publicidad(ini, fin, tramos)
        if solape > umbral:
            descartados.append({**m, "solape_publicidad": round(solape, 2)})
            print(f"   🚫 Publicidad: descarto el candidato {ini:.0f}–{fin:.0f}s "
                  f"({solape:.0%} dentro de un aviso)")
        else:
            conservados.append(m)
    return conservados, descartados
