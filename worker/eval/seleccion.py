"""
Tier `seleccion` (W19): clasificador + Pasada A, con el código de la rama,
sobre el transcript cacheado de cada video con Referencias, N repeticiones
independientes, y métricas de cobertura contra las Referencias.

Aislado de producción por construcción (`aislamiento.sin_cache_de_produccion`):
no lee ni escribe `analysis_cache` ni `category_cache`, no escribe
`transcription_cache`, y el costo se acumula en memoria con `EVAL_DRY_RUN=1`
(nada va a `job_usage_events`).

El JSON de la corrida guarda los candidatos de cada repetición, así que las
métricas se pueden **recalcular** gratis cuando cambian las Referencias
(p. ej. después de que Agustín valida): `--recalcular <corrida.json>`.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import aislamiento
import eval_metrics as em
import referencias as refs

# Paralelismo por defecto: 3 repeticiones × 8 videos entran en < 15 min con 4
# (una Pasada A de un video de 110 min tarda 30–60 s). Con 8, OpenRouter
# devolvió 402 por "in-flight budget" cuando el saldo era bajo (23-sep).
DEFAULT_WORKERS = 4

_captura = threading.local()


def cargar_transcript_eval(video: dict, log: Callable[[str], None] = print) -> tuple[dict, dict] | None:
    """
    Transcript `whisper_full` del video, en este orden: copia local del eval
    (`downloads/eval_transcripts/`), caché de Supabase en solo lectura, y
    como último recurso se genera una vez y se guarda **solo localmente**.
    """
    yt = video.get("youtube_id")
    if not yt:
        return None
    local = aislamiento.leer_transcript_local(yt)
    if local:
        log(f"   ♻️ Transcript local del eval ({len(local[0].get('lines') or [])} Líneas)")
        return local

    from services.transcriber import full_transcript_model
    from services import transcript_cache as tc

    with aislamiento.sin_cache_de_produccion(activo=True):
        cached = tc.get_cached_transcript(yt, source="whisper_full", model=full_transcript_model())
    if cached and cached.get("lines"):
        video_info = _metadata(yt, cached)
        aislamiento.guardar_transcript_local(
            yt, cached, video_info, origen=f"transcription_cache {yt}:whisper_full (lectura)",
        )
        log(f"   ✅ Transcript desde transcription_cache (solo lectura), copiado local")
        return cached, video_info

    log("   🌐 Transcript no cacheado — generándolo (se guarda solo local)...")
    os.environ.setdefault("TRANSCRIPT_SOURCE", "whisper_full")
    from services.yt_transcript import get_youtube_transcript
    with aislamiento.sin_cache_de_produccion(activo=True):
        transcript, video_info = get_youtube_transcript(
            video.get("url") or f"https://www.youtube.com/watch?v={yt}"
        )
    aislamiento.guardar_transcript_local(yt, transcript, video_info, origen="generado por el tier seleccion")
    return transcript, video_info


def _metadata(yt: str, transcript: dict) -> dict:
    try:
        from services.yt_transcript import get_video_metadata
        info = dict(get_video_metadata(yt) or {})
    except Exception:
        info = {}
    info["id"] = yt
    if not info.get("duration"):
        info["duration"] = transcript.get("duration")
    return info


def _candidato(m: dict, orden: int) -> dict:
    return {
        "orden": orden,
        "start_time": m.get("start_time"),
        "end_time": m.get("end_time"),
        "rank_score": m.get("rank_score"),
        "hook": (m.get("hook") or "")[:120],
    }


def correr_pasada_a(transcript: dict, video_info: dict) -> dict[str, Any]:
    """
    Una corrida de clasificador + Pasada A por el camino de producción
    (`processor.analyze_with_openrouter`), con las cachés neutralizadas.
    Devuelve categoría, candidatos (con `rank_score` de la Pasada A),
    costo y segundos.
    """
    from context.job_context import clear_job_context, set_job_context
    from services import moment_selector, processor
    from services import usage_tracker as ut

    job_id = f"eval-seleccion-{uuid.uuid4()}"
    _captura.categoria = None
    _captura.result_dict = None
    set_job_context(job_id=job_id)
    t0 = time.time()
    try:
        analysis = processor.analyze_with_openrouter(transcript, dict(video_info))
    finally:
        clear_job_context()
    segundos = round(time.time() - t0, 1)
    rollup = ut._job_rollups.pop(job_id, None) or {}

    raw = getattr(_captura, "result_dict", None)
    if not (raw and isinstance(raw.get("candidates_all"), list)):
        # La Pasada A falló y el pipeline cayó al mega-prompt legacy: eso no
        # mide la selección (trae ~5 momentos), la repetición no cuenta.
        raise RuntimeError(
            f"la Pasada A cayó al mega-prompt de respaldo ({len(analysis.viral_moments)} momentos): repetición inválida"
        )
    fuente = raw["candidates_all"]
    candidatos = [_candidato(m, i) for i, m in enumerate(fuente) if isinstance(m, dict)]
    return {
        "categoria": getattr(_captura, "categoria", None),
        "candidatos": [c for c in candidatos if c["start_time"] is not None and c["end_time"] is not None],
        "costo_usd": round(float(rollup.get("total_cost_usd") or 0), 6),
        "costo_por_tarea": rollup.get("by_task"),
        "segundos": segundos,
    }


def _instalar_capturas():
    """Envuelve clasificador y Pasada A para capturar su salida por hilo."""
    from services import moment_selector, processor

    cat_original = processor.get_video_category
    sel_original = moment_selector.select_moments

    def _cat(*a, **k):
        c = cat_original(*a, **k)
        _captura.categoria = c
        return c

    def _sel(*a, **k):
        r = sel_original(*a, **k)
        _captura.result_dict = r
        return r

    processor.get_video_category = _cat
    moment_selector.select_moments = _sel

    def _restaurar():
        processor.get_video_category = cat_original
        moment_selector.select_moments = sel_original
    return _restaurar


def metricas_de_rep(rep: dict, doc: dict, *, incluir_borradores: bool, duracion: float | None) -> dict | None:
    if rep.get("error"):
        return None
    momentos = refs.momentos_validados(doc, incluir_borradores=incluir_borradores)
    return em.metricas_referencias(
        rep.get("candidatos") or [], momentos,
        duracion_sec=duracion, excluir=doc.get("excluir") or [],
    )


def correr_seleccion(
    videos: list[dict],
    *,
    reps: int,
    incluir_borradores: bool,
    workers: int = DEFAULT_WORKERS,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Corre el tier y devuelve el dict de la corrida (por video + agregado)."""
    os.environ["EVAL_DRY_RUN"] = "1"  # usage_tracker: rollup en memoria, sin insertar eventos
    t0 = time.time()

    por_video: list[dict] = []
    tareas: list[tuple[dict, int]] = []
    for video in videos:
        entrada = {
            "id": video["id"], "youtube_id": video.get("youtube_id"),
            "formato": video.get("formato"), "reps": [], "errores": [],
        }
        doc = refs.cargar_referencias(video.get("youtube_id") or "")
        entrada["referencias"] = refs.estado_validacion(doc)
        loaded = cargar_transcript_eval(video, log=log)
        if not loaded:
            entrada["errores"].append("sin_transcript")
            por_video.append(entrada)
            continue
        transcript, video_info = loaded
        entrada["duracion_sec"] = float(transcript.get("duration") or video_info.get("duration") or 0)
        entrada["_transcript"], entrada["_video_info"], entrada["_doc"] = transcript, video_info, doc
        por_video.append(entrada)
        tareas += [(entrada, i) for i in range(reps)]

    def _una(entrada: dict, i: int) -> dict:
        try:
            r = correr_pasada_a(entrada["_transcript"], entrada["_video_info"])
            log(f"   ✔ {entrada['id']} rep {i + 1}: {len(r['candidatos'])} candidatos, "
                f"${r['costo_usd']:.4f}, {r['segundos']:.0f}s")
            return {"rep": i + 1, **r}
        except Exception as e:
            log(f"   ❌ {entrada['id']} rep {i + 1}: {str(e)[:160]}")
            return {"rep": i + 1, "error": f"{type(e).__name__}: {str(e)[:200]}"}

    log(f"🎯 tier seleccion: {len(tareas)} corridas de Pasada A ({reps} por video), {workers} en paralelo")
    restaurar = _instalar_capturas()
    try:
        with aislamiento.sin_cache_de_produccion() as intentos:
            with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
                futuros = [(e, pool.submit(_una, e, i)) for e, i in tareas]
                for entrada, fut in futuros:
                    entrada["reps"].append(fut.result())
            intentos_cache = dict(intentos)
    finally:
        restaurar()

    for entrada in por_video:
        entrada.pop("_transcript", None)
        entrada.pop("_video_info", None)
        entrada["reps"].sort(key=lambda r: r["rep"])
    corrida = {
        "tier": "seleccion",
        "reps": reps,
        "incluir_borradores": incluir_borradores,
        "aislamiento": {"activo": aislamiento.aislamiento_activo(), "intentos_bloqueados": intentos_cache},
        "videos": por_video,
        "wall_seconds": round(time.time() - t0, 1),
    }
    return calcular_metricas(corrida, incluir_borradores=incluir_borradores)


def completar_corrida(
    corrida: dict,
    videos: list[dict],
    *,
    workers: int = DEFAULT_WORKERS,
    log: Callable[[str], None] = print,
) -> dict:
    """Rehace solo las repeticiones con error de una corrida guardada y recalcula."""
    os.environ["EVAL_DRY_RUN"] = "1"
    t0 = time.time()
    por_id = {v["id"]: v for v in videos}
    tareas = []
    for v in corrida["videos"]:
        fallidas = [r for r in v["reps"] if r.get("error")]
        if not fallidas or v["id"] not in por_id:
            continue
        loaded = cargar_transcript_eval(por_id[v["id"]], log=log)
        if not loaded:
            continue
        tareas += [(v, r, loaded) for r in fallidas]
    log(f"🔁 completar: {len(tareas)} repeticiones fallidas, {workers} en paralelo")

    def _una(v, r, loaded):
        try:
            nuevo = correr_pasada_a(*loaded)
            log(f"   ✔ {v['id']} rep {r['rep']}: {len(nuevo['candidatos'])} candidatos, ${nuevo['costo_usd']:.4f}")
            return {"rep": r["rep"], **nuevo, "reintento_de": r.get("error", "")[:120],
                    "costo_descartado_usd": r.get("costo_descartado_usd") or r.get("costo_usd")}
        except Exception as e:
            log(f"   ❌ {v['id']} rep {r['rep']}: {str(e)[:160]}")
            return {"rep": r["rep"], "error": f"{type(e).__name__}: {str(e)[:200]}"}

    restaurar = _instalar_capturas()
    try:
        with aislamiento.sin_cache_de_produccion() as intentos:
            with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
                futuros = [(v, r, pool.submit(_una, v, r, loaded)) for v, r, loaded in tareas]
                for v, r, fut in futuros:
                    v["reps"][v["reps"].index(r)] = fut.result()
            bloqueados = dict(intentos)
    finally:
        restaurar()
    previos = corrida.setdefault("aislamiento", {}).setdefault("intentos_bloqueados", {})
    for k, n in bloqueados.items():
        previos[k] = previos.get(k, 0) + n
    corrida["wall_seconds"] = round((corrida.get("wall_seconds") or 0) + time.time() - t0, 1)
    corrida.setdefault("completada", []).append({"reps_rehechas": len(tareas), "segundos": round(time.time() - t0, 1)})
    return calcular_metricas(corrida, incluir_borradores=corrida.get("incluir_borradores", False))


def calcular_metricas(corrida: dict, *, incluir_borradores: bool, docs: dict[str, dict] | None = None) -> dict:
    """
    (Re)calcula métricas por repetición, por video y agregadas a partir de
    los candidatos guardados en la corrida y las Referencias actuales.
    """
    for v in corrida["videos"]:
        doc = v.pop("_doc", None)
        if docs is not None:
            doc = docs.get(v["youtube_id"])
        if doc is None:
            doc = refs.cargar_referencias(v.get("youtube_id") or "")
        v["referencias"] = refs.estado_validacion(doc)
        if not doc or not refs.momentos_validados(doc, incluir_borradores=incluir_borradores):
            v["agregado"] = None
            v.setdefault("errores", []).append("sin_referencias_validadas")
            continue
        v["errores"] = [e for e in v.get("errores") or [] if e != "sin_referencias_validadas"]
        for rep in v["reps"]:
            rep["metricas"] = metricas_de_rep(rep, doc, incluir_borradores=incluir_borradores, duracion=v.get("duracion_sec"))
        v["agregado"] = em.agregar_repeticiones(v["reps"])
        # Incluye lo pagado en repeticiones descartadas (fallback, reintentos)
        v["costo_usd"] = round(sum((r.get("costo_usd") or 0) + (r.get("costo_descartado_usd") or 0) for r in v["reps"]), 6)
        v["categorias"] = sorted({r.get("categoria") for r in v["reps"] if r.get("categoria")})
    corrida["incluir_borradores"] = incluir_borradores
    corrida["agregado"] = em.agregar_seleccion(corrida["videos"])
    corrida["costo_total_usd"] = round(sum(v.get("costo_usd") or 0 for v in corrida["videos"]), 6)
    return corrida


def resumen_legible(corrida: dict) -> list[str]:
    def f(x, pct=True):
        if not x or x.get("media") is None:
            return "  n/a  "
        return f"{x['media']:.0%}±{x['desvio']:.0%}" if pct else f"{x['media']:.1f}±{x['desvio']:.1f}"

    out = [
        "═══════════════════════════════════════════",
        f"📊 tier seleccion — {corrida['reps']} reps"
        + (" (INCLUYE Referencias sin validar)" if corrida.get("incluir_borradores") else ""),
        "═══════════════════════════════════════════",
        f"{'video':<28} {'refs A':>6} {'recall_A':>11} {'recall_AB':>11} {'parcial_A':>11} {'partidas':>9} {'min_cuarto':>11} {'p@10':>9}",
    ]
    for v in corrida["videos"]:
        a = v.get("agregado")
        if not a:
            out.append(f"{v['id']:<28} {'—':>6}  {', '.join(v.get('errores') or [])}")
            continue
        n_a = next((r["metricas"]["n_referencias_a"] for r in v["reps"] if r.get("metricas")), 0)
        out.append(
            f"{v['id']:<28} {n_a:>6} {f(a['recall_completo']):>11} {f(a['recall_completo_ab']):>11} "
            f"{f(a['recall_parcial']):>11} {f(a['historias_partidas'], pct=False):>9} "
            f"{f(a['min_cuarto']):>11} {f(a['precision_ref@10']):>9}"
        )
    g = corrida["agregado"]
    out += [
        "───────────────────────────────────────────",
        f"{'agregado (macro)':<28} {'':>6} {f(g['recall_completo']):>11} {f(g['recall_completo_ab']):>11} "
        f"{f(g['recall_parcial']):>11} {f(g['historias_partidas'], pct=False):>9} {f(g['min_cuarto']):>11} "
        f"{f(g['precision_ref@10']):>9}",
        f"Costo: ${corrida.get('costo_total_usd') or 0:.4f} · reloj: {(corrida.get('wall_seconds') or 0) / 60:.1f} min",
    ]
    return out
