"""
Pasada A — Selección de momentos virales (Fase 2, Plan calidad IA).

Prompt enfocado SOLO en encontrar momentos: timing, hook conceptual, trigger
emocional, verificación de frases y scores preliminares. SIN copy (el copy
completo se genera en la pasada B post-Whisper con el texto real del clip).

Sobre-generación: pedimos `candidate_count()` candidatos con score
preliminar (W9-B: `min(30, max(6, minutos // 2))`), el juez evalúa TODOS en
main.py y se entrega por umbral (`select_finalists`, `DELIVERY_JUDGE_MIN`),
no una cantidad fija. Esto reemplaza la densidad fija "video >5min = 5
momentos" por selección competitiva entre candidatos.
"""
import copy
import json
import os
import re
import time
from dataclasses import dataclass

from config.model_tiers import output_language_instruction
from config.llm_chat import build_chat_kwargs, log_llm_usage
from services.validation import CLIP_MAX_DURATION_SEC


def target_moment_count(duration_sec: float) -> int:
    """N final de momentos según duración (misma densidad que el legacy)."""
    if duration_sec < 90:
        return 1
    if duration_sec < 300:
        return 3
    return 5


def candidate_count(duration_sec: float) -> int:
    """
    Candidatos a pedirle a la Pasada A (W9-B, docs/PLAN_CALIDAD.md §9 W9):
    min(30, max(6, minutos // 2)). Ya no se acota a `target+1` — TODOS los
    candidatos que devuelva la Pasada A se evalúan de verdad (descarga +
    Whisper + juez) en main.py, así que el techo de cuántos pedir lo pone
    el costo objetivo (~US$0.0015/candidato evaluado, medido en
    eval/runs/2026-09-18-w2c-verificacion.json), no el `target` final de
    entrega (que ahora es un piso, no un tope — ver
    moment_selector.select_finalists / DELIVERY_MAX_CLIPS).
    """
    minutes = max(1, int(duration_sec // 60))
    return min(30, max(6, minutes // 2))


# ─── W2: el juez elige ───────────────────────────────────────────────────────
# docs/PLAN_CALIDAD.md §4 W2 (causas C4/C5): la Pasada A sobre-genera pero el
# auto-score del propio LLM no discrimina (8-9 a casi todo); el juez corría
# después de renderizar y no decidía nada. Ahora el juez evalúa a TODOS los
# candidatos (W9-B: ya no se trunca a un pool chico, ver
# `rank_and_prune_candidates`) antes de decidir qué se entrega.

# ─── W9-B: entrega por umbral, no por target fijo ────────────────────────────
# docs/PLAN_CALIDAD.md §9 W9, docs/adr/0008: el plan de créditos (1/3/5) ya no
# limita la cantidad de clips entregados — se entrega todo lo que pase la
# nota del juez, hasta un tope de costo/UX. `target_moment_count` (el 1/3/5
# de siempre) pasa de "cantidad exacta a entregar" a "piso mínimo
# garantizado" (junto con DELIVERY_MIN_CLIPS) en `select_finalists`.
#
# DELIVERY_MAX_CLIPS: 12, no 30. Con candidate_count() pidiendo hasta 30
# candidatos para un video de 60 min, evaluarlos cuesta ~30×US$0.0015≈
# US$0.045 (Whisper+juez, medido). Entregar cada candidato que pasa el
# umbral cuesta además la Pasada B (~US$0.0073/clip, medido en
# eval/runs/2026-09-18-w2c-verificacion.json → cost_by_task.copy/clips_count).
# Si el propio objetivo de Fase 0 se cumple (juez sube y MUCHOS candidatos
# pasan el umbral), entregar los 30 costaría 30×0.0073≈US$0.22 solo de
# copy — sumado a la evaluación, ~US$0.27/job, muy por encima del tope de
# US$0.15/job (docs/PLAN_CALIDAD.md §3). Con el tope en 12: peor caso
# (30 evaluados, los 12 mejores entregados) ≈ 0.045 + 12×0.0073 ≈
# US$0.13/job — bajo el tope, con margen, y sigue cumpliendo el objetivo de
# "≥8 clips por video de 60 min" de docs/ANALISIS_OPUS_CLIP.md §6 fila B.
DELIVERY_JUDGE_MIN = float(os.getenv("DELIVERY_JUDGE_MIN", "15"))     # sobre 30 (3 métricas × 10)
DELIVERY_MAX_CLIPS = int(os.getenv("DELIVERY_MAX_CLIPS", "12"))
DELIVERY_MIN_CLIPS = 3   # piso absoluto, aunque target_moment_count() sea 1

# Penalizaciones sobre la suma de notas del juez (hook+retention+shareability,
# rango teórico 3-30), en tres niveles según qué tan roto está el candidato
# (W2-C, docs/PLAN_CALIDAD.md §9 — reemplaza las penalizaciones planas de W2,
# que trataban `verification_failed` como una sola señal aunque mezclaba
# cosas muy distintas: ver services.validation.verification_failed_from_flags).
#
# FUERTE (PENALTY_BROKEN): el clip no tiene lo que dice tener.
#   hook_not_found / payoff_not_found — Verificación (CONTEXT.md) falló: la
#   frase citada por la Pasada A no se pudo anclar en el audio real, ni con
#   el matching difuso de locate_phrase. bad_segment (W3) — el segmento no
#   tiene habla plausible. Cualquiera de las tres hace que casi nunca
#   convenga entregar el candidato aunque el juez lo haya puntuado bien.
# MEDIA (PENALTY_DEGRADED): se pudo entregar algo, pero con una degradación
#   real. insufficient_source (W1) — no se pudo ampliar el margen ni
#   reintentar la descarga, se usó el mejor segmento disponible.
#   timestamps_suspect (W3) — Whisper dio tiempos sospechosos y hubo que
#   re-transcribir con el otro proveedor (si igual persiste, termina en
#   bad_segment o subs_disabled_timestamps, penalizados aparte).
# LEVE (PENALTY_MINOR): señales informativas que NO significan que el corte
#   esté mal (W2-C). late_hook / incomplete_tail — W1 ancla el clip al
#   INICIO DE ORACIÓN de la primera frase, no a su primera palabra, así que
#   unas palabras/segundos de contexto antes del hook citado son normales;
#   antes estas dos disparaban `verification_failed` completo y un candidato
#   bien cortado perdía contra uno peor (caso real: podcast_general_01,
#   candidato 1010-1050s). min_duration_reverted — el refinamiento tuvo que
#   volver a límites anteriores, pero el clip igual se entrega.
PENALTY_BROKEN = 12.0
PENALTY_DEGRADED = 6.0
PENALTY_MINOR = 2.0
PENALTY_DENSITY_OUT_OF_RANGE = 8.0   # sin cambios: sin habla plausible o timestamps rotos
PENALTY_NO_JUDGE_SCORE = 10.0        # el juez falló: nos quedamos con el auto-score, muy penalizado

# Diversidad entre candidatos entregados.
MAX_OVERLAP_RATIO = 0.30             # solapamiento temporal máximo (ver filter_overlapping_moments)
MAX_HOOK_SIMILARITY = 0.6            # Jaccard de palabras normalizadas (sin stopwords)

_STOPWORDS_ES = {
    "el", "la", "los", "las", "de", "del", "un", "una", "unos", "unas", "que",
    "y", "o", "a", "en", "por", "para", "con", "su", "sus", "es", "se", "lo",
    "al", "como", "más", "tu", "este", "esta", "esa", "ese", "no", "si", "sí",
    "le", "les", "nos", "muy", "ya", "pero", "porque", "cuando", "qué",
}


@dataclass
class CandidateEval:
    """Resultado de evaluar un candidato (W2): descarga + Whisper + ancla de
    frases (W1) + juez sobre el texto real del clip, sin Pasada B ni render.

    `index` es la posición 1-based del candidato dentro de la lista que
    devolvió la Pasada A (estable durante todo el job, antes de renumerar
    los finalistas 1..target en orden cronológico para la entrega).
    """
    index: int
    start_time: float
    end_time: float
    hook: str
    judge_scores: dict | None      # {"hook","retention","shareability","reasoning"} o None
    self_score: float              # suma de scores de la Pasada A (fallback si el juez falla)
    usable: bool = True            # False = ni siquiera hay clip_text (sin video/sin habla)
    density_out_of_range: bool = False
    # Nivel FUERTE (PENALTY_BROKEN): el clip no tiene lo que dice tener.
    hook_not_found: bool = False
    payoff_not_found: bool = False
    bad_segment: bool = False
    # Nivel MEDIO (PENALTY_DEGRADED): degradación real pero entregable.
    insufficient_source: bool = False
    timestamps_suspect: bool = False
    # Nivel LEVE (PENALTY_MINOR): informativo, no significa corte roto (W2-C).
    late_hook: bool = False
    incomplete_tail: bool = False
    min_duration_reverted: bool = False
    discard_reason: str | None = None
    # Rankeo con Jev (RANKER=jev): nota ya normalizada a la escala 0..30 del
    # Juez, o None si Jev no corrió o falló (entonces manda `judge_scores`).
    jev_rank_score: float | None = None
    jev_confidence: float | None = None


def is_broken(c: "CandidateEval") -> bool:
    """Nivel FUERTE (W2-C): el clip no tiene lo que dice tener (Verificación
    fallida o segmento sin habla). W18: no completa el piso ni suma para el
    Cortacircuitos (este último cuenta solo hook/payoff, ver main.py)."""
    return bool(c.hook_not_found or c.payoff_not_found or c.bad_segment)


def _judge_sum(c: "CandidateEval") -> float:
    """Nota base del candidato, en la escala 0..30 del Juez.

    Con `RANKER=jev`, `jev_rank_score` ya viene normalizado a esa escala y
    manda sobre la nota del Juez: ordena sin empates (el Juez empata el 13,9 %
    de los pares, ver services/ranker_jev.py). Si Jev no corrió o falló, el
    campo queda en None y sigue mandando el Juez — el fallback es el
    comportamiento de siempre.
    """
    if c.jev_rank_score is not None:
        return float(c.jev_rank_score)
    if c.judge_scores:
        try:
            return (
                float(c.judge_scores["hook"])
                + float(c.judge_scores["retention"])
                + float(c.judge_scores["shareability"])
            )
        except (KeyError, TypeError, ValueError):
            pass
    return max(0.0, c.self_score - PENALTY_NO_JUDGE_SCORE)


def score_candidate(c: "CandidateEval") -> float:
    """
    Nota de ranking (W2): suma del juez sobre el clip real (o el auto-score
    de la Pasada A muy penalizado si el juez falló) menos penalizaciones por
    señales de calidad ya conocidas, en tres niveles (W2-C, ver constantes
    PENALTY_* arriba). El auto-score NUNCA gana si el juez puntuó — es la
    causa C4 que W2 corrige. Cada nivel penaliza UNA vez aunque el candidato
    dispare más de una señal de ese nivel (son síntomas del mismo problema,
    no problemas independientes que se sumen).
    """
    if not c.usable:
        return -1000.0  # sin clip_text no hay nada que renderizar: nunca se entrega
    score = _judge_sum(c)
    if c.hook_not_found or c.payoff_not_found or c.bad_segment:
        score -= PENALTY_BROKEN
    if c.insufficient_source or c.timestamps_suspect:
        score -= PENALTY_DEGRADED
    if c.late_hook or c.incomplete_tail or c.min_duration_reverted:
        score -= PENALTY_MINOR
    if c.density_out_of_range:
        score -= PENALTY_DENSITY_OUT_OF_RANGE
    return score


def _normalize_words(text: str) -> set[str]:
    words = re.findall(r"[a-záéíóúñü0-9]+", (text or "").lower())
    return {w for w in words if w not in _STOPWORDS_ES and len(w) > 2}


def _hook_similarity(a: str, b: str) -> float:
    """Jaccard de palabras normalizadas (sin stopwords) — comparación simple,
    sin embeddings, para detectar candidatos que repiten el mismo momento."""
    wa, wb = _normalize_words(a), _normalize_words(b)
    if not wa or not wb:
        return 0.0
    inter = len(wa & wb)
    union = len(wa | wb)
    return inter / union if union else 0.0


def _overlap_ratio(a: "CandidateEval", b: "CandidateEval") -> float:
    start = max(a.start_time, b.start_time)
    end = min(a.end_time, b.end_time)
    inter = max(0.0, end - start)
    shortest = min(a.end_time - a.start_time, b.end_time - b.start_time)
    return (inter / shortest) if shortest > 0 else 0.0


def select_finalists(
    candidates: list["CandidateEval"], target: int
) -> tuple[list["CandidateEval"], list["CandidateEval"]]:
    """
    W9-B — entrega por umbral (docs/PLAN_CALIDAD.md §9 W9), no por `target`
    fijo: se entregan TODOS los candidatos usables, sin conflicto de
    diversidad, con `score_candidate() >= DELIVERY_JUDGE_MIN`, hasta
    `DELIVERY_MAX_CLIPS`. `target` (`target_moment_count`, el 1/3/5 de
    siempre) y `DELIVERY_MIN_CLIPS` (3) son ahora un PISO: si menos
    candidatos que `max(target, DELIVERY_MIN_CLIPS)` pasan el umbral, se
    completa con los siguientes mejores igual aunque no lo pasen — mejor
    un clip mediocre que entregar menos de lo mínimo (mismo criterio que
    W2, ahora aplicado a un piso más alto). Nunca se entrega un candidato
    `usable=False` (sin `clip_text`, no hay nada que renderizar), ni
    siquiera para completar el piso.

    W18 (hallazgo H4, job fb287cba): el piso tampoco se completa con
    candidatos rotos (`is_broken`: `hook_not_found`, `payoff_not_found` o
    `bad_segment`). Si hay menos entregables que el piso, se entregan menos;
    con 0, el job falla y devuelve el crédito (`_finalize_job_outcome`).

    Diversidad: un candidato se descarta si solapa > MAX_OVERLAP_RATIO en
    tiempo con uno ya elegido, o si su hook es casi el mismo (Jaccard de
    palabras > MAX_HOOK_SIMILARITY) — igual que W2. El backfill del piso
    ignora la diversidad (como antes): mejor un candidato repetido que
    entregar menos del piso.

    Returns:
        (elegidos, en orden cronológico), (descartados, con `discard_reason`)
    """
    floor = max(target, DELIVERY_MIN_CLIPS)
    ranked = sorted(candidates, key=score_candidate, reverse=True)
    selected: list[CandidateEval] = []
    deferred: list[CandidateEval] = []

    for cand in ranked:
        if not cand.usable:
            cand.discard_reason = cand.discard_reason or "sin clip_text: no se puede entregar"
            deferred.append(cand)
            continue
        if len(selected) >= DELIVERY_MAX_CLIPS:
            cand.discard_reason = f"tope: ya se alcanzaron los {DELIVERY_MAX_CLIPS} clips de DELIVERY_MAX_CLIPS"
            deferred.append(cand)
            continue
        conflict = any(
            _overlap_ratio(cand, s) > MAX_OVERLAP_RATIO
            or _hook_similarity(cand.hook, s.hook) > MAX_HOOK_SIMILARITY
            for s in selected
        )
        if conflict:
            cand.discard_reason = "diversidad: solapa o repite el hook de un candidato ya elegido"
            deferred.append(cand)
            continue
        if score_candidate(cand) >= DELIVERY_JUDGE_MIN:
            selected.append(cand)
        else:
            cand.discard_reason = "umbral: nota del juez por debajo de DELIVERY_JUDGE_MIN"
            deferred.append(cand)

    if len(selected) < floor:
        selected_idx = {c.index for c in selected}
        for cand in deferred:
            if len(selected) >= floor or len(selected) >= DELIVERY_MAX_CLIPS:
                break
            if cand.index in selected_idx or not cand.usable:
                continue
            if is_broken(cand):
                cand.discard_reason = (cand.discard_reason or "") + " · piso: roto, no rellena (W18)"
                continue
            cand.discard_reason = None
            selected.append(cand)
            selected_idx.add(cand.index)

    selected_idx = {c.index for c in selected}
    discarded = [c for c in ranked if c.index not in selected_idx]
    selected.sort(key=lambda c: c.start_time)
    return selected, discarded


def get_selection_prompt(
    duration: int,
    num_candidates: int,
    category: str = "business",
    language: str = None,
) -> str:
    """Prompt corto y estricto: solo selección de momentos, sin copy."""
    lang_instruction = output_language_instruction(language)

    if category == "podcast":
        focus = """PRIORIZA (contenido conversacional):
1. Pregunta provocadora → respuesta sorprendente (ping-pong viral)
2. Revelación personal inesperada del invitado
3. Desacuerdo o tensión creativa entre host e invitado
4. Frase memorable standalone que no necesita contexto
5. Reacción genuina (risa, incomodidad, sorpresa)

TIMING: start = inicio de la pregunta/premisa - 5s; end = fin de la respuesta/reacción + 4s."""
    else:
        focus = """PRIORIZA (contenido de un orador / educativo):
1. Contrarian truths: ideas que rompen creencias comunes
2. High utility: valor accionable inmediato
3. Deep vulnerability: admisión de errores humanos
4. Curiosity gap: declaraciones que abren loops mentales

TIMING: start = inicio del setup de la idea; end = fin del remate/conclusión."""

    return f"""Eres un editor senior de clips virales. Tu ÚNICA tarea en esta pasada es SELECCIONAR los mejores momentos del video. NO generes copy, threads ni posts — eso ocurre en otra etapa.

{lang_instruction}

MISIÓN:
Identifica los {num_candidates} MEJORES momentos candidatos del video. Sé exigente: cada momento debe funcionar como clip standalone sin contexto previo.

{focus}

REGLAS DE TIMING (CRÍTICAS):
- Usa EXACTAMENTE los timestamps de la transcripción (no los inventes).
- start_time y end_time se devuelven SIEMPRE en segundos absolutos desde el inicio del video (un número, sin formato): las marcas del transcript son referencia de lectura, y si alguna viene como [mm:ss] hay que convertirla (mm × 60 + ss).
- Un momento es una idea completa: planteo, desarrollo y remate. Entre 20 y {CLIP_MAX_DURATION_SEC:.0f} segundos. En podcasts y entrevistas lo normal es 40-90 s; en videos cortos de un solo hablante, 20-60 s. Cortá siempre donde termina una oración.
- El momento debe empezar donde empieza la IDEA (setup) y terminar donde termina (remate). No cortes a mitad de frase.
- Momentos NO solapados (máximo 20% de overlap entre candidatos).

VERIFICACIÓN ANTI-ALUCINACIÓN (OBLIGATORIA por momento):
- first_phrase_in_audio: las primeras 5-8 palabras EXACTAS que se dicen en el clip (copiadas de la transcripción).
- last_phrase_in_audio: las últimas 5-8 palabras EXACTAS del clip. DEBE terminar en . ? o ! (oración completa).
- El end_time debe caer al final de un segmento de transcripción con oración completa, NO a mitad de frase.
- Si no puedes citar las frases exactas con cierre de oración, NO incluyas ese momento.

SCORES PRELIMINARES (1-10, sé honesto — la mayoría de los momentos son 5-7):
- hook: ¿los primeros 3 segundos frenan el scroll?
- retention: ¿mantiene atención hasta el final?
- shareability: ¿alguien lo compartiría o etiquetaría a un amigo?

FORMATO JSON DE SALIDA (SOLO JSON, sin markdown):
{{
  "video_title": "Título magnético del video",
  "summary": "Resumen ejecutivo (max 200 chars)",
  "main_topics": ["tema1", "tema2", "tema3"],
  "viral_moments": [
    {{
      "start_time": 120,
      "end_time": 155,
      "clipping_reason": "Por qué este [start,end] exacto: qué setup captura y dónde remata",
      "hook": "Frase gancho conceptual del momento (1-2 líneas, en el idioma del video)",
      "viral_overlay": "HOOK CORTO MAX 4 PALABRAS UPPERCASE",
      "emotional_trigger": "Curiosidad | Miedo | Sorpresa | Codicia | Altruismo",
      "pillar_type": "authority",
      "category": "{category}",
      "sentiment_detected": "serious",
      "scores": {{"hook": 7, "retention": 6, "shareability": 8}},
      "verification": {{
        "first_phrase_in_audio": "primeras 5-8 palabras exactas",
        "last_phrase_in_audio": "últimas 5-8 palabras exactas",
        "narrative_goal": "por qué es una idea completa sin contexto"
      }}
    }}
  ]
}}

RECORDATORIO: genera {num_candidates} candidatos, ordenados del mejor al peor. SIN copy. SIN short_video_script. SOLO selección."""


def _segment_boundary_penalty(moment: dict, transcript: dict | None) -> float:
    """Penaliza momentos cuyo end_time cae a mitad de segmento sin punct."""
    if not transcript:
        return 0.0
    end_t = float(moment.get("end_time") or 0)
    segments = transcript.get("segments") or []
    for sg in segments:
        sg_start = float(sg.get("start", 0))
        sg_end = float(sg.get("end", sg_start))
        if sg_start < end_t <= sg_end + 0.5:
            txt = (sg.get("text") or "").strip()
            if txt and txt[-1] not in ".?!…":
                return 3.0  # penalización fuerte
            if abs(end_t - sg_end) > 2.0:
                return 1.5  # end lejos del fin de segmento
            return 0.0
    return 0.5


def rank_and_prune_candidates(
    result_dict: dict,
    target: int,
    transcript: dict | None = None,
) -> dict:
    """
    W9-B (docs/PLAN_CALIDAD.md §9 W9): ya NO trunca. Hasta esta línea de
    trabajo, esto podaba a un pool chico (`target + EVAL_POOL_EXTRA`, W2)
    por auto-score de la Pasada A antes de gastar en descarga+Whisper+juez;
    ahora `candidate_count()` ya pone el techo de cuántos candidatos pedirle
    al LLM (según el costo objetivo, no según `target`), y TODOS los que
    devuelve se evalúan de verdad en main.py — la selección final la hace
    el juez sobre el clip real en `select_finalists`, por umbral, no acá
    por auto-score (el auto-score del propio LLM no discrimina, causa C4,
    PLAN_CALIDAD.md §1.3). `target` queda sin usar en esta función (se
    conserva en la firma por compatibilidad con el único caller,
    `select_moments`, y porque documenta la intención de quien la llama).

    Solo anota `candidates_all` (auto-score + penalización de borde de
    segmento) para poder comparar después contra el ranking del juez.
    """
    moments = result_dict.get("viral_moments") or []

    def _score(m: dict) -> float:
        s = m.get("scores") or {}
        if isinstance(s, list) and s and isinstance(s[0], dict):
            s = s[0]
        if not isinstance(s, dict):
            base = 0.0
        else:
            try:
                base = (
                    float(s.get("hook", 0))
                    + float(s.get("retention", 0))
                    + float(s.get("shareability", 0))
                )
            except (TypeError, ValueError):
                base = 0.0
        return base - _segment_boundary_penalty(m, transcript)

    # Todos los candidatos de la Pasada A (con el score usado para rankear)
    # viajan en `candidates_all` hasta analysis_cache, para poder comparar el
    # ranking con el juez después. AnalysisResult ignora la clave.
    result_dict["candidates_all"] = [
        {**copy.deepcopy(m), "rank_score": round(_score(m), 2)} if isinstance(m, dict) else m
        for m in moments
    ]
    return result_dict


def _costo_respuesta(model: str, response) -> float:
    """Costo estimado de una llamada, con la misma cuenta que usage_tracker."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0.0
    from config.pricing import estimate_llm_cost_usd
    details = getattr(usage, "completion_tokens_details", None)
    reasoning = (getattr(details, "reasoning_tokens", None) or 0) if details is not None else 0
    return estimate_llm_cost_usd(
        model,
        getattr(usage, "prompt_tokens", None) or 0,
        getattr(usage, "completion_tokens", None) or 0,
        reasoning,
    )


def _llamar_pasada_a(messages: list[dict], client, model: str, max_retries: int, etiqueta: str = "Pasada A") -> tuple[dict, float]:
    """
    Una llamada de la Pasada A con reintentos: devuelve (result_dict, costo).
    Lanza si el LLM falla tras los reintentos o no devuelve `viral_moments`.
    """
    response_text = None
    last_error = None
    costo = 0.0
    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                **build_chat_kwargs(
                    "analysis",
                    model,
                    messages,
                    response_format={"type": "json_object"},
                )
            )
            log_llm_usage("analysis", model, response)
            costo += _costo_respuesta(model, response)
            raw = response.choices[0].message.content if response.choices else None
            if not raw or not raw.strip():
                finish = response.choices[0].finish_reason if response.choices else "no_choices"
                raise ValueError(f"LLM returned empty content (finish_reason={finish})")
            response_text = raw.strip()
            break
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                wait = 2 ** (attempt + 1)
                print(f"⚠️ {etiqueta} intento {attempt + 1} falló: {str(e)[:120]} — retry en {wait}s")
                time.sleep(wait)
            else:
                raise last_error

    # Limpiar wrapper markdown si aparece
    if response_text.startswith("```json"):
        response_text = response_text[7:]
    if response_text.startswith("```"):
        response_text = response_text[3:]
    if response_text.endswith("```"):
        response_text = response_text[:-3]
    response_text = response_text.strip()

    try:
        result_dict = json.loads(response_text)
    except json.JSONDecodeError:
        from json_repair import repair_json
        result_dict = json.loads(repair_json(response_text))
        print(f"   ✅ JSON de {etiqueta} reparado")

    if isinstance(result_dict, list):
        if len(result_dict) == 1 and isinstance(result_dict[0], dict):
            result_dict = result_dict[0]
        else:
            raise ValueError(f"{etiqueta} devolvió array de {len(result_dict)} elementos")

    moments = result_dict.get("viral_moments") if isinstance(result_dict, dict) else None
    if not isinstance(moments, list) or not moments:
        raise ValueError(f"{etiqueta} no devolvió viral_moments")
    return result_dict, costo


def _contexto_video(video_info: dict, duration: float, language: str) -> str:
    return f"""VIDEO INFO:
- Título original: {video_info.get('title', 'Desconocido')}
- Duración: {int(duration)} segundos
- Canal: {video_info.get('uploader', 'Desconocido')}
- Idioma: {language or 'es'}"""


_INSTRUCCION_TIMESTAMPS = """🎯 INSTRUCCIÓN CRÍTICA:
- Los timestamps son EXACTOS — COPIA los valores, no los adivines.
- Cita first/last_phrase_in_audio LITERALMENTE desde la transcripción."""


def _pasada_unica(
    transcript_text: str,
    video_info: dict,
    duration: float,
    category: str,
    language: str,
    client,
    model: str,
    max_retries: int,
    num_candidates: int,
) -> tuple[dict, float]:
    """La Pasada A de siempre: una llamada con el transcript completo."""
    prompt = get_selection_prompt(
        duration=int(duration),
        num_candidates=num_candidates,
        category=category,
        language=language,
    )
    context = f"""{_contexto_video(video_info, duration, language)}

📜 TRANSCRIPCIÓN OFICIAL CON TIMESTAMPS:
{transcript_text}

{_INSTRUCCION_TIMESTAMPS}"""

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"{context}\n\nSelecciona los {num_candidates} mejores momentos. Responde SOLO con JSON válido."},
    ]
    return _llamar_pasada_a(messages, client, model, max_retries)


def _mmss(seg: float) -> str:
    total = int(round(max(0.0, float(seg))))
    return f"{total // 60}:{total % 60:02d}"


# Un candidato cuyo centro cae a más de esto fuera de su Ventana se descarta:
# el modelo inventó o convirtió mal el timestamp (ver format_lines_for_prompt).
VENTANA_TOLERANCIA_SEG = 30.0


def _pasada_por_ventanas(
    ventanas,
    cupos: list[int],
    video_info: dict,
    duration: float,
    category: str,
    language: str,
    client,
    model: str,
    max_retries: int,
) -> tuple[dict | None, float, list[int]]:
    """
    Una llamada por Ventana, en paralelo (≤ VENTANAS_CONCURRENCIA_MAX), con
    el mismo `get_selection_prompt`. Cada llamada ve solo las Líneas de su
    Ventana, con timestamps absolutos, y sabe la duración total del video y
    el rango que está mirando. Devuelve (unión o None si fallaron todas,
    costo, índices de Ventanas fallidas).
    """
    from concurrent.futures import ThreadPoolExecutor

    from context.job_context import in_current_context
    from services import ventanas as vt
    from services.transcript_lines import format_lines_for_prompt

    n = len(ventanas)
    estilo = os.getenv("TRANSCRIPT_LINE_STYLE", "seconds")

    def _una(par):
        v, cupo = par
        prompt = get_selection_prompt(
            duration=int(duration), num_candidates=cupo, category=category, language=language,
        )
        texto = format_lines_for_prompt(v.lineas, style=estilo)
        context = f"""{_contexto_video(video_info, duration, language)}

🪟 VENTANA {v.indice + 1} DE {n}: esta llamada ve SOLO el tramo del segundo {int(v.inicio)} al {int(v.fin)} ({_mmss(v.inicio)}–{_mmss(v.fin)}) de un video de {int(duration)} segundos. Otras llamadas revisan el resto del video.
- Elegí momentos que ocurran dentro de este tramo.
- start_time y end_time son segundos ABSOLUTOS desde el inicio del video (los mismos números de la transcripción), NO relativos a la Ventana.

📜 TRANSCRIPCIÓN OFICIAL CON TIMESTAMPS (tramo {_mmss(v.inicio)}–{_mmss(v.fin)}):
{texto}

{_INSTRUCCION_TIMESTAMPS}"""
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"{context}\n\nSelecciona los {cupo} mejores momentos de este tramo. Responde SOLO con JSON válido."},
        ]
        try:
            r, costo = _llamar_pasada_a(messages, client, model, max_retries, etiqueta=f"Pasada A ventana {v.indice + 1}/{n}")
            return v, r, costo, None
        except Exception as e:
            # El costo de los intentos fallidos igual queda en usage_tracker.
            return v, None, 0.0, e

    concurrencia = max(1, min(vt.VENTANAS_CONCURRENCIA_MAX, n))
    with ThreadPoolExecutor(max_workers=concurrencia) as pool:
        resultados = list(pool.map(in_current_context(_una), list(zip(ventanas, cupos))))

    costo_total = 0.0
    fallidas: list[int] = []
    base: dict | None = None
    union: list[dict] = []
    for v, r, costo, err in resultados:
        costo_total += costo
        if r is None:
            fallidas.append(v.indice)
            print(f"⚠️ Pasada A ventana {v.indice + 1}/{n} ({_mmss(v.inicio)}–{_mmss(v.fin)}) "
                  f"falló tras los reintentos: {str(err)[:150]} — sigo con las demás")
            continue
        if base is None:
            base = r
        fuera = 0
        for puesto, m in enumerate(r.get("viral_moments") or []):
            if not isinstance(m, dict):
                continue
            iv = vt._intervalo(m)
            if iv is None:
                continue
            centro = (iv[0] + iv[1]) / 2
            if not (v.inicio - VENTANA_TOLERANCIA_SEG <= centro <= v.fin + VENTANA_TOLERANCIA_SEG):
                fuera += 1
                continue
            union.append({**m, "ventana": v.indice, "puesto_en_ventana": puesto})
        if fuera:
            print(f"   ⚠️ ventana {v.indice + 1}: {fuera} candidatos fuera de su tramo, descartados")

    if base is None or not union:
        return None, costo_total, fallidas

    antes = len(union)
    union, duplicados = vt.deduplicar(union)
    fusiones = 0
    if vt.fusion_enabled():
        union, fusiones = vt.fusionar_historias(union, ventanas, CLIP_MAX_DURATION_SEC)
    union = vt.ordenar_union(union)
    print(f"   🪟 Unión de Ventanas: {antes} candidatos → {len(duplicados)} duplicados, "
          f"{fusiones} fusiones → {len(union)}")

    result = dict(base)
    result["viral_moments"] = union
    return result, costo_total, fallidas


def select_moments(
    transcript_text: str,
    video_info: dict,
    duration: float,
    category: str,
    language: str,
    client,
    model: str,
    max_retries: int = 3,
    transcript: dict | None = None,
) -> dict:
    """
    Ejecuta la pasada A: selección de momentos con sobre-generación + ranking.

    W21: con `SELECCION_POR_VENTANAS=on` y un transcript con Líneas, parte
    el video en Ventanas (services/ventanas.py) y hace una llamada por
    Ventana en paralelo; si una Ventana falla sigue con las demás, y si
    fallan todas cae a la pasada única de siempre.

    Returns:
        result_dict con shape de AnalysisResult (momentos sin copy). Trae
        además `_pasada_a` (modo, Ventanas, costo y segundos) para medir.

    Raises:
        Exception si el LLM falla tras los retries (el caller cae al mega-prompt).
    """
    from services import ventanas as vt

    t0 = time.time()
    target = target_moment_count(duration)
    num_candidates = candidate_count(duration)

    ventanas = None
    if vt.seleccion_por_ventanas_enabled() and transcript and transcript.get("lines"):
        ventanas = vt.armar_ventanas(transcript["lines"], duration)
        if len(ventanas) < 2:
            ventanas = None

    result_dict = None
    meta: dict = {"modo": "unica", "candidatos_pedidos": num_candidates}
    costo = 0.0
    if ventanas:
        cupos = vt.repartir_cupo(ventanas, num_candidates)
        print(f"🎯 Pasada A por Ventanas con {model}: {len(ventanas)} Ventanas, "
              f"cupos {cupos} ({sum(cupos)} candidatos) → top {target}...")
        result_dict, costo, fallidas = _pasada_por_ventanas(
            ventanas, cupos, video_info, duration, category, language, client, model, max_retries,
        )
        meta.update({
            "modo": "ventanas",
            "ventanas": [[round(v.inicio), round(v.fin)] for v in ventanas],
            "cupos": cupos,
            "candidatos_pedidos": sum(cupos),
            "ventanas_fallidas": fallidas,
        })
        if result_dict is None:
            print("⚠️ Pasada A: fallaron todas las Ventanas — respaldo con la pasada única")
            meta["modo"] = "unica_respaldo"

    if result_dict is None:
        if not ventanas:
            print(f"🎯 Pasada A: seleccionando momentos con {model} "
                  f"({num_candidates} candidatos → top {target})...")
        result_dict, costo_unica = _pasada_unica(
            transcript_text, video_info, duration, category, language,
            client, model, max_retries, num_candidates,
        )
        costo += costo_unica

    result_dict = rank_and_prune_candidates(result_dict, target, transcript=transcript)

    # Garantizar content_pieces vacío (el schema lo requiere; pasada B lo llena)
    for m in result_dict["viral_moments"]:
        if isinstance(m, dict) and not isinstance(m.get("content_pieces"), dict):
            m["content_pieces"] = {}

    segundos = round(time.time() - t0, 1)
    meta.update({"costo_usd": round(costo, 6), "segundos": segundos})
    result_dict["_pasada_a"] = meta
    print(f"✅ Pasada A ({meta['modo']}): {len(result_dict['viral_moments'])} momentos seleccionados "
          f"· costo ${costo:.4f} · {segundos:.1f}s")
    return result_dict
