"""
Fase 4 — Scoring calibrado y estadísticas honestas.

1. Juez independiente: un modelo distinto al de análisis (MODEL_JUDGE) puntúa
   cada clip FINAL contra una rúbrica con anclas explícitas, usando el texto
   Whisper real del clip. Reemplaza los scores autoevaluados (el modelo que
   elige el momento se ponía la nota a sí mismo → inflación sistemática 7-9).

2. ROI determinístico: `roi_time_saved` se calcula con fórmula fija en lugar
   del número alucinado por el LLM.
"""
import json
import os

from openai import OpenAI

from config.model_tiers import get_model
from config.llm_chat import build_chat_kwargs, log_llm_usage


# ─── ROI determinístico ──────────────────────────────────────────────────────
# Minutos que un humano tardaría en producir lo mismo:
#   base (encontrar el momento + cortar el clip) = 8 min
#   + 0.5 min por segundo de clip (revisar/subtitular/ajustar)
#   + 15 min por pieza de copy escrita (thread / linkedin / caption)
ROI_BASE_MINUTES = 8.0
ROI_PER_CLIP_SECOND = 0.5
ROI_PER_COPY_PIECE = 15.0


def deterministic_roi(clip_duration_sec: float, copy_pieces: int) -> int:
    """ROI en minutos con fórmula fija (honesta y reproducible)."""
    duration = max(0.0, float(clip_duration_sec or 0))
    pieces = max(0, int(copy_pieces or 0))
    return int(round(ROI_BASE_MINUTES + ROI_PER_CLIP_SECOND * duration + ROI_PER_COPY_PIECE * pieces))


# ─── Juez independiente ──────────────────────────────────────────────────────

_JUDGE_RUBRIC = """Eres un JUEZ independiente de calidad de clips virales. NO creaste este clip. Sé crítico y usa TODA la escala 1-10 — la mayoría de los clips reales son 4-7.

RÚBRICA CON ANCLAS (por métrica):

HOOK (¿los primeros 3 segundos frenan el scroll?):
- 3 = arranca con relleno, contexto o mitad de una idea; nada llama la atención
- 6 = la primera frase es interesante pero necesita contexto o tarda en llegar
- 9 = la primera frase es una afirmación contraintuitiva/tensión inmediata que funciona sin contexto

RETENTION (¿mantiene la atención hasta el final?):
- 3 = divaga, hay relleno, la idea se diluye o el final queda cortado
- 6 = idea completa pero con momentos flojos en el medio
- 9 = tensión sostenida de inicio a fin, remate claro, cero relleno

SHAREABILITY (¿alguien lo compartiría o etiquetaría a un amigo?):
- 3 = genérico; no hay razón social para compartirlo
- 6 = útil o interesante, pero no urgente de compartir
- 9 = da estatus compartirlo (dato sorprendente, verdad incómoda, utilidad inmediata)

REGLAS:
- Evalúa SOLO el texto real del clip (transcript) + el overlay. No asumas contenido visual.
- Un clip que corta a mitad de frase NO puede tener retention > 5.
- Responde SOLO JSON: {"hook": n, "retention": n, "shareability": n, "reasoning": "1-2 frases explicando los scores"}"""


def judge_moment_scores(
    clip_text: str,
    *,
    hook: str = "",
    viral_overlay: str = "",
    category: str = "business",
    clip_duration_sec: float = 0.0,
    client=None,
) -> dict | None:
    """
    Puntúa el clip final con MODEL_JUDGE contra la rúbrica anclada.

    Returns:
        {"hook": int, "retention": int, "shareability": int, "reasoning": str}
        o None si el juez falla (el caller conserva los scores del análisis).
    """
    if not clip_text or not clip_text.strip():
        return None

    if client is None:
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )

    model = get_model("judge")
    user_prompt = f"""CLIP A EVALUAR:
- Categoría: {category}
- Duración: {clip_duration_sec:.0f}s
- Overlay quemado en pantalla: {viral_overlay or '(sin overlay)'}
- Hook propuesto: {hook or '(sin hook)'}

TRANSCRIPT REAL DEL CLIP (audio exacto):
{clip_text[:3000]}

Puntúa contra la rúbrica. Responde SOLO JSON."""

    try:
        response = client.chat.completions.create(
            **build_chat_kwargs(
                "judge",
                model,
                [
                    {"role": "system", "content": _JUDGE_RUBRIC},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                timeout=30,
            )
        )
        log_llm_usage("judge", model, response)
        raw = response.choices[0].message.content if response.choices else None
        if not raw or not raw.strip():
            return None

        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            from json_repair import repair_json
            data = json.loads(repair_json(raw.strip()))

        scores = {}
        for key in ("hook", "retention", "shareability"):
            val = data.get(key)
            if val is None:
                return None
            scores[key] = max(1, min(10, int(round(float(val)))))
        scores["reasoning"] = str(data.get("reasoning") or "")[:500]
        return scores
    except Exception as e:
        print(f"   ⚠️ Judge falló (conservamos scores del análisis): {str(e)[:120]}")
        return None
