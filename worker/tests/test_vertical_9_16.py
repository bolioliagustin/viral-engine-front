"""
Sprint 1.1 — Dia 3: Test de to_vertical_9_16

Toma los clips generados en el Dia 2 y los convierte a 9:16 con fondo blur.
Valida dimensiones, aspect ratio, audio preservado, calidad visual.

Run desde worker/: python tests/test_vertical_9_16.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.clip_generator import to_vertical_9_16, probe_video, ClipGenerationError


WORKER_DIR = Path(__file__).parent.parent
INPUT_DIR = WORKER_DIR / "clips" / "test_sprint_1_1"
OUTPUT_DIR = WORKER_DIR / "clips" / "test_sprint_1_1_9x16"

# Usamos los clips del Dia 2
INPUT_CLIPS = ["intro.mp4", "middle.mp4", "hook_style.mp4"]


def format_row(cols, widths):
    return " | ".join(str(c).ljust(w) for c, w in zip(cols, widths))


def run_tests():
    print("=" * 80)
    print("TEST: to_vertical_9_16 — Sprint 1.1 Dia 3")
    print("=" * 80)

    if not INPUT_DIR.exists():
        print(f"❌ Directorio de clips no existe: {INPUT_DIR}")
        print("   Ejecuta primero tests/test_clip_generator.py")
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUTPUT_DIR.glob("*.mp4"):
        old.unlink()

    results = []

    for clip_name in INPUT_CLIPS:
        input_path = INPUT_DIR / clip_name
        if not input_path.exists():
            print(f"⚠️  Saltando {clip_name}: no existe")
            continue

        source = probe_video(str(input_path))
        output = OUTPUT_DIR / f"{Path(clip_name).stem}_9x16.mp4"

        print(f"\n{'─' * 80}")
        print(f"Input: {clip_name}")
        print(f"  Origen: {source.width}x{source.height} ({source.aspect_ratio}), "
              f"{source.duration_sec:.1f}s, {source.size_mb:.1f}MB")

        t0 = time.time()
        try:
            meta = to_vertical_9_16(
                clip_path=str(input_path),
                output_path=str(output),
                target_width=1080,
                target_height=1920,
                blur_intensity=25,
            )
            elapsed = time.time() - t0

            dims_ok = meta.width == 1080 and meta.height == 1920
            dur_ok = abs(meta.duration_sec - source.duration_sec) < 0.5
            audio_ok = meta.has_audio == source.has_audio
            size_ok = meta.size_mb > 0.1

            ok = dims_ok and dur_ok and audio_ok and size_ok
            marker = "✅" if ok else "⚠️"
            print(f"  {marker} OK en {elapsed:.1f}s")
            print(f"     Salida: {meta.width}x{meta.height} ({meta.aspect_ratio})")
            print(f"     Duracion: {meta.duration_sec:.2f}s")
            print(f"     Audio: {'si' if meta.has_audio else 'no'}")
            print(f"     Tamaño: {meta.size_mb:.2f} MB")
            results.append((clip_name, ok, elapsed, meta, source))
        except ClipGenerationError as e:
            print(f"  ❌ FALLO: {e}")
            results.append((clip_name, False, time.time() - t0, None, source))
        except Exception as e:
            print(f"  ❌ ERROR INESPERADO: {e}")
            import traceback
            traceback.print_exc()
            results.append((clip_name, False, time.time() - t0, None, source))

    # Resumen
    print("\n" + "=" * 80)
    print("RESUMEN")
    print("=" * 80)
    passed = sum(1 for _, ok, _, _, _ in results if ok)
    total = len(results)
    print(f"\n{passed}/{total} tests pasaron\n")

    header = ["clip", "ok", "tiempo", "in dims", "out dims", "tamaño"]
    widths = [14, 4, 8, 10, 10, 10]
    print(format_row(header, widths))
    print("-" * (sum(widths) + len(widths) * 3))
    for name, ok, elapsed, meta, source in results:
        if meta:
            row = [name, "✅" if ok else "⚠️", f"{elapsed:.1f}s",
                   f"{source.width}x{source.height}",
                   f"{meta.width}x{meta.height}",
                   f"{meta.size_mb:.1f}MB"]
        else:
            row = [name, "❌", f"{elapsed:.1f}s", f"{source.width}x{source.height}", "-", "-"]
        print(format_row(row, widths))

    print(f"\nClips 9:16 generados en: {OUTPUT_DIR}")
    print("   VALIDACION VISUAL OBLIGATORIA:")
    print("   - Video centrado y legible")
    print("   - Fondo desenfocado (no solo barras negras)")
    print("   - Audio sincronizado")
    print("   - Calidad aceptable para subir a TikTok/Reels")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(run_tests())
