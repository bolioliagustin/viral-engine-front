"""
Borrador asistido de Referencias (W19).

    python eval/borrador_referencias.py <video_id|youtube_id> [--modelo M] [--dry-run]

Toma el transcript `whisper_full` del video (copia local del eval, caché de
Supabase en solo lectura, o lo genera una vez y lo guarda solo local) y le
pide a un modelo de **otra familia** que la Pasada A (vía OpenRouter) que
proponga momentos con Núcleo y porqué. La rúbrica es propia: si fuera el
prompt de la Pasada A, el recall saldría inflado.

La salida se fusiona en `eval/referencias/<youtube_id>.json` sin duplicar
(lo que ya está, semilla o validado, gana) con `validado_por: null`, y se
regenera el markdown de validación en `eval/referencias/validar/`. Modelo,
costo y fecha quedan en `borradores[]` del JSON.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
WORKER_DIR = EVAL_DIR.parent
sys.path.insert(0, str(WORKER_DIR))
sys.path.insert(0, str(EVAL_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(WORKER_DIR.parent / ".env")

import referencias as refs  # noqa: E402

# Anthropic: otra familia que la Pasada A (Gemini) y que el juez (GPT).
MODELO_DEFAULT = "anthropic/claude-sonnet-5"
# USD por 1M tokens, por si OpenRouter no devuelve el costo en `usage`.
PRECIOS_FALLBACK = {"anthropic/claude-sonnet-5": (2.0, 10.0)}
RUBRICA_VERSION = "r1"
MAX_TOKENS = 24000
RAZONAMIENTO_MAX_TOKENS = 8000
CRUDOS_DIR = WORKER_DIR / "downloads" / "eval_borradores"

RUBRICA = """Sos la persona que edita los clips de este creador y conoce a su audiencia. Vas a leer la transcripción COMPLETA de un episodio y marcar los momentos que vos publicarías como clip vertical corto (TikTok, Reels, Shorts), para armar una lista de referencia contra la que después se va a evaluar un sistema automático.

Qué hace que un momento sea publicable:
- Se entiende solo, sin haber visto el resto del episodio (o con una frase de contexto que entra en el clip).
- Tiene una curva: un planteo que genera expectativa y un remate que la paga (risa, sorpresa, una idea que se te queda, una emoción, una frase que la gente repetiría o discutiría).
- Tiene energía: una anécdota bien contada, una imitación, una respuesta filosa, un dato que sorprende, una explicación que ordena algo confuso.
- NO es publicable: publicidad o menciones de sponsors, saludos, presentación del programa, logística ("ahora vamos a…"), momentos que solo se entienden viendo la pantalla, chistes internos que dependen de algo dicho mucho antes.

Para cada momento marcás dos rangos, en segundos ABSOLUTOS tomados de las marcas [segundos] de cada renglón:
- NÚCLEO (nucleo_inicio, nucleo_fin): lo mínimo que un clip TIENE que contener para funcionar: desde el arranque del planteo hasta el final del remate. Si le sacás el planteo o el remate, el clip no sirve.
- TRAMO (inicio, fin): el clip ideal completo; contiene al núcleo y puede sumar unos segundos de contexto antes o la reacción después. inicio ≤ nucleo_inicio < nucleo_fin ≤ fin.

Tipo (elegí uno exacto): "anécdota", "opinión", "frase citable", "cruce con el público", "imitación", "dato", "explicación".
Calidad: "A" = lo publicarías seguro; "B" = probablemente sí. Si dudás de que sea publicable, no lo pongas.

Reglas:
- Recorré el episodio ENTERO, del primer al último minuto: lo mejor puede estar en cualquier parte, también al final. No te quedes con los primeros que encontrás.
- Proponé entre {n_min} y {n_max} momentos, sin solaparlos entre sí, ordenados por tiempo.
- Los tiempos salen de las marcas del texto; no inventes segundos.
- En "cita_inicio" y "cita_fin" copiá literalmente las primeras y las últimas 4–8 palabras del núcleo.
- En "excluir" marcá los tramos de publicidad o sponsors (inicio, fin, motivo), si los hay.

Respondé SOLO con JSON válido, con esta forma:
{{"momentos": [{{"inicio": 0, "fin": 0, "nucleo_inicio": 0, "nucleo_fin": 0, "tipo": "", "calidad": "A", "titulo": "título corto", "por_que": "una oración: qué lo hace publicable", "cita_inicio": "", "cita_fin": ""}}], "excluir": [{{"inicio": 0, "fin": 0, "motivo": ""}}]}}"""

_SINONIMOS_TIPO = {
    "anecdota": "anécdota", "historia": "anécdota", "opinion": "opinión", "chicana": "opinión",
    "frase": "frase citable", "cita": "frase citable", "chiste": "frase citable",
    "cruce": "cruce con el público", "público": "cruce con el público", "publico": "cruce con el público",
    "imitacion": "imitación", "explicacion": "explicación",
}


def formatear_transcript(lines: list[dict]) -> str:
    return "\n".join(f"[{int(float(l['start']))}] {(l.get('text') or '').strip()}" for l in lines if (l.get("text") or "").strip())


def rango_pedido(duracion_sec: float) -> tuple[int, int]:
    return (20, 30) if duracion_sec >= 40 * 60 else (12, 20)


def _tipo(valor: str) -> str | None:
    v = (valor or "").strip().lower()
    if v in refs.TIPOS:
        return v
    for clave, tipo in _SINONIMOS_TIPO.items():
        if clave in v:
            return tipo
    return None


def normalizar_propuestos(crudos: list[dict], *, duracion: float, autor: str, fecha: str) -> tuple[list[dict], list[str]]:
    """Valida y corrige lo que devolvió el modelo; devuelve (momentos, avisos)."""
    out, avisos = [], []
    for i, m in enumerate(crudos or []):
        try:
            ni, nf = float(m["nucleo_inicio"]), float(m["nucleo_fin"])
            ini = float(m.get("inicio", ni))
            fin = float(m.get("fin", nf))
        except (KeyError, TypeError, ValueError):
            avisos.append(f"#{i}: tiempos inválidos, se descarta")
            continue
        if nf - ni < 3 or nf > duracion + 5 or ni < 0:
            avisos.append(f"#{i}: núcleo {ni}–{nf} fuera de rango o < 3 s, se descarta")
            continue
        ini, fin = min(ini, ni), max(fin, nf)  # el tramo siempre contiene al núcleo
        tipo = _tipo(m.get("tipo"))
        if tipo is None:
            avisos.append(f"#{i}: tipo {m.get('tipo')!r} desconocido → 'opinión'")
            tipo = "opinión"
        calidad = (m.get("calidad") or "B").strip().upper()
        out.append({
            "id": f"D{i:02d}",
            "inicio": ini, "fin": min(fin, duracion), "nucleo_inicio": ni, "nucleo_fin": nf,
            "tipo": tipo, "calidad": calidad if calidad in refs.CALIDADES else "B",
            "titulo": (m.get("titulo") or "").strip()[:80],
            "por_que": (m.get("por_que") or "").strip() or "(sin porqué)",
            "cita_inicio": (m.get("cita_inicio") or "").strip(),
            "cita_fin": (m.get("cita_fin") or "").strip(),
            "autor": autor, "validado_por": None, "fecha": fecha,
        })
    return out, avisos


def extraer_json(texto: str) -> dict:
    """El objeto JSON de la respuesta, aunque venga con prosa o ``` alrededor."""
    from json_repair import repair_json
    ini = texto.find("{")
    fin = texto.rfind("}")
    if ini == -1:
        raise ValueError("la respuesta no trae JSON")
    data = json.loads(repair_json(texto[ini:fin + 1] if fin > ini else texto[ini:]))
    if isinstance(data, list):
        data = {"momentos": data, "excluir": []}
    return data


def _costo(modelo: str, respuesta) -> float:
    usage = getattr(respuesta, "usage", None)
    extra = getattr(usage, "model_extra", None) or {}
    if extra.get("cost") is not None:
        return round(float(extra["cost"]), 6)
    pin, pout = PRECIOS_FALLBACK.get(modelo, (3.0, 15.0))
    return round((getattr(usage, "prompt_tokens", 0) or 0) / 1e6 * pin
                 + (getattr(usage, "completion_tokens", 0) or 0) / 1e6 * pout, 6)


def pedir_borrador(lines: list[dict], video_info: dict, duracion: float, modelo: str) -> dict:
    from openai import OpenAI

    n_min, n_max = rango_pedido(duracion)
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.getenv("OPENROUTER_API_KEY"))
    user = (
        f"Video: {video_info.get('title') or '(sin título)'} — canal: {video_info.get('uploader') or '?'} — "
        f"duración: {int(duracion)} s ({duracion / 60:.0f} min)\n\nTRANSCRIPCIÓN (cada renglón empieza con su segundo absoluto):\n"
        f"{formatear_transcript(lines)}"
    )
    t0 = time.time()
    resp = client.chat.completions.create(
        model=modelo,
        messages=[
            {"role": "system", "content": RUBRICA.format(n_min=n_min, n_max=n_max)},
            {"role": "user", "content": user},
        ],
        # El razonamiento consume max_tokens (AGENTS.md): sin tope, en un video
        # de 110 min se comió los 16k y la respuesta salió cortada. Se acota y
        # se deja margen para ~30 momentos de salida visible.
        max_tokens=MAX_TOKENS,
        temperature=0.3,
        extra_body={"usage": {"include": True}, "reasoning": {"max_tokens": RAZONAMIENTO_MAX_TOKENS}},
    )
    crudo = resp.choices[0].message.content or ""
    # La respuesta cruda queda local (gitignored) para poder auditar el parseo
    CRUDOS_DIR.mkdir(parents=True, exist_ok=True)
    (CRUDOS_DIR / f"{video_info.get('id')}-{int(time.time())}.txt").write_text(crudo, encoding="utf-8")
    finish = resp.choices[0].finish_reason
    if finish != "stop":
        print(f"   ⚠️ finish_reason={finish}: la respuesta puede estar cortada")
    data = extraer_json(crudo)
    return {
        "data": data,
        "finish_reason": finish,
        "costo_usd": _costo(modelo, resp),
        "segundos": round(time.time() - t0, 1),
        "tokens": {
            "entrada": getattr(resp.usage, "prompt_tokens", None),
            "salida": getattr(resp.usage, "completion_tokens", None),
            "razonamiento": getattr(getattr(resp.usage, "completion_tokens_details", None), "reasoning_tokens", None),
        },
    }


def _resolver_video(clave: str) -> dict:
    golden = json.load(open(EVAL_DIR / "golden_set.json", encoding="utf-8"))
    for v in golden["videos"]:
        if clave in (v["id"], v.get("youtube_id")):
            return v
    return {"id": clave, "youtube_id": clave}


def main() -> int:
    p = argparse.ArgumentParser(description="Borrador asistido de Referencias")
    p.add_argument("video", help="id del golden set o youtube_id")
    p.add_argument("--modelo", default=os.getenv("MODEL_REFERENCIAS", MODELO_DEFAULT))
    p.add_argument("--forzar-familia", action="store_true", help="permitir un modelo de la misma familia que la Pasada A")
    p.add_argument("--forzar", action="store_true", help="correr aunque el markdown de validación tenga cambios sin aplicar")
    p.add_argument(
        "--solo-propuesta", action="store_true",
        help="guardar la propuesta en eval/referencias/propuestas/ sin tocar el JSON ni el markdown "
             "(para cuando hay una validación en curso); se suma con referencias_cli.py fusionar",
    )
    p.add_argument("--dry-run", action="store_true", help="no llama al modelo: muestra el tamaño del prompt")
    args = p.parse_args()

    from config.model_tiers import get_model
    import seleccion

    familia_a = get_model("analysis").split("/")[0]
    if args.modelo.split("/")[0] == familia_a and not args.forzar_familia:
        print(f"❌ {args.modelo} es de la misma familia que la Pasada A ({familia_a}): inflaría el recall")
        return 2

    video = _resolver_video(args.video)
    yt = video["youtube_id"]
    loaded = seleccion.cargar_transcript_eval(video)
    if not loaded:
        print("❌ Sin transcript")
        return 1
    transcript, video_info = loaded
    lines = transcript.get("lines") or []
    duracion = float(transcript.get("duration") or video_info.get("duration") or 0)
    print(f"📝 {video['id']} ({yt}): {len(lines)} Líneas, {duracion / 60:.1f} min, modelo {args.modelo}")
    md = refs.VALIDAR_DIR / f"{yt}.md"
    previo = refs.cargar_referencias(yt)
    if (previo and md.exists() and md.read_text(encoding="utf-8") != refs.generar_markdown(previo, lines)
            and not args.forzar and not args.solo_propuesta):
        # Hay una validación en curso: sumar momentos ahora rompería el `aplicar`
        print(f"❌ {md} tiene cambios sin aplicar (validación en curso). Aplicalos primero "
              f"(referencias_cli.py aplicar), usá --solo-propuesta o --forzar.")
        return 2
    if args.dry_run:
        print(f"   prompt ≈ {len(formatear_transcript(lines)) + len(RUBRICA)} caracteres")
        return 0

    r = pedir_borrador(lines, video_info, duracion, args.modelo)
    fecha = date.today().isoformat()
    propuestos, avisos = normalizar_propuestos(
        r["data"].get("momentos") or [], duracion=duracion, autor=f"borrador:{args.modelo}", fecha=fecha,
    )
    for a in avisos:
        print(f"   ⚠️ {a}")
    propuesta = {
        "youtube_id": yt, "video_id": video["id"], "duracion_sec": duracion,
        "transcript": {
            "source": transcript.get("source"), "model": transcript.get("model"),
            "lineas": len(lines), "idioma": transcript.get("language"),
        },
        "borrador": {
            "modelo": args.modelo, "rubrica": RUBRICA_VERSION, "fecha": fecha,
            "costo_usd": r["costo_usd"], "segundos": r["segundos"], "tokens": r["tokens"],
            "propuestos": len(propuestos),
        },
        "momentos": propuestos,
        "excluir": [
            {"inicio": float(e["inicio"]), "fin": float(e["fin"]), "motivo": (e.get("motivo") or "").strip() or None}
            for e in r["data"].get("excluir") or []
            if isinstance(e, dict) and _es_rango(e)
        ],
    }
    if args.solo_propuesta:
        ruta = refs.guardar_propuesta(propuesta)
        print(f"✅ {len(propuestos)} propuestos, guardados sin fusionar | ${r['costo_usd']:.4f} | {r['segundos']:.0f}s\n"
              f"   {ruta}\n   Fusionar cuando termine la validación: python eval/referencias_cli.py fusionar {yt}")
        return 0

    doc = refs.fusionar_propuesta(refs.cargar_referencias(yt), propuesta)
    errores = refs.validar_documento(doc)
    if errores:
        print("❌ El documento no valida:\n  " + "\n  ".join(errores))
        return 1
    ruta = refs.guardar_referencias(doc)
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(refs.generar_markdown(doc, lines), encoding="utf-8")
    conteo = doc["borradores"][-1]
    print(f"✅ {len(propuestos)} propuestos → {conteo['nuevos']} nuevos, {conteo['duplicados']} ya estaban | "
          f"${r['costo_usd']:.4f} | {r['segundos']:.0f}s\n   {ruta}\n   {md}")
    return 0


def _es_rango(e: dict) -> bool:
    try:
        return float(e["fin"]) > float(e["inicio"])
    except (KeyError, TypeError, ValueError):
        return False


if __name__ == "__main__":
    sys.exit(main())
