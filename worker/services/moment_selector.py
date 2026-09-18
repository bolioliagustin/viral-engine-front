"""
Pasada A — Selección de momentos virales (Fase 2, Plan calidad IA).

Prompt enfocado SOLO en encontrar momentos: timing, hook conceptual, trigger
emocional, verificación de frases y scores preliminares. SIN copy (el copy
completo se genera en la pasada B post-Whisper con el texto real del clip).

Sobre-generación: pedimos hasta `min(12, minutos_de_video)` candidatos con
score preliminar, rankeamos y nos quedamos con los top N (N según duración,
igual que el pipeline legacy). Esto reemplaza la densidad fija "video >5min
= 5 momentos" por selección competitiva entre candidatos.
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


def candidate_count(duration_sec: float, target: int) -> int:
    """
    Candidatos a sobre-generar: min(12, minutos de video), nunca menos que
    el target final (para videos cortos pedimos al menos target+1 y rankear).
    """
    minutes = max(1, int(duration_sec // 60))
    n = min(12, minutes)
    return max(n, min(target + 1, 12))


# ─── W2: el juez elige ───────────────────────────────────────────────────────
# docs/PLAN_CALIDAD.md §4 W2 (causas C4/C5): la Pasada A sobre-genera pero el
# auto-score del propio LLM no discrimina (8-9 a casi todo); el juez corría
# después de renderizar y no decidía nada. Ahora: cuántos candidatos extra
# sobre el target final pasan por descarga+Whisper+ancla(W1)+juez antes de
# descartarse (evaluación barata, sin Pasada B ni render).
EVAL_POOL_EXTRA = 3

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


def _judge_sum(c: "CandidateEval") -> float:
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
    W2 — el juez elige: rankea por `score_candidate` (el juez sobre el clip
    real, no el auto-score de la Pasada A) y entrega los `target` mejores.

    Diversidad: un candidato se descarta si solapa > MAX_OVERLAP_RATIO en
    tiempo con uno ya elegido, o si su hook es casi el mismo (Jaccard de
    palabras > MAX_HOOK_SIMILARITY). Si la diversidad deja menos de `target`
    elegidos, se completa con los siguientes mejores igual — mejor un
    candidato repetido que entregar menos clips de los que pidió el usuario.
    Con menos candidatos que `target`, no se rompe: devuelve los que haya.

    Returns:
        (elegidos, en orden cronológico), (descartados, con `discard_reason`)
    """
    ranked = sorted(candidates, key=score_candidate, reverse=True)
    selected: list[CandidateEval] = []
    deferred: list[CandidateEval] = []

    for cand in ranked:
        conflict = any(
            _overlap_ratio(cand, s) > MAX_OVERLAP_RATIO
            or _hook_similarity(cand.hook, s.hook) > MAX_HOOK_SIMILARITY
            for s in selected
        )
        if len(selected) < target and not conflict:
            selected.append(cand)
        else:
            cand.discard_reason = (
                "diversidad: solapa o repite el hook de un candidato ya elegido"
                if conflict else
                "ranking: quedó fuera del top por nota del juez"
            )
            deferred.append(cand)

    if len(selected) < target:
        selected_idx = {c.index for c in selected}
        for cand in deferred:
            if len(selected) >= target:
                break
            if cand.index in selected_idx:
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
    Pre-filtro barato por auto-score de la Pasada A (suma hook+retention+
    shareability): cuando se generaron muchos más candidatos de los que se
    van a evaluar de verdad, descarta los peores por auto-score para no
    gastar descarga+Whisper+juez en todos. Conserva `target + EVAL_POOL_EXTRA`
    candidatos (W2) — NO `target`: la selección final la hace el juez sobre
    el clip real en `select_finalists`, después de W1 (main.py), porque el
    auto-score del propio LLM no discrimina (causa C4, PLAN_CALIDAD.md §1.3).
    Mantiene orden cronológico en el output (se procesan en orden de aparición).
    """
    moments = result_dict.get("viral_moments") or []
    pool_size = target + EVAL_POOL_EXTRA

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

    if len(moments) <= pool_size:
        return result_dict

    ranked = sorted(moments, key=_score, reverse=True)[:pool_size]
    dropped = len(moments) - len(ranked)
    # Orden cronológico para presentación
    ranked.sort(key=lambda m: float(m.get("start_time") or 0))
    print(
        f"   🏊 Pool de evaluación: {len(moments)} generados → {len(ranked)} "
        f"pasan a Whisper+juez (top {pool_size} por auto-score, {dropped} "
        f"descartados antes de gastar en descarga; el juez decide el target "
        f"final de {target} en main.py)"
    )
    result_dict["viral_moments"] = ranked
    return result_dict


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

    Returns:
        result_dict con shape de AnalysisResult (momentos sin copy).

    Raises:
        Exception si el LLM falla tras los retries (el caller cae al mega-prompt).
    """
    target = target_moment_count(duration)
    num_candidates = candidate_count(duration, target)

    prompt = get_selection_prompt(
        duration=int(duration),
        num_candidates=num_candidates,
        category=category,
        language=language,
    )

    context = f"""VIDEO INFO:
- Título original: {video_info.get('title', 'Desconocido')}
- Duración: {int(duration)} segundos
- Canal: {video_info.get('uploader', 'Desconocido')}
- Idioma: {language or 'es'}

📜 TRANSCRIPCIÓN OFICIAL CON TIMESTAMPS:
{transcript_text}

🎯 INSTRUCCIÓN CRÍTICA:
- Los timestamps son EXACTOS — COPIA los valores, no los adivines.
- Cita first/last_phrase_in_audio LITERALMENTE desde la transcripción."""

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"{context}\n\nSelecciona los {num_candidates} mejores momentos. Responde SOLO con JSON válido."},
    ]

    print(f"🎯 Pasada A: seleccionando momentos con {model} "
          f"({num_candidates} candidatos → top {target})...")

    response_text = None
    last_error = None
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
                print(f"⚠️ Pasada A intento {attempt + 1} falló: {str(e)[:120]} — retry en {wait}s")
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
        print("   ✅ JSON de pasada A reparado")

    if isinstance(result_dict, list):
        if len(result_dict) == 1 and isinstance(result_dict[0], dict):
            result_dict = result_dict[0]
        else:
            raise ValueError(f"Pasada A devolvió array de {len(result_dict)} elementos")

    moments = result_dict.get("viral_moments")
    if not isinstance(moments, list) or not moments:
        raise ValueError("Pasada A no devolvió viral_moments")

    result_dict = rank_and_prune_candidates(result_dict, target, transcript=transcript)

    # Garantizar content_pieces vacío (el schema lo requiere; pasada B lo llena)
    for m in result_dict["viral_moments"]:
        if isinstance(m, dict) and not isinstance(m.get("content_pieces"), dict):
            m["content_pieces"] = {}

    print(f"✅ Pasada A: {len(result_dict['viral_moments'])} momentos seleccionados")
    return result_dict
