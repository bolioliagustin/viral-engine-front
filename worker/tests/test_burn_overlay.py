"""
Sprint 1.1 — Dia 5: Test de burn_overlay_text

Prueba los 3 estilos de overlay sobre un clip 9:16 con subs ya quemados.
Asi podemos ver el resultado final: clip + subs + hook inicial.

Run desde worker/: python tests/test_burn_overlay.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.clip_generator import (
    burn_overlay_text,
    probe_video,
    ClipGenerationError,
    OVERLAY_STYLES,
)


WORKER_DIR = Path(__file__).parent.parent
INPUT_CLIP = WORKER_DIR / "clips" / "test_sprint_1_1_subs" / "middle_9x16_tiktok_viral.mp4"
OUTPUT_DIR = WORKER_DIR / "clips" / "test_sprint_1_1_overlays"


# Casos: (nombre_archivo, estilo, posicion, texto, duracion)
TEST_CASES = [
    ("tiktok_viral_top", "tiktok_viral", "top", "NADIE TE CUENTA ESTO", 3.5),
    ("question_top", "question", "top", "¿SABIAS ESTO?", 3.0),
    ("stat_top", "stat", "top", "92% FALLAN AQUI", 3.5),
    ("tiktok_viral_center", "tiktok_viral", "center", "ESPERA EL FINAL", 2.5),
]


def run_tests():
    print("=" * 80)
    print("TEST: burn_overlay_text — Sprint 1.1 Dia 5")
    print("=" * 80)

    if not INPUT_CLIP.exists():
        print(f"❌ Clip con subs no existe: {INPUT_CLIP}")
        print("   Ejecuta primero tests/test_burn_subtitles.py")
        return 1

    source = probe_video(str(INPUT_CLIP))
    print(f"\nClip de entrada (ya tiene subs quemados):")
    print(f"  {source.width}x{source.height} ({source.aspect_ratio}), "
          f"{source.duration_sec:.1f}s, {source.size_mb:.1f}MB")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUTPUT_DIR.glob("*.mp4"):
        old.unlink()
    for old in OUTPUT_DIR.glob("*.ass"):
        old.unlink()

    results = []
    for name, style, position, text, duration in TEST_CASES:
        print(f"\n{'─' * 80}")
        print(f"Test: {name}")
        print(f"  estilo={style}, pos={position}, duracion={duration}s")
        print(f"  texto: '{text}'")

        output = OUTPUT_DIR / f"{name}.mp4"
        t0 = time.time()
        try:
            meta = burn_overlay_text(
                video_path=str(INPUT_CLIP),
                text=text,
                output_path=str(output),
                position=position,
                start_sec=0.0,
                duration_sec=duration,
                style=style,
            )
            elapsed = time.time() - t0
            dims_ok = meta.width == 1080 and meta.height == 1920
            dur_ok = abs(meta.duration_sec - source.duration_sec) < 0.5
            audio_ok = meta.has_audio

            ok = dims_ok and dur_ok and audio_ok
            marker = "✅" if ok else "⚠️"
            print(f"  {marker} OK en {elapsed:.1f}s | {meta.size_mb:.1f}MB")
            results.append((name, ok, elapsed, meta))
        except ClipGenerationError as e:
            print(f"  ❌ FALLO: {e}")
            results.append((name, False, time.time() - t0, None))
        except Exception as e:
            print(f"  ❌ ERROR INESPERADO: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False, time.time() - t0, None))

    # Validaciones de error (deben fallar)
    print(f"\n{'─' * 80}")
    print("Validaciones (deben fallar con error claro)")
    print(f"{'─' * 80}")
    error_cases = [
        ({"text": ""}, "texto vacio"),
        ({"text": "ok", "style": "no_existe"}, "estilo inexistente"),
        ({"text": "ok", "position": "diagonal"}, "posicion invalida"),
        ({"text": "ok", "duration_sec": 0}, "duracion cero"),
    ]
    for kwargs, desc in error_cases:
        try:
            burn_overlay_text(
                video_path=str(INPUT_CLIP),
                output_path=str(OUTPUT_DIR / "invalid.mp4"),
                **kwargs,
            )
            print(f"  ⚠️  {desc}: no fallo (deberia)")
        except ClipGenerationError as e:
            print(f"  ✅ {desc}: {str(e)[:80]}")

    # Resumen
    print("\n" + "=" * 80)
    print("RESUMEN")
    print("=" * 80)
    passed = sum(1 for _, ok, _, _ in results if ok)
    total = len(results)
    print(f"\n{passed}/{total} tests pasaron\n")
    for name, ok, elapsed, meta in results:
        status = "✅" if ok else "❌"
        if meta:
            print(f"  {status} {name:25} | {elapsed:5.1f}s | {meta.size_mb:5.1f}MB")
        else:
            print(f"  {status} {name:25} | {elapsed:5.1f}s | fallo")

    print(f"\nClips generados en: {OUTPUT_DIR}")
    print("   VALIDACION VISUAL:")
    print("   - tiktok_viral_top: texto grande BLANCO con borde negro, arriba, primeros 3.5s")
    print("   - question_top: AMARILLO, un poco mas chico, primeros 3s")
    print("   - stat_top: con caja NEGRA de fondo (tipo chyron), primeros 3.5s")
    print("   - tiktok_viral_center: mismo estilo pero en el centro del video")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(run_tests())
