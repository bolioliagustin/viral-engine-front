"""
Mediciones que no contaminan producción (W19, docs/PLAN_MEJORA.md §4.1).

El `.env` de la raíz apunta a la base de la beta. Una medición que escribe
en sus cachés le puede servir un análisis experimental a un job real (la
misma clase de bug que `fb287cba`). Por eso, en los tiers `seleccion` y
`e2e`, mientras dura `sin_cache_de_produccion()`:

- la caché de análisis (`analysis_cache`) y la de categoría
  (`category_cache`) **no se leen ni se escriben**;
- `save_transcript` no escribe en `transcription_cache`: el transcript se
  guarda **solo localmente** en `downloads/eval_transcripts/`;
- los transcripts sí se leen (solo lectura): primero la copia local del
  eval, después la caché de Supabase;
- `cache_purge.purge_video_cache` (W18, la llama el Cortacircuitos) purga
  solo archivos locales, nunca Supabase, y la copia local del eval de ese
  video deja de servirse hasta que se guarde una nueva.

Todos los imports de esas funciones en el worker son perezosos (dentro de la
función que las usa), así que parchear el atributo del módulo alcanza.

Variable de entorno: `EVAL_CACHE_PRODUCCION=1` desactiva el aislamiento
(vuelve al comportamiento viejo, que lee y escribe las cachés). Default:
apagada, o sea aislado.
"""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Iterator

WORKER_DIR = Path(__file__).resolve().parent.parent
EVAL_TRANSCRIPTS_DIR = WORKER_DIR / "downloads" / "eval_transcripts"

# Registro de lo que se intentó hacer contra las cachés durante el bloque
# (lo leen los tests y el JSON de la corrida).
INTENTOS: dict[str, int] = {}


def aislamiento_activo() -> bool:
    return os.getenv("EVAL_CACHE_PRODUCCION", "").strip().lower() not in ("1", "true", "yes")


def _contar(nombre: str) -> None:
    INTENTOS[nombre] = INTENTOS.get(nombre, 0) + 1


# ─── Almacén local de transcripts del eval ──────────────────────────────────

def ruta_transcript_local(youtube_id: str) -> Path:
    return EVAL_TRANSCRIPTS_DIR / f"{youtube_id}.json"


def leer_transcript_local(youtube_id: str) -> tuple[dict, dict] | None:
    """(transcript, video_info) guardados por el eval, o None."""
    ruta = ruta_transcript_local(youtube_id)
    if not ruta.exists():
        return None
    with open(ruta, encoding="utf-8") as f:
        data = json.load(f)
    return data["transcript"], data.get("video_info") or {"id": youtube_id}


def guardar_transcript_local(youtube_id: str, transcript: dict, video_info: dict | None = None, origen: str = "") -> Path:
    ruta = ruta_transcript_local(youtube_id)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(
            {"transcript": transcript, "video_info": video_info or {"id": youtube_id}, "origen": origen},
            f, ensure_ascii=False,
        )
    return ruta


# ─── Parche de las cachés de producción ─────────────────────────────────────

@contextlib.contextmanager
def sin_cache_de_produccion(activo: bool | None = None) -> Iterator[dict[str, int]]:
    """
    Neutraliza las cachés de producción mientras dura el bloque. Devuelve el
    dict de intentos (nombre de función → cuántas veces se la llamó).
    """
    activo = aislamiento_activo() if activo is None else activo
    INTENTOS.clear()
    if not activo:
        yield INTENTOS
        return

    from services import analysis_cache as ac
    from services import cache_purge as cp
    from services import transcript_cache as tc

    purge_original = getattr(cp, "purge_video_cache", None)
    purgados: set[str] = set()

    get_transcript_original = tc.get_cached_transcript

    def _nada(nombre, retorno):
        def _f(*args, **kwargs):
            _contar(nombre)
            return retorno
        _f.__name__ = f"aislado_{nombre}"
        return _f

    def _save_transcript_local(video_id, transcript, language=None, duration_seconds=None, source=None, model=None):
        # Solo local, y solo el transcript completo (captions no le sirven al eval).
        _contar("save_transcript")
        if source and source != "supadata":
            guardar_transcript_local(video_id, transcript, origen=f"generado en eval ({source}:{model})")
            purgados.discard(video_id)
            return True
        return False

    def _get_transcript(video_id, source=None, model=None):
        # Lectura permitida; primero la copia local del eval (sirve con la
        # base caída), después la caché de producción en solo lectura.
        if source and source != "supadata" and video_id not in purgados:
            local = leer_transcript_local(video_id)
            if local:
                return local[0]
        return get_transcript_original(video_id, source=source, model=model)

    def _purge(video_id, *args, **kwargs):
        _contar("purge_video_cache")
        purgados.add(video_id)
        kwargs["include_supabase"] = False
        return purge_original(video_id, *args, **kwargs)

    reemplazos = {
        (ac, "get_cached_analysis"): _nada("get_cached_analysis", None),
        (ac, "get_cached_analysis_row"): _nada("get_cached_analysis_row", None),
        (ac, "save_analysis"): _nada("save_analysis", False),
        (ac, "get_cached_category"): _nada("get_cached_category", None),
        (ac, "save_category"): _nada("save_category", False),
        (ac, "delete_cached_analysis"): _nada("delete_cached_analysis", 0),
        (tc, "save_transcript"): _save_transcript_local,
        (tc, "get_cached_transcript"): _get_transcript,
        (cp, "purge_video_cache"): _purge,
    }
    # Si otra línea renombra o saca alguna función, se parchea lo que exista;
    # el test de aislamiento (tabla por tabla contra un Supabase falso) es la
    # red que avisa si aparece un camino nuevo de escritura.
    originales = {
        clave: getattr(clave[0], clave[1]) for clave in reemplazos if hasattr(clave[0], clave[1])
    }
    try:
        for (mod, nombre) in originales:
            setattr(mod, nombre, reemplazos[(mod, nombre)])
        yield INTENTOS
    finally:
        for (mod, nombre), fn in originales.items():
            setattr(mod, nombre, fn)
