"""
Sprint 1.1 — Dia 6: Test end-to-end del pipeline completo.

Simula el flujo real del producto:
  1. Video largo descargado de YouTube
  2. Un momento viral identificado (start/end timestamps)
  3. Segments del transcript (estilo Whisper/Supadata)
  4. Un hook generado por el LLM
  5. → generate_clip() orquesta todo y devuelve MP4 listo para subir

Run desde worker/: python tests/test_generate_clip.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.clip_generator import generate_clip, probe_video, ClipGenerationError


WORKER_DIR = Path(__file__).parent.parent
SOURCE_VIDEO = WORKER_DIR / "downloads" / "9bZkp7q19f0_video.mp4"
OUTPUT_DIR = WORKER_DIR / "clips" / "test_sprint_1_1_final"


# Segments en tiempo del VIDEO ORIGINAL (simula lo que llegaria de Supadata/Whisper)
# Coinciden con el rango 60-90s del video original
SEGMENTS = [
    {"start": 60.0, "end": 63.5, "text": "Eso es lo que nadie te cuenta en internet"},
    {"start": 63.5, "end": 67.0, "text": "Porque la mayoria tiene miedo de decirlo en voz alta"},
    {"start": 67.0, "end": 71.0, "text": "Pero es exactamente lo que separa a los que logran resultados"},
    {"start": 71.0, "end": 74.5, "text": "La diferencia esta en la constancia, no en el talento"},
    {"start": 74.5, "end": 78.0, "text": "Y te lo digo porque yo pase por eso mismo"},
    {"start": 78.0, "end": 82.0, "text": "Cuando empece no tenia idea de lo que estaba haciendo"},
    {"start": 82.0, "end": 86.0, "text": "Solo sabia que no queria rendirme hasta ver resultados"},
    {"start": 86.0, "end": 90.0, "text": "Y mira donde estoy ahora, nada es casualidad"},
]


# Casos de prueba: (nombre, start, end, overlay_text, subtitle_style, overlay_style)
TEST_CASES = [
    ("full_pipeline", 60, 90, "NADIE TE CUENTA ESTO", "tiktok_viral", "tiktok_viral"),
    ("no_overlay", 100, 130, None, "clean", None),
    ("no_subs_only_overlay", 150, 170, "DATO CURIOSO", None, "question"),
    ("minimal", 200, 220, None, None, None),
]


def run_tests():
    print("=" * 80)
    print("TEST: generate_clip (pipeline completo) — Sprint 1.1 Dia 6")
    print("=" * 80)

    if not SOURCE_VIDEO.exists():
        print(f"❌ Video fuente no existe: {SOURCE_VIDEO}")
        return 1

    src = probe_video(str(SOURCE_VIDEO))
    print(f"\nVideo fuente: {src.width}x{src.height}, {src.duration_sec:.1f}s, {src.size_mb:.1f}MB\n")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUTPUT_DIR.glob("*.mp4"):
        old.unlink()
    for old in OUTPUT_DIR.glob("*.srt"):
        old.unlink()
    for old in OUTPUT_DIR.glob("*.ass"):
        old.unlink()

    results = []

    for name, start, end, overlay_text, sub_style, over_style in TEST_CASES:
        print(f"\n{'─' * 80}")
        print(f"Test: {name}")
        print(f"  range: {start}-{end}s ({end-start}s)")
        print(f"  subs: {sub_style or 'NO'}, overlay: {overlay_text or 'NO'}")

        output = OUTPUT_DIR / f"{name}.mp4"
        kwargs = {
            "video_path": str(SOURCE_VIDEO),
            "start_sec": start,
            "end_sec": end,
            "output_path": str(output),
        }
        if sub_style:
            kwargs["segments"] = SEGMENTS
            kwargs["subtitle_style"] = sub_style
        if overlay_text:
            kwargs["overlay_text"] = overlay_text
            kwargs["overlay_style"] = over_style or "tiktok_viral"

        try:
            result = generate_clip(**kwargs)
            meta = result.final
            dims_ok = meta.width == 1080 and meta.height == 1920
            dur_ok = abs(meta.duration_sec - (end - start)) < 1.0
            ok = dims_ok and dur_ok and meta.has_audio

            marker = "✅" if ok else "⚠️"
            print(f"  {marker} OK en {result.total_time_sec}s | {meta.size_mb:.1f}MB")
            print(f"     dims: {meta.width}x{meta.height} ({meta.aspect_ratio})")
            print(f"     pasos: {result.steps_time}")
            results.append((name, ok, result))
        except ClipGenerationError as e:
            print(f"  ❌ FALLO: {e}")
            results.append((name, False, None))
        except Exception as e:
            print(f"  ❌ ERROR: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False, None))

    # Resumen
    print("\n" + "=" * 80)
    print("RESUMEN FINAL SPRINT 1.1 — PIPELINE COMPLETO")
    print("=" * 80)
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n{passed}/{total} tests pasaron\n")

    print(f"{'test':<25} {'ok':<5} {'total':<8} {'cut':<6} {'9:16':<8} {'subs':<7} {'overlay':<8} {'tamaño':<8}")
    print("-" * 80)
    for name, ok, result in results:
        status = "✅" if ok else "❌"
        if result:
            t = result.steps_time
            print(f"{name:<25} {status:<5} {result.total_time_sec:<8} "
                  f"{t.get('cut','-'):<6} {t.get('vertical_9_16','-'):<8} "
                  f"{t.get('subs','-'):<7} {t.get('overlay','-'):<8} "
                  f"{result.final.size_mb:.1f}MB")
        else:
            print(f"{name:<25} {status:<5} fallo")

    print(f"\nClips finales en: {OUTPUT_DIR}")
    print("\n   SPRINT 1.1 — el pipeline esta listo para integrar al worker.")
    print("   Proximo paso (Dia 7): upload a R2 + schema Supabase.")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(run_tests())
