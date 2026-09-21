"""Rankeo de Candidatos con Jev (TypeSafe System One), detrás de `RANKER`.

## Por qué

El Juez LLM puntúa cada dimensión con un entero de 1 a 10 y amontona casi todo
en 4–6: medido sobre 59 clips del golden set, **no puede ordenar el 13,9 % de
los pares** (empata), y por eso la nota del top-5 quedaba plana (5,65) para
cualquier umbral de entrega (docs/PLAN_CALIDAD.md §9, calibración de INT-5).
Como W2 usa esa nota para *elegir* qué clips se entregan, los empates son un
problema de producto, no de métrica.

Jev responde la misma pregunta con un score continuo (media ponderada por la
probabilidad de cada nivel) más una confianza: sobre esos mismos 59 clips
empata el 0,2 % de los pares, dispersa un 46 % más y ordena más parecido al
ranking de Opus Clip sobre el mismo video (0,61 contra 0,37 del Juez, n=10).

## Qué NO hace

No reemplaza al Juez: el score que se persiste en `content_results.score_judge`
y con el que se comparan todas las corridas del golden set sigue siendo el del
Juez LLM. Jev sólo decide **el orden** en la fase de evaluación de W2. Tampoco
toca la Pasada A ni la Pasada B: Jev no genera texto por diseño.

## Escala

Jev devuelve 0–4 por dimensión (0–12 la suma). `score_candidate` y las
constantes `PENALTY_*` / `DELIVERY_JUDGE_MIN` están en la escala 0–30 del Juez,
así que la suma se normaliza: 0–12 → 0–30. No es cosmético — sin normalizar,
una penalización de 6 puntos borraría a la mitad de los candidatos.
Las medias quedan alineadas (Jev 14,7/30 contra 15,7/30 del Juez sobre los
mismos 59 clips), así que el umbral de entrega conserva su significado.

## Fallback

Cualquier falla (sin API key, 401, 429/529 tras los reintentos, timeout, forma
inesperada) devuelve `None` y el caller usa la nota del Juez, que ya calculó.
Jev nunca puede dejar un job sin ranking.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

ENDPOINT = os.getenv("TYPESAFE_ENDPOINT", "https://api.typesafe.ai/v1/systemone")
MODEL = os.getenv("TYPESAFE_MODEL", "jev-latest")
TIMEOUT_SEC = float(os.getenv("TYPESAFE_TIMEOUT_SEC", "20"))
MAX_ATTEMPTS = int(os.getenv("TYPESAFE_MAX_ATTEMPTS", "3"))

# Escalas: Jev da 0..4 por dimensión (3 dimensiones → 0..12); el ranking de W2
# vive en la escala del Juez (3 × 1..10 → 0..30).
JEV_MAX_SUM = 12.0
JUDGE_MAX_SUM = 30.0

# Las tres dimensiones del Juez, con la rúbrica explícita que el prompt del
# Juez lleva en prosa. Cinco niveles: la resolución fina la da la probabilidad,
# no la cantidad de escalones.
QUESTIONS = {
    "gancho": {
        "type": "score",
        "instructions": (
            "¿Qué tan bien engancha este clip en los primeros segundos a alguien "
            "que lo ve en un feed vertical?"
        ),
        "criteria": [
            "No engancha: arranca a mitad de idea o es puro relleno",
            "Flojo: se entiende pero no da motivo para quedarse",
            "Correcto: abre con una afirmación clara",
            "Fuerte: abre con una afirmación concreta que genera curiosidad",
            "Excelente: la primera frase es imposible de saltear",
        ],
    },
    "retencion": {
        "type": "score",
        "instructions": (
            "¿Qué tan probable es que alguien mire el clip hasta el final sin saltearlo?"
        ),
        "criteria": [
            "Se abandona enseguida: divaga o repite",
            "Difícil de seguir: hay una idea pero con mucho relleno",
            "Se sigue: idea completa con algo de relleno",
            "Retiene: desarrollo claro y remate",
            "Retiene fuerte: cada segundo aporta y cierra con un remate",
        ],
    },
    "compartir": {
        "type": "score",
        "instructions": "¿Qué tan probable es que alguien comparta o guarde este clip?",
        "criteria": [
            "No se comparte: trivial o sin valor",
            "Poco: interesante pero olvidable",
            "Puede: aporta un dato útil",
            "Probable: dato concreto o contraintuitivo",
            "Muy probable: dato que da estatus compartirlo",
        ],
    },
}

_DIMENSIONS = ("gancho", "retencion", "compartir")


def ranker_is_jev() -> bool:
    """True si el rankeo de candidatos debe usar Jev (`RANKER=jev`)."""
    return (os.getenv("RANKER", "llm") or "llm").strip().lower() == "jev"


def _api_key() -> str | None:
    key = (os.getenv("TYPESAFE_API_KEY") or "").strip()
    return key or None


def _post(payload: dict, key: str) -> tuple[dict, int]:
    body = json.dumps(payload).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        req = urllib.request.Request(
            ENDPOINT,
            data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
                data = json.load(resp)
            return data, int((time.time() - started) * 1000)
        except urllib.error.HTTPError as e:
            # 429/529: la doc pide backoff exponencial. El resto no se reintenta.
            if e.code in (429, 529) and attempt < MAX_ATTEMPTS - 1:
                time.sleep(2 ** attempt)
                last_error = e
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(2 ** attempt)
                last_error = e
                continue
            raise
    raise last_error if last_error else RuntimeError("jev: sin intentos")


def jev_rank_scores(
    clip_text: str,
    *,
    hook: str = "",
    viral_overlay: str = "",
    clip_duration_sec: float = 0.0,
    moment_index: int | None = None,
) -> dict | None:
    """
    Nota de ranking de Jev para un Candidato, o None si no se pudo obtener.

    Returns:
        {
          "scores_0_4": {"gancho": float, "retencion": float, "compartir": float},
          "confidence": {...},          # 0..1 por dimensión
          "sum_0_12": float,
          "rank_score": float,          # normalizado a la escala 0..30 del Juez
          "confidence_avg": float,
          "latency_ms": int,
        }
    """
    if not clip_text or not clip_text.strip():
        return None
    key = _api_key()
    if not key:
        print("   ⚠️ RANKER=jev pero falta TYPESAFE_API_KEY — se usa la nota del Juez")
        return None

    state = {
        "transcripcion_del_clip": clip_text,
        "gancho_propuesto": hook or "",
        "cartel_en_pantalla": viral_overlay or "",
        "duracion_seg": round(float(clip_duration_sec or 0), 1),
    }
    try:
        data, latency_ms = _post(
            {"state": state, "model": MODEL, "questions": QUESTIONS}, key
        )
        answers = data["answers"]
        scores = {d: float(answers[d]["score"]) for d in _DIMENSIONS}
        confidence = {d: float(answers[d].get("confidence") or 0.0) for d in _DIMENSIONS}
    except urllib.error.HTTPError as e:
        print(f"   ⚠️ Jev falló (HTTP {e.code}) — se usa la nota del Juez")
        return None
    except Exception as e:  # noqa: BLE001 — cualquier falla cae al Juez
        print(f"   ⚠️ Jev falló ({type(e).__name__}: {str(e)[:80]}) — se usa la nota del Juez")
        return None

    total = sum(scores.values())
    result = {
        "scores_0_4": {k: round(v, 3) for k, v in scores.items()},
        "confidence": {k: round(v, 3) for k, v in confidence.items()},
        "sum_0_12": round(total, 3),
        "rank_score": round(total / JEV_MAX_SUM * JUDGE_MAX_SUM, 3),
        "confidence_avg": round(sum(confidence.values()) / len(_DIMENSIONS), 3),
        "latency_ms": latency_ms,
    }

    try:
        from services.usage_tracker import record_jev_usage

        record_jev_usage(
            model=data.get("model") or MODEL,
            input_tokens=int((data.get("usage") or {}).get("input_tokens") or 0),
            output_tokens=int((data.get("usage") or {}).get("output_tokens") or 0),
            moment_index=moment_index,
            latency_ms=latency_ms,
            metadata={"confidence_avg": result["confidence_avg"]},
        )
    except Exception:  # noqa: BLE001 — la contabilidad nunca rompe el pipeline
        pass

    return result
