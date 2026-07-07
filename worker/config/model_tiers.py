"""
Model tiers por tarea (Fase 1 — Plan calidad IA).

Cada tarea del pipeline usa un modelo distinto, configurable por env:

  - MODEL_ANALYSIS   → selección de momentos (pasada A).
                       Default: google/gemini-2.5-pro
  - MODEL_COPY_WRITING / MODEL_COPY → threads/posts/captions (pasada B).
                       Default: google/gemini-2.5-flash
  - MODEL_JUDGE      → scoring independiente del clip final.
                       Default: google/gemini-2.5-flash-lite
  - MODEL_CLASSIFIER → clasificador binario podcast/business.
                       Default: google/gemini-2.0-flash-001

Compat: si las envs nuevas no están seteadas, caemos a las legacy
(OPENROUTER_MODEL / OPENROUTER_COPY_MODEL / OPENROUTER_CLASSIFIER_MODEL)
y recién después al default 2026.

Temperature por tarea: output estructural (selección, juez, clasificador)
usa temperature baja; solo el copy creativo mantiene 0.6+.
"""
import os

# Defaults actualizados (2026). Se pueden pisar por env.
DEFAULT_MODELS = {
    "analysis": "google/gemini-2.5-pro",
    "copy": "google/gemini-2.5-flash",
    "judge": "google/gemini-2.5-flash-lite",
    "classifier": "google/gemini-2.0-flash-001",
}

# Orden de resolución: env nueva → env legacy → default.
_ENV_CHAIN = {
    "analysis": ("MODEL_ANALYSIS", "OPENROUTER_MODEL"),
    "copy": ("MODEL_COPY_WRITING", "MODEL_COPY", "OPENROUTER_COPY_MODEL", "OPENROUTER_MODEL"),
    "judge": ("MODEL_JUDGE",),
    "classifier": ("MODEL_CLASSIFIER", "OPENROUTER_CLASSIFIER_MODEL"),
}

# Temperature por tarea: estructural bajo, creativo medio.
TASK_TEMPERATURES = {
    "analysis": 0.3,
    "copy": 0.65,
    "judge": 0.1,
    "classifier": 0.0,
}

# Nombres legibles de idioma para la instrucción de salida del prompt.
_LANGUAGE_NAMES = {
    "es": "español",
    "en": "English",
    "pt": "português",
    "fr": "français",
    "de": "Deutsch",
    "it": "italiano",
    "ca": "català",
}


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


def get_temperature(task: str) -> float:
    """Temperature recomendada para la tarea."""
    return TASK_TEMPERATURES.get(task, 0.3)


def is_free_tier(model: str) -> bool:
    """True si el modelo es free tier de OpenRouter (rate limits + peor calidad)."""
    return ":free" in (model or "").lower()


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
