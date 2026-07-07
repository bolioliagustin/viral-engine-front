"""
Fase 6 — Loop de evaluación automatizado del golden set.

Corre la fase de ANÁLISIS del pipeline (clasificador + selección de momentos
+ validaciones) contra los videos del golden set y reporta métricas de
calidad. Sale con exit code != 0 si alguna métrica queda por debajo de los
umbrales de `golden_set.json > thresholds` — usable en CI antes de deploy.

Uso (desde la raíz del repo, con el .env configurado):

    python worker/eval/run_golden_set.py                 # análisis-only (rápido, ~1 llamada LLM por video)
    python worker/eval/run_golden_set.py --copy          # además genera copy (pasada B) + juez por momento
    python worker/eval/run_golden_set.py --video business_spanish_01   # solo un video
    python worker/eval/run_golden_set.py --json          # output JSON (para CI)

Métricas reportadas:
  - category_accuracy: % de videos donde el clasificador matchea expected_category
  - duration_pass_rate: % de momentos que pasan validate_durations (>=10s)
  - duration_untruncated_rate: % de momentos que NO necesitaron truncado a 60s
  - verification_pass_rate: % de momentos cuyas first/last phrases matchean el transcript
  - moments_per_video: cantidad de momentos finales
  Con --copy además:
  - content validator stats (tweet count, char ranges, clichés)
  - judge_delta: promedio |score_judge - score_llm| (calibración)

Transcripts: usa transcription_cache (Supabase) si existe; si no, los baja
vía yt_transcript (requiere red / SUPADATA_API_KEY).
"""
import argparse
import json
import statistics
import sys
from pathlib import Path

# Permitir imports del worker (services.*, config.*) al correr desde la raíz
WORKER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKER_DIR))

from dotenv import load_dotenv
load_dotenv(WORKER_DIR.parent / ".env")

GOLDEN_SET_PATH = Path(__file__).resolve().parent / "golden_set.json"


def _load_golden_set() -> dict:
    with open(GOLDEN_SET_PATH, encoding="utf-8") as f:
        return json.load(f)


def _get_transcript(video: dict):
    """Transcript desde cache Supabase o red. Returns (transcript, video_info) o None."""
    from services.yt_transcript import get_video_id, get_video_metadata, get_youtube_transcript
    from services.transcript_cache import get_cached_transcript

    video_id = video.get("youtube_id") or get_video_id(video.get("url") or "")
    if not video_id:
        print(f"   ❌ Sin youtube_id ni URL válida")
        return None

    cached = get_cached_transcript(video_id)
    if cached:
        print(f"   ✅ Transcript desde cache ({len(cached.get('segments') or [])} segments)")
        video_info = get_video_metadata(video_id)
        segs = cached.get("segments") or []
        if not video_info.get("duration") and segs:
            video_info["duration"] = float(segs[-1].get("end", 0))
        return cached, video_info

    print(f"   🌐 Transcript no cacheado — bajando de YouTube...")
    try:
        return get_youtube_transcript(video.get("url") or f"https://www.youtube.com/watch?v={video_id}")
    except Exception as e:
        print(f"   ❌ No se pudo obtener transcript: {str(e)[:120]}")
        return None


def evaluate_video(video: dict, *, with_copy: bool = False) -> dict:
    """Evalúa un video del golden set. Returns dict de métricas."""
    from services.processor import analyze_with_openrouter, get_video_category
    from services.validation import (
        validate_durations,
        filter_overlapping_moments,
        validate_against_transcript,
        _words_in_range,
    )
    from config.model_tiers import get_model

    result = {
        "id": video["id"],
        "ok": False,
        "category_match": None,
        "moments_selected": 0,
        "moments_valid": 0,
        "moments_truncated": 0,
        "verification_pass": 0,
        "verification_total": 0,
        "errors": [],
    }

    loaded = _get_transcript(video)
    if not loaded:
        result["errors"].append("no_transcript")
        return result
    transcript, video_info = loaded

    # ── Clasificador ─────────────────────────────────────────────────────
    from openai import OpenAI
    import os
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.getenv("OPENROUTER_API_KEY"))
    excerpt = " ".join(
        (sg.get("text") or "").strip() for sg in (transcript.get("segments") or [])[:40]
    )[:1500]
    category = get_video_category(video_info, client, transcript_excerpt=excerpt)
    expected = video.get("expected_category")
    result["category_detected"] = category
    result["category_match"] = (category == expected) if expected else None
    print(f"   📂 Categoría: {category} (esperada: {expected}) "
          f"{'✅' if result['category_match'] else '❌' if result['category_match'] is False else ''}")

    # ── Análisis (pasada A o mega-prompt según env) ──────────────────────
    try:
        analysis = analyze_with_openrouter(transcript, video_info)
    except Exception as e:
        result["errors"].append(f"analysis_failed: {str(e)[:150]}")
        return result

    moments = analysis.viral_moments
    result["moments_selected"] = len(moments)

    # ── validate_durations: pass rate + truncation rate ──────────────────
    truncated = 0
    for m in moments:
        if m.start_time is not None and m.end_time is not None:
            if (m.end_time - m.start_time) > 60:
                truncated += 1
    valid = validate_durations(list(moments), min_duration=10, max_duration=60, transcript=transcript)
    valid = filter_overlapping_moments(valid, max_overlap_ratio=0.5)
    result["moments_valid"] = len(valid)
    result["moments_truncated"] = truncated

    # ── Verificación first/last phrase contra transcript ─────────────────
    last_phrase_punct = 0
    last_phrase_total = 0
    for m in valid:
        if not getattr(m, "verification", None):
            continue
        result["verification_total"] += 1
        if validate_against_transcript(m, transcript):
            result["verification_pass"] += 1
        last = getattr(m.verification, "last_phrase_in_audio", "") or ""
        if last.strip():
            last_phrase_total += 1
            if last.strip()[-1] in ".?!…":
                last_phrase_punct += 1
    result["last_phrase_punct_rate"] = (
        last_phrase_punct / last_phrase_total if last_phrase_total else None
    )

    # ── Opcional: pasada B + validators + juez ───────────────────────────
    if with_copy and valid:
        from services.processor import generate_moment_copy_full
        from services.content_validators import clean_moment
        from services.scorer import judge_moment_scores

        copy_problems = 0
        judge_deltas = []
        for m in valid:
            clip_text = _words_in_range(
                transcript.get("segments") or [],
                float(m.start_time), float(m.end_time),
            )
            if not clip_text.strip():
                continue
            ok = generate_moment_copy_full(
                m, clip_text,
                category=category,
                language=transcript.get("language"),
            )
            if ok:
                stats = clean_moment(m.model_dump())
                copy_problems += len(stats.problems)
            judge = judge_moment_scores(
                clip_text,
                hook=m.hook or "",
                viral_overlay=m.viral_overlay or "",
                category=category,
                clip_duration_sec=float(m.end_time - m.start_time),
            )
            if judge and m.scores:
                llm_avg = (m.scores.hook + m.scores.retention + m.scores.shareability) / 3
                judge_avg = (judge["hook"] + judge["retention"] + judge["shareability"]) / 3
                judge_deltas.append(abs(judge_avg - llm_avg))

        result["copy_problems"] = copy_problems
        result["judge_delta_avg"] = round(statistics.mean(judge_deltas), 2) if judge_deltas else None

    result["ok"] = True
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Golden set eval del pipeline de análisis IA")
    parser.add_argument("--copy", action="store_true", help="Correr también pasada B + juez (más lento/caro)")
    parser.add_argument("--video", help="Evaluar solo este id del golden set")
    parser.add_argument("--json", action="store_true", help="Output JSON (para CI)")
    args = parser.parse_args()

    golden = _load_golden_set()
    thresholds = golden.get("thresholds") or {}
    videos = [v for v in golden.get("videos") or [] if v.get("enabled", True)]
    if args.video:
        videos = [v for v in videos if v["id"] == args.video]
        if not videos:
            print(f"❌ Video '{args.video}' no encontrado o deshabilitado")
            return 2

    if not videos:
        print("❌ Golden set vacío (todos deshabilitados)")
        return 2

    print(f"🏆 Golden set eval: {len(videos)} video(s), copy={'sí' if args.copy else 'no'}\n")

    results = []
    for video in videos:
        print(f"── {video['id']} ─────────────────────────────")
        try:
            results.append(evaluate_video(video, with_copy=args.copy))
        except Exception as e:
            print(f"   ❌ Eval crash: {str(e)[:200]}")
            results.append({"id": video["id"], "ok": False, "errors": [f"crash: {str(e)[:150]}"]})
        print()

    # ── Agregación ────────────────────────────────────────────────────────
    ok_results = [r for r in results if r.get("ok")]
    cat_checked = [r for r in ok_results if r.get("category_match") is not None]
    category_accuracy = (
        sum(1 for r in cat_checked if r["category_match"]) / len(cat_checked)
        if cat_checked else None
    )
    total_selected = sum(r.get("moments_selected", 0) for r in ok_results)
    total_valid = sum(r.get("moments_valid", 0) for r in ok_results)
    total_truncated = sum(r.get("moments_truncated", 0) for r in ok_results)
    duration_pass_rate = (total_valid / total_selected) if total_selected else None
    untruncated_rate = (1 - total_truncated / total_selected) if total_selected else None
    ver_total = sum(r.get("verification_total", 0) for r in ok_results)
    ver_pass = sum(r.get("verification_pass", 0) for r in ok_results)
    verification_pass_rate = (ver_pass / ver_total) if ver_total else None
    min_moments = min((r.get("moments_valid", 0) for r in ok_results), default=0)

    summary = {
        "videos_evaluated": len(results),
        "videos_ok": len(ok_results),
        "category_accuracy": category_accuracy,
        "duration_pass_rate": duration_pass_rate,
        "duration_untruncated_rate": untruncated_rate,
        "verification_pass_rate": verification_pass_rate,
        "min_moments_per_video": min_moments,
        "results": results,
    }

    # ── Chequeo de umbrales ───────────────────────────────────────────────
    failures = []

    def _check(name: str, value, threshold_key: str):
        threshold = thresholds.get(threshold_key)
        if threshold is None or value is None:
            return
        if value < threshold:
            failures.append(f"{name}={value:.2f} < {threshold} ({threshold_key})")

    if len(ok_results) < len(results):
        failures.append(f"{len(results) - len(ok_results)} video(s) fallaron la evaluación")
    _check("category_accuracy", category_accuracy, "category_accuracy_min")
    _check("duration_pass_rate", duration_pass_rate, "duration_pass_rate_min")
    _check("duration_untruncated_rate", untruncated_rate, "duration_untruncated_rate_min")
    _check("verification_pass_rate", verification_pass_rate, "verification_pass_rate_min")
    if thresholds.get("min_moments_per_video") is not None and ok_results:
        if min_moments < thresholds["min_moments_per_video"]:
            failures.append(
                f"min_moments_per_video={min_moments} < {thresholds['min_moments_per_video']}"
            )
    if args.copy:
        deltas = [r.get("judge_delta_avg") for r in ok_results if r.get("judge_delta_avg") is not None]
        if deltas and thresholds.get("judge_llm_delta_max") is not None:
            avg_delta = statistics.mean(deltas)
            if avg_delta > thresholds["judge_llm_delta_max"]:
                failures.append(
                    f"judge_llm_delta_avg={avg_delta:.2f} > {thresholds['judge_llm_delta_max']}"
                )
    punct_rates = [r.get("last_phrase_punct_rate") for r in ok_results if r.get("last_phrase_punct_rate") is not None]
    if punct_rates and thresholds.get("last_phrase_punct_rate_min") is not None:
        avg_punct = statistics.mean(punct_rates)
        if avg_punct < thresholds["last_phrase_punct_rate_min"]:
            failures.append(
                f"last_phrase_punct_rate={avg_punct:.2f} < {thresholds['last_phrase_punct_rate_min']}"
            )

    summary["failures"] = failures
    summary["passed"] = not failures

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    else:
        print("═══════════════════════════════════════════")
        print("📊 RESUMEN GOLDEN SET")
        print("═══════════════════════════════════════════")
        fmt = lambda v: f"{v:.0%}" if isinstance(v, float) else str(v)
        print(f"   Videos OK:              {len(ok_results)}/{len(results)}")
        print(f"   Category accuracy:      {fmt(category_accuracy) if category_accuracy is not None else 'n/a'}")
        print(f"   Duration pass rate:     {fmt(duration_pass_rate) if duration_pass_rate is not None else 'n/a'}")
        print(f"   Untruncated rate:       {fmt(untruncated_rate) if untruncated_rate is not None else 'n/a'}")
        print(f"   Verification pass rate: {fmt(verification_pass_rate) if verification_pass_rate is not None else 'n/a'}")
        punct_avg = statistics.mean(
            [r["last_phrase_punct_rate"] for r in ok_results if r.get("last_phrase_punct_rate") is not None]
        ) if ok_results else None
        if punct_avg is not None:
            print(f"   Last phrase punct rate: {fmt(punct_avg)}")
        print(f"   Min moments/video:      {min_moments}")
        if args.copy:
            deltas = [r.get("judge_delta_avg") for r in ok_results if r.get("judge_delta_avg") is not None]
            if deltas:
                print(f"   Judge vs LLM delta:     {statistics.mean(deltas):.2f} pts")
        print()
        if failures:
            print("❌ REGRESIONES DETECTADAS:")
            for f in failures:
                print(f"   - {f}")
        else:
            print("✅ Todos los umbrales OK")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
