"""
Model tiers por tarea (Plan calidad IA — julio 2026).

Cada tarea del pipeline usa un modelo distinto, configurable por env:

  - MODEL_ANALYSIS   → selección de momentos (pasada A).
                       Default: google/gemini-3.5-flash
  - MODEL_COPY_WRITING / MODEL_COPY → threads/posts/captions (pasada B).
                       Default: google/gemini-3.5-flash
  - MODEL_JUDGE      → scoring independiente del clip final.
                       Default: openai/gpt-5.4-nano (cross-family)
  - MODEL_CLASSIFIER → clasificador podcast/business.
                       Default: google/gemini-2.5-flash-lite
                       (reemplaza gemini-2.0-flash-001, apagado jun 2026)

Compat: env legacy OPENROUTER_MODEL / OPENROUTER_COPY_MODEL /
OPENROUTER_CLASSIFIER_MODEL siguen como fallback.

Gemini 3.x / GPT-5.x: temperature omitida (default proveedor); reasoning
effort vía OpenRouter extra_body. Ver config/llm_chat.py.
"""
import os
import re

# Defaults post-recomendación (jul 2026). Validar con golden set antes de prod.
DEFAULT_MODELS = {
    "analysis": "google/gemini-3.5-flash",
    "copy": "google/gemini-3.5-flash",
    "judge": "openai/gpt-5.4-nano",
    "classifier": "google/gemini-2.5-flash-lite",
}

_ENV_CHAIN = {
    "analysis": ("MODEL_ANALYSIS", "OPENROUTER_MODEL"),
    "copy": ("MODEL_COPY_WRITING", "MODEL_COPY", "OPENROUTER_COPY_MODEL", "OPENROUTER_MODEL"),
    "judge": ("MODEL_JUDGE",),
    "classifier": ("MODEL_CLASSIFIER", "OPENROUTER_CLASSIFIER_MODEL"),
}

# Temperature para familia Gemini 2.x / legacy. Gemini 3.x: omitir (None).
TASK_TEMPERATURES = {
    "analysis": 0.3,
    "copy": 0.65,
    "judge": 0.1,
    "classifier": 0.0,
}

# max_tokens por tarea (analysis elevado para thinking tokens en Gemini 3.x).
TASK_MAX_TOKENS = {
    "classifier": 5,
    "analysis": 16000,
    "copy": 4000,
    "judge": 400,
}

# reasoning.effort en OpenRouter (solo modelos que razonan por defecto).
REASONING_EFFORT_BY_TASK = {
    "classifier": "minimal",
    "analysis": "low",
    "copy": "minimal",
    "judge": "none",
}

_LANGUAGE_NAMES = {
    "es": "español",
    "en": "English",
    "pt": "português",
    "fr": "français",
    "de": "Deutsch",
    "it": "italiano",
    "ca": "català",
}

_GEMINI3_RE = re.compile(r"gemini[- ]?3[\.\-]", re.I)
_GPT5_RE = re.compile(r"gpt[- ]?5", re.I)
_REASONING_CAPABLE_RE = re.compile(
    r"gemini[- ]?3[\.\-]|gpt[- ]?5|o[134](-mini)?",
    re.I,
)


def get_model(task: str) -> str:
    """Resuelve el modelo para una tarea ('analysis'|'copy'|'judge'|'classifier')."""
    chain = _ENV_CHAIN.get(task)
    if chain is None:
        raise ValueError(f"Tarea desconocida: {task!r}")
    for env_key in chain:
        value = os.getenv(env_key)
        if value and value.strip():
            return value.strip()
    return DEFAULT_MODELS[task]


def resolved_models() -> dict[str, str]:
    """Modelo efectivo por tarea (útil para logs de arranque)."""
    return {task: get_model(task) for task in DEFAULT_MODELS}


def is_gemini3_family(model: str) -> bool:
    """True si el modelo es de la familia Gemini 3.x (Google recomienda no fijar temperature)."""
    return bool(_GEMINI3_RE.search(model or ""))


def model_supports_reasoning_config(model: str) -> bool:
    """True si debemos enviar reasoning.effort vía OpenRouter."""
    return bool(_REASONING_CAPABLE_RE.search(model or ""))


def get_temperature(task: str, model: str | None = None) -> float | None:
    """
    Temperature para la llamada. None = omitir (Gemini 3.x usa default del proveedor).
    """
    resolved = model or get_model(task)
    if is_gemini3_family(resolved):
        return None
    return TASK_TEMPERATURES.get(task, 0.3)


def get_max_tokens(task: str, model: str | None = None) -> int:
    """Límite de tokens de salida (+ thinking cuando aplica)."""
    base = TASK_MAX_TOKENS.get(task, 4000)
    # Pasada A legacy en moment_selector usaba 8000; unificado a 16000.
    return base


def get_reasoning_effort(task: str, model: str | None = None) -> str | None:
    """
    effort para extra_body.reasoning en OpenRouter, o None si no aplica.

    Override por env: MODEL_{TASK}_REASONING=low|minimal|none|...
    """
    resolved = model or get_model(task)
    if not model_supports_reasoning_config(resolved):
        return None

    env_key = f"MODEL_{task.upper()}_REASONING"
    override = os.getenv(env_key)
    if override and override.strip():
        return override.strip().lower()

    return REASONING_EFFORT_BY_TASK.get(task)


def is_free_tier(model: str) -> bool:
    """True si el modelo es free tier de OpenRouter (rate limits + peor calidad)."""
    return ":free" in (model or "").lower()


def is_deprecated_model(model: str) -> bool:
    """Warn al arranque si el modelo apunta a familia 2.0 (apagada jun 2026)."""
    m = (model or "").lower()
    return "gemini-2.0-flash" in m or "gemini-2.0-flash-lite" in m


def language_name(lang_code: str | None) -> str:
    """Nombre legible del idioma para la instrucción de salida ('es' → 'español')."""
    if not lang_code:
        return "español"
    code = lang_code.split("-")[0].lower().strip()
    return _LANGUAGE_NAMES.get(code, code)


def output_language_instruction(lang_code: str | None) -> str:
    """
    Instrucción de idioma para inyectar en prompts: fuerza que TODO el output
    (copy, hooks, justificaciones) salga en el idioma del transcript.
    """
    name = language_name(lang_code)
    return (
        f"🌐 IDIOMA DE SALIDA OBLIGATORIO: {name}. "
        f"TODO el contenido generado (hooks, tweets, posts, overlays, "
        f"justificaciones) debe estar en {name}, el idioma del video. "
        f"NO traduzcas ni cambies de idioma."
    )
