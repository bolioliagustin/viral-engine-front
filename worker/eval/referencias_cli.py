"""
Validación humana de Referencias (W19).

    python eval/referencias_cli.py markdown [youtube_id ...]    # regenera eval/referencias/validar/*.md
    python eval/referencias_cli.py aplicar eval/referencias/validar/<youtube_id>.md --validador agustin
    python eval/referencias_cli.py estado                        # validados / pendientes por video

`aplicar` lee las decisiones del markdown (`si` / `no` / `pendiente`), los
tiempos y campos corregidos y los bloques `### NUEVO`, y los vuelve a
escribir en el JSON. Después regenera el markdown para que quede al día.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR.parent))
sys.path.insert(0, str(EVAL_DIR))

import aislamiento  # noqa: E402
import referencias as refs  # noqa: E402


def _lineas(youtube_id: str) -> list[dict] | None:
    local = aislamiento.leer_transcript_local(youtube_id)
    return (local[0].get("lines") if local else None)


def _todos() -> list[str]:
    return sorted(p.stem for p in refs.REFERENCIAS_DIR.glob("*.json"))


def cmd_markdown(ids: list[str]) -> int:
    for yt in ids or _todos():
        doc = refs.cargar_referencias(yt)
        if not doc:
            print(f"❌ {yt}: sin Referencias")
            continue
        ruta = refs.VALIDAR_DIR / f"{yt}.md"
        ruta.parent.mkdir(parents=True, exist_ok=True)
        lineas = _lineas(yt)
        ruta.write_text(refs.generar_markdown(doc, lineas), encoding="utf-8")
        print(f"✅ {ruta}" + ("" if lineas else "  (sin transcript local: sin extractos de texto)"))
    return 0


def cmd_aplicar(md_path: str, validador: str) -> int:
    ruta = Path(md_path)
    yt = ruta.stem
    doc = refs.cargar_referencias(yt)
    if not doc:
        print(f"❌ {yt}: no existe eval/referencias/{yt}.json")
        return 1
    try:
        conteo = refs.aplicar_validacion(doc, ruta.read_text(encoding="utf-8"), validador=validador)
    except ValueError as e:
        print(f"❌ No se aplicó nada: {e}")
        return 1
    refs.guardar_referencias(doc)
    ruta.write_text(refs.generar_markdown(doc, _lineas(yt)), encoding="utf-8")
    print(f"✅ {yt}: {conteo}")
    print(f"   estado: {refs.estado_validacion(doc)}")
    return 0


def cmd_estado() -> int:
    print(f"{'youtube_id':<14} {'video':<28} {'momentos':>8} {'validados':>9} {'A valid.':>8} {'pend.':>6}")
    for yt in _todos():
        doc = refs.cargar_referencias(yt)
        e = refs.estado_validacion(doc)
        print(f"{yt:<14} {doc.get('video_id') or '':<28} {e['momentos']:>8} {e['validados']:>9} {e['validados_a']:>8} {e['pendientes']:>6}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Validación de Referencias")
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("markdown")
    m.add_argument("ids", nargs="*")
    a = sub.add_parser("aplicar")
    a.add_argument("md")
    a.add_argument("--validador", required=True)
    sub.add_parser("estado")
    args = p.parse_args()
    if args.cmd == "markdown":
        return cmd_markdown(args.ids)
    if args.cmd == "aplicar":
        return cmd_aplicar(args.md, args.validador)
    return cmd_estado()


if __name__ == "__main__":
    sys.exit(main())
