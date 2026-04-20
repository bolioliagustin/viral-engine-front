"""
Sprint 1.1 — Dia 4: Test de burn_subtitles

Toma el clip 9:16 del Dia 3 y le quema subtitulos usando segmentos sinteticos
que simulan un podcast en espanol. Prueba los 3 estilos disponibles.

Run desde worker/: python tests/test_burn_subtitles.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.clip_generator import (
    segments_to_srt,
    burn_subtitles,
    probe_video,
    ClipGenerationError,
    SUBTITLE_STYLES,
)


WORKER_DIR = Path(__file__).parent.parent
INPUT_CLIP = WORKER_DIR / "clips" / "test_sprint_1_1_9x16" / "middle_9x16.mp4"
OUTPUT_DIR = WORKER_DIR / "clips" / "test_sprint_1_1_subs"


# Segmentos sinteticos simulando un podcast hispano
# El clip "middle" fue cortado del s60 al s90 del video original,
# pero internamente el clip va de 0-30s, asi que pasamos start_offset=0.
# En un caso real, el offset seria 60.0 si los segments estan en tiempo original.
SYNTHETIC_SEGMENTS = [
    {"start": 0.0,  "end": 3.5,  "text": "Eso es lo que nadie te cuenta en internet"},
    {"start": 3.5,  "end": 7.0,  "text": "Porque la mayoria tiene miedo de decirlo en voz alta"},
    {"start": 7.0,  "end": 11.0, "text": "Pero es exactamente lo que separa a los que logran resultados de los que no"},
    {"start": 11.0, "end": 14.5, "text": "La diferencia esta en la constancia, no en el talento"},
    {"start": 14.5, "end": 18.0, "text": "Y te lo digo porque yo pase por eso mismo hace tres anios"},
    {"start": 18.0, "end": 22.0, "text": "Cuando empece no tenia ni idea de lo que estaba haciendo"},
    {"start": 22.0, "end": 26.0, "text": "Solo sabia que no queria rendirme hasta ver un resultado real"},
    {"start": 26.0, "end": 30.0, "text": "Y mira donde estoy ahora, nada es casualidad"},
]


def run_tests():
    print("=" * 80)
    print("TEST: burn_subtitles — Sprint 1.1 Dia 4")
    print("=" * 80)

    if not INPUT_CLIP.exists():
        print(f"❌ Clip 9:16 no existe: {INPUT_CLIP}")
        print("   Ejecuta primero tests/test_vertical_9_16.py")
        return 1

    source = probe_video(str(INPUT_CLIP))
    print(f"\nClip de entrada:")
    print(f"  {source.path}")
    print(f"  {source.width}x{source.height} ({source.aspect_ratio}), "
          f"{source.duration_sec:.1f}s, {source.size_mb:.1f}MB")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUTPUT_DIR.glob("*.mp4"):
        old.unlink()
    for old in OUTPUT_DIR.glob("*.srt"):
        old.unlink()

    # Paso 1: generar SRT
    print(f"\n{'─' * 80}")
    print("Paso 1: generar SRT desde segments")
    print(f"{'─' * 80}")
    srt_path = OUTPUT_DIR / "subs.srt"
    try:
        segments_to_srt(
            segments=SYNTHETIC_SEGMENTS,
            output_path=str(srt_path),
            start_offset_sec=0.0,
            clip_duration_sec=source.duration_sec,
            max_words_per_line=5,
        )
        srt_size = srt_path.stat().st_size
        lines = srt_path.read_text(encoding="utf-8").count("\n-->")
        print(f"  ✅ SRT generado: {srt_path.name} ({srt_size} bytes, ~{lines} lineas)")
        # Mostrar las primeras 3 entradas
        content = srt_path.read_text(encoding="utf-8")
        preview = "\n".join(content.splitlines()[:12])
        print(f"\n  Preview:\n{preview}")
    except ClipGenerationError as e:
        print(f"  ❌ FALLO: {e}")
        return 1

    # Paso 2: probar cada estilo
    results = []
    for style_name in SUBTITLE_STYLES.keys():
        print(f"\n{'─' * 80}")
        print(f"Paso 2: burn_subtitles con estilo '{style_name}'")
        print(f"{'─' * 80}")
        output = OUTPUT_DIR / f"middle_9x16_{style_name}.mp4"

        t0 = time.time()
        try:
            meta = burn_subtitles(
                video_path=str(INPUT_CLIP),
                srt_path=str(srt_path),
                output_path=str(output),
                style=style_name,
            )
            elapsed = time.time() - t0
            dims_ok = meta.width == 1080 and meta.height == 1920
            dur_ok = abs(meta.duration_sec - source.duration_sec) < 0.5
            audio_ok = meta.has_audio
            ok = dims_ok and dur_ok and audio_ok

            marker = "✅" if ok else "⚠️"
            print(f"  {marker} OK en {elapsed:.1f}s")
            print(f"     Salida: {meta.width}x{meta.height}, {meta.duration_sec:.1f}s, {meta.size_mb:.1f}MB")
            results.append((style_name, ok, elapsed, meta))
        except ClipGenerationError as e:
            print(f"  ❌ FALLO: {e}")
            results.append((style_name, False, time.time() - t0, None))
        except Exception as e:
            print(f"  ❌ ERROR INESPERADO: {e}")
            import traceback
            traceback.print_exc()
            results.append((style_name, False, time.time() - t0, None))

    # Resumen
    print("\n" + "=" * 80)
    print("RESUMEN")
    print("=" * 80)
    passed = sum(1 for _, ok, _, _ in results if ok)
    total = len(results)
    print(f"\n{passed}/{total} estilos funcionan\n")

    for name, ok, elapsed, meta in results:
        status = "✅" if ok else "❌"
        if meta:
            print(f"  {status} {name:15} | {elapsed:5.1f}s | {meta.size_mb:5.1f}MB")
        else:
            print(f"  {status} {name:15} | {elapsed:5.1f}s | fallo")

    print(f"\nClips generados en: {OUTPUT_DIR}")
    print("   VALIDACION VISUAL OBLIGATORIA:")
    print("   - Los subs aparecen sincronizados con el audio")
    print("   - Se leen bien (no muy chicos ni muy grandes)")
    print("   - tiktok_viral debe tener borde negro grueso y font bold")
    print("   - clean debe ser mas sutil, profesional")
    print("   - podcast debe ser mas pequeno y al fondo")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(run_tests())
