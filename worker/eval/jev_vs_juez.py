"""Jev (TypeSafe System One) contra nuestro juez LLM, sobre clips ya medidos.

Reutiliza la reconstrucción de texto de `comparar_jueces.py` (E3): toma los
clips de una o más corridas del golden set, arma la transcripción real del clip
desde el Transcript completo cacheado, y le hace la misma pregunta a Jev que a
nuestro Juez — sin volver a correr el pipeline.

Lo que decide si Jev sirve NO es que su promedio sea más alto (está en otra
escala): es si **discrimina** mejor. Tres cosas se miden:

  1. Dispersión: ¿el histograma se abre o sigue apelotonado en el medio?
  2. Empates: nuestro juez da enteros 1-10 y empata mucho; Jev da un score
     continuo. ¿Cuántos pares de clips quedan empatados con cada uno?
  3. Acuerdo: correlación de rangos contra el juez actual. Si es ~1, Jev es lo
     mismo más barato; si es baja, hay que mirar los desacuerdos a ojo.

Uso (desde worker/):
    python eval/jev_vs_juez.py eval/runs/2026-09-20-w8-e1.json \\
        eval/runs/2026-09-20-integracion-fase0.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from eval.comparar_jueces import (  # noqa: E402
    _load_clips,
    _load_lines_by_video,
    _resolve_clip_texts,
)

ENDPOINT = "https://api.typesafe.ai/v1/systemone"

# Mismas tres dimensiones que nuestro Juez, con la rúbrica explícita que el
# prompt del juez lleva implícita. 5 niveles (0-4) en vez de 1-10: Jev devuelve
# un continuo entre niveles, así que la resolución la da la probabilidad, no la
# cantidad de escalones.
PREGUNTAS = {
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
        "instructions": "¿Qué tan probable es que alguien mire el clip hasta el final sin saltearlo?",
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


def _preguntar_jev(key: str, texto: str, clip: dict, reintentos: int = 3) -> tuple[dict, int]:
    estado = {
        "transcripcion_del_clip": texto,
        "gancho_propuesto": clip.get("hook") or "",
        "cartel_en_pantalla": clip.get("viral_overlay") or "",
        "duracion_seg": clip.get("duration_final_sec") or clip.get("duration_chosen_sec") or 0,
    }
    cuerpo = json.dumps({"state": estado, "model": "jev-latest", "questions": PREGUNTAS}).encode()
    for intento in range(reintentos):
        req = urllib.request.Request(
            ENDPOINT,
            data=cuerpo,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        t0 = time.time()
        try:
            resp = json.load(urllib.request.urlopen(req, timeout=60))
            return resp, round((time.time() - t0) * 1000)
        except urllib.error.HTTPError as e:
            if e.code in (429, 529) and intento < reintentos - 1:
                time.sleep(2 ** intento)
                continue
            raise


def _empates(valores: list[float]) -> float:
    """Fracción de pares de clips que quedan empatados (no se pueden ordenar)."""
    pares = empatados = 0
    for i in range(len(valores)):
        for j in range(i + 1, len(valores)):
            pares += 1
            if abs(valores[i] - valores[j]) < 1e-9:
                empatados += 1
    return round(empatados / pares, 4) if pares else 0.0


def _spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None

    def rangos(v: list[float]) -> list[float]:
        orden = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, i in enumerate(orden):
            r[i] = float(pos)
        return r

    ra, rb = rangos(a), rangos(b)
    ma, mb = statistics.mean(ra), statistics.mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return round(num / den, 3) if den else None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("runs", nargs="+", help="JSON(s) de corridas del golden set")
    p.add_argument("--limite", type=int, default=0, help="Máximo de clips (0 = todos)")
    p.add_argument("--salida", default="", help="Guardar el detalle en este JSON")
    args = p.parse_args(argv)

    key = os.getenv("TYPESAFE_API_KEY")
    if not key:
        print("falta TYPESAFE_API_KEY en el .env", file=sys.stderr)
        return 2

    clips: list[dict] = []
    for ruta in args.runs:
        run = json.loads(Path(ruta).read_text(encoding="utf-8"))
        clips.extend(_load_clips(run, Path(ruta).stem))
    if args.limite:
        clips = clips[: args.limite]

    lineas = _load_lines_by_video({c.get("youtube_id") for c in clips if c.get("youtube_id")})
    textos = _resolve_clip_texts(clips, allow_full=True)

    filas = []
    tokens = latencias = 0
    for clip, (texto, fuente) in zip(clips, textos):
        if not texto:
            continue
        try:
            resp, ms = _preguntar_jev(key, texto, clip)
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {clip.get('video_id')} m{clip.get('moment_index')}: {type(e).__name__} {str(e)[:120]}")
            continue
        a = resp["answers"]
        jev = {k: a[k]["score"] for k in ("gancho", "retencion", "compartir")}
        conf = {k: a[k]["confidence"] for k in ("gancho", "retencion", "compartir")}
        nuestro = clip.get("score_judge") or {}
        filas.append(
            {
                "video": clip.get("video_id"),
                "momento": clip.get("moment_index"),
                "fuente_texto": fuente,
                "jev": jev,
                "jev_suma": round(sum(jev.values()), 3),
                "jev_confianza_media": round(sum(conf.values()) / 3, 3),
                "nuestro": {k: nuestro.get(k) for k in ("hook", "retention", "shareability")},
                "nuestro_suma": sum(v for v in nuestro.values() if isinstance(v, (int, float))),
                "latencia_ms": ms,
            }
        )
        tokens += resp.get("usage", {}).get("input_tokens", 0)
        latencias += ms
        print(f"  ✓ {clip.get('video_id')} m{clip.get('moment_index')}: jev {round(sum(jev.values()),2)}/12  nuestro {filas[-1]['nuestro_suma']}/30  ({ms} ms)")

    if not filas:
        print("sin clips evaluados", file=sys.stderr)
        return 1

    jev_sumas = [f["jev_suma"] for f in filas]
    nuestras = [f["nuestro_suma"] for f in filas if isinstance(f["nuestro_suma"], (int, float))]
    # normalizar a 0-1 para comparar dispersión entre escalas distintas
    jev_norm = [s / 12 for s in jev_sumas]
    nuestro_norm = [s / 30 for s in nuestras]

    print("\n" + "=" * 72)
    print(f"CLIPS EVALUADOS: {len(filas)}")
    print(f"{'':28}{'Jev (0-12)':>14}{'Nuestro (0-30)':>18}")
    print(f"{'media':28}{statistics.mean(jev_sumas):>14.2f}{statistics.mean(nuestras):>18.2f}")
    print(f"{'desvío':28}{statistics.pstdev(jev_sumas):>14.2f}{statistics.pstdev(nuestras):>18.2f}")
    print(f"{'desvío normalizado (0-1)':28}{statistics.pstdev(jev_norm):>14.3f}{statistics.pstdev(nuestro_norm):>18.3f}")
    print(f"{'rango (max-min)':28}{max(jev_sumas)-min(jev_sumas):>14.2f}{max(nuestras)-min(nuestras):>18.2f}")
    print(f"{'pares empatados':28}{_empates(jev_sumas):>14.1%}{_empates([float(x) for x in nuestras]):>18.1%}")
    print(f"\ncorrelación de rangos Jev vs nuestro juez: {_spearman(jev_sumas, [float(x) for x in nuestras])}")
    print(f"confianza media de Jev: {statistics.mean(f['jev_confianza_media'] for f in filas):.2f}")
    print(f"latencia media: {latencias/len(filas):.0f} ms | tokens de entrada: {tokens} "
          f"(≈ US${tokens*0.042/1e6:.5f})")

    orden_jev = sorted(filas, key=lambda f: -f["jev_suma"])
    orden_nuestro = sorted(filas, key=lambda f: -(f["nuestro_suma"] or 0))
    print("\nTOP 5 según Jev      :", [f"{f['video'][:12]} m{f['momento']}" for f in orden_jev[:5]])
    print("TOP 5 según el nuestro:", [f"{f['video'][:12]} m{f['momento']}" for f in orden_nuestro[:5]])
    coincidentes = len({(f["video"], f["momento"]) for f in orden_jev[:5]} &
                       {(f["video"], f["momento"]) for f in orden_nuestro[:5]})
    print(f"coinciden {coincidentes}/5 en el top")

    if args.salida:
        Path(args.salida).write_text(json.dumps(filas, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\ndetalle en {args.salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
