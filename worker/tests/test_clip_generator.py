"""
Sprint 1.1 — Dia 2: Test de cut_clip

Toma un video ya descargado y corta 3 clips de prueba con timestamps distintos.
Valida duracion, tamaño, codec, reproducibilidad.

Run desde worker/: python tests/test_clip_generator.py
"""
import sys
import os
import time
from pathlib import Path

# Agregar worker/ al path
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.clip_generator import cut_clip, probe_video, ClipGenerationError


WORKER_DIR = Path(__file__).parent.parent
TEST_VIDEO = WORKER_DIR / "downloads" / "9bZkp7q19f0_video.mp4"  # Gangnam Style ~4min
OUTPUT_DIR = WORKER_DIR / "clips" / "test_sprint_1_1"


# Casos de prueba: (nombre, start, end, descripcion)
TEST_CASES = [
    ("intro", 0, 15, "Primeros 15s del video"),
    ("middle", 60, 90, "30s del medio (s60-s90)"),
    ("hook_style", 120, 135, "15s estilo hook para TikTok"),
    # Edge cases
    ("short_10s", 30, 40, "Clip de exactamente 10s"),
    ("long_60s", 100, 160, "Clip largo de 60s"),
]


def format_row(cols, widths):
    return " | ".join(c.ljust(w) for c, w in zip(cols, widths))


def run_tests():
    print("=" * 80)
    print("TEST: cut_clip — Sprint 1.1 Dia 2")
    print("=" * 80)

    # Validar que el video de entrada existe
    if not TEST_VIDEO.exists():
        print(f"❌ Video de prueba no existe: {TEST_VIDEO}")
        print("   Ejecuta primero la descarga del video de test.")
        return 1

    # Metadata del video fuente
    source = probe_video(str(TEST_VIDEO))
    print(f"\nVideo fuente:")
    print(f"  Path: {source.path}")
    print(f"  Duracion: {source.duration_sec:.1f}s")
    print(f"  Resolucion: {source.width}x{source.height}")
    print(f"  Tamaño: {source.size_mb:.1f} MB")
    print(f"  Audio: {'si' if source.has_audio else 'no'}")

    # Limpiar output dir
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUTPUT_DIR.glob("*.mp4"):
        old.unlink()

    # Correr cada caso
    results = []
    for name, start, end, desc in TEST_CASES:
        expected_dur = end - start
        print(f"\n{'─' * 80}")
        print(f"Test: {name} — {desc}")
        print(f"  start={start}s, end={end}s, duracion esperada={expected_dur}s")

        output = OUTPUT_DIR / f"{name}.mp4"
        t0 = time.time()
        try:
            meta = cut_clip(
                video_path=str(TEST_VIDEO),
                start_sec=start,
                end_sec=end,
                output_path=str(output),
            )
            elapsed = time.time() - t0
            dur_diff = abs(meta.duration_sec - expected_dur)
            dur_ok = dur_diff < 1.0
            size_ok = meta.size_mb > 0.1  # al menos 100KB
            audio_ok = meta.has_audio

            ok = dur_ok and size_ok and audio_ok
            marker = "✅" if ok else "⚠️"
            print(f"  {marker} OK en {elapsed:.1f}s")
            print(f"     duracion: {meta.duration_sec:.2f}s (diff {dur_diff:.2f}s)")
            print(f"     tamaño: {meta.size_mb:.2f} MB")
            print(f"     audio: {'si' if meta.has_audio else 'no'}")
            results.append((name, ok, elapsed, meta))
        except ClipGenerationError as e:
            print(f"  ❌ FALLO: {e}")
            results.append((name, False, time.time() - t0, None))
        except Exception as e:
            print(f"  ❌ ERROR INESPERADO: {e}")
            results.append((name, False, time.time() - t0, None))

    # Resumen
    print("\n" + "=" * 80)
    print("RESUMEN")
    print("=" * 80)
    passed = sum(1 for _, ok, _, _ in results if ok)
    total = len(results)
    print(f"\n{passed}/{total} tests pasaron\n")

    header = ["test", "ok", "tiempo", "duracion", "tamaño"]
    widths = [14, 4, 8, 12, 10]
    print(format_row(header, widths))
    print("-" * sum(widths) + "-" * (len(widths) * 3))

    for name, ok, elapsed, meta in results:
        if meta:
            row = [name, "✅" if ok else "⚠️", f"{elapsed:.1f}s", f"{meta.duration_sec:.1f}s", f"{meta.size_mb:.1f}MB"]
        else:
            row = [name, "❌", f"{elapsed:.1f}s", "-", "-"]
        print(format_row(row, widths))

    # Probar validaciones (deberia fallar)
    print("\n" + "=" * 80)
    print("VALIDACIONES (deben fallar con error claro)")
    print("=" * 80)

    error_cases = [
        ("duracion_negativa", 100, 50, "end menor a start"),
        ("duracion_cero", 50, 50, "start == end"),
        ("muy_corto", 10, 12, "menor a 3s"),
        ("muy_largo", 0, 200, "mayor a 180s"),
        ("fuera_de_rango", 999, 1010, "end excede duracion del video"),
    ]
    for name, start, end, desc in error_cases:
        try:
            cut_clip(str(TEST_VIDEO), start, end, str(OUTPUT_DIR / f"invalid_{name}.mp4"))
            print(f"  ⚠️  {name}: deberia haber fallado pero no lo hizo")
        except ClipGenerationError as e:
            print(f"  ✅ {name} ({desc}): {str(e)[:80]}")

    print(f"\nClips generados en: {OUTPUT_DIR}")
    print("   Podes abrirlos con cualquier reproductor para validar visualmente.")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(run_tests())
