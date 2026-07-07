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
import json
import os
import time

from config.model_tiers import get_temperature, output_language_instruction


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

TIMING: start = inicio de la pregunta/premisa - 5s; end = fin de la respuesta/reacción + 4s. Ideal 20-40s."""
    else:
        focus = """PRIORIZA (contenido de un orador / educativo):
1. Contrarian truths: ideas que rompen creencias comunes
2. High utility: valor accionable inmediato
3. Deep vulnerability: admisión de errores humanos
4. Curiosity gap: declaraciones que abren loops mentales

TIMING: start = inicio del setup de la idea; end = fin del remate/conclusión. Ideal 20-55s."""

    return f"""Eres un editor senior de clips virales. Tu ÚNICA tarea en esta pasada es SELECCIONAR los mejores momentos del video. NO generes copy, threads ni posts — eso ocurre en otra etapa.

{lang_instruction}

MISIÓN:
Identifica los {num_candidates} MEJORES momentos candidatos del video. Sé exigente: cada momento debe funcionar como clip standalone sin contexto previo.

{focus}

REGLAS DE TIMING (CRÍTICAS):
- Usa EXACTAMENTE los timestamps de la transcripción (no los inventes).
- Cada momento: 15-60 segundos. NUNCA menos de 10s.
- El momento debe empezar donde empieza la IDEA (setup) y terminar donde termina (remate). No cortes a mitad de frase.
- Momentos NO solapados (máximo 20% de overlap entre candidatos).

VERIFICACIÓN ANTI-ALUCINACIÓN (OBLIGATORIA por momento):
- first_phrase_in_audio: las primeras 5-8 palabras EXACTAS que se dicen en el clip (copiadas de la transcripción).
- last_phrase_in_audio: las últimas 5-8 palabras EXACTAS del clip.
- Si no puedes citar las frases exactas, NO incluyas ese momento.

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


def rank_and_prune_candidates(result_dict: dict, target: int) -> dict:
    """
    Rankea candidatos por score preliminar (suma hook+retention+shareability)
    y conserva los top `target`. Mantiene orden cronológico en el output final
    (los momentos se muestran al usuario en orden de aparición).
    """
    moments = result_dict.get("viral_moments") or []
    if len(moments) <= target:
        return result_dict

    def _score(m: dict) -> float:
        s = m.get("scores") or {}
        if isinstance(s, list) and s and isinstance(s[0], dict):
            s = s[0]
        if not isinstance(s, dict):
            return 0.0
        try:
            return (
                float(s.get("hook", 0))
                + float(s.get("retention", 0))
                + float(s.get("shareability", 0))
            )
        except (TypeError, ValueError):
            return 0.0

    ranked = sorted(moments, key=_score, reverse=True)[:target]
    dropped = len(moments) - len(ranked)
    # Orden cronológico para presentación
    ranked.sort(key=lambda m: float(m.get("start_time") or 0))
    print(f"   🏆 Ranking candidatos: {len(moments)} generados → top {target} ({dropped} descartados)")
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
                model=model,
                messages=messages,
                temperature=get_temperature("analysis"),
                max_tokens=8000,
                response_format={"type": "json_object"},
            )
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

    result_dict = rank_and_prune_candidates(result_dict, target)

    # Garantizar content_pieces vacío (el schema lo requiere; pasada B lo llena)
    for m in result_dict["viral_moments"]:
        if isinstance(m, dict) and not isinstance(m.get("content_pieces"), dict):
            m["content_pieces"] = {}

    print(f"✅ Pasada A: {len(result_dict['viral_moments'])} momentos seleccionados")
    return result_dict
