"""
Golden set eval — regresión de calidad IA por tier.

Tiers (de más rápido a más completo):
  smoke    — 1 video, ~2-4 min, pre-deploy
  analysis — todos los videos enabled, sin copy (~10-20 min)
  full     — analysis + pasada B + juez (~30-60 min)
  e2e      — pipeline real por clip (descarga + Whisper + snap + Pasada B +
             juez + render) en modo dry-run: nada se persiste ni se sube.
             Emite juez, flags, densidad, duraciones, costo y tiempo por clip.

Uso (desde worker/ en Docker, o repo root en local):

    python eval/run_golden_set.py --tier smoke
    python eval/run_golden_set.py --tier analysis
    python eval/run_golden_set.py --tier full
    python eval/run_golden_set.py --tier analysis --video claude_hacks_regression_01
    python eval/run_golden_set.py --tier full --json > /tmp/report.json
    EVAL_DRY_RUN=1 ENVIRONMENT=development python eval/run_golden_set.py --tier e2e --json \
        2>eval/runs/<fecha>-<PROMPT_VERSION>.log >eval/runs/<fecha>-<PROMPT_VERSION>.json

Con --json: logs humanos van a stderr; stdout es solo JSON válido.

Legacy: --copy equivale a --tier full.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKER_DIR))

from dotenv import load_dotenv

load_dotenv(WORKER_DIR.parent / ".env")
load_dotenv(WORKER_DIR / ".env")

GOLDEN_SET_PATH = Path(__file__).resolve().parent / "golden_set.json"


def _log(msg: str = "", *, json_mode: bool = False) -> None:
    stream = sys.stderr if json_mode else sys.stdout
    print(msg, file=stream)


def _load_golden_set() -> dict:
    with open(GOLDEN_SET_PATH, encoding="utf-8") as f:
        return json.load(f)


def _get_transcript(video: dict):
    from services.yt_transcript import get_video_id, get_video_metadata, get_youtube_transcript
    from services.transcript_cache import get_cached_transcript

    video_id = video.get("youtube_id") or get_video_id(video.get("url") or "")
    if not video_id:
        _log("   ❌ Sin youtube_id ni URL válida")
        return None

    cached = get_cached_transcript(video_id)
    if cached:
        _log(f"   ✅ Transcript desde cache ({len(cached.get('segments') or [])} segments)")
        video_info = get_video_metadata(video_id)
        segs = cached.get("segments") or []
        if not video_info.get("duration") and segs:
            video_info["duration"] = float(segs[-1].get("end", 0))
        return cached, video_info

    _log("   🌐 Transcript no cacheado — bajando de YouTube...")
    try:
        return get_youtube_transcript(
            video.get("url") or f"https://www.youtube.com/watch?v={video_id}"
        )
    except Exception as e:
        _log(f"   ❌ No se pudo obtener transcript: {str(e)[:120]}")
        return None


def evaluate_video(video: dict, *, with_copy: bool = False, json_mode: bool = False) -> dict:
    from openai import OpenAI
    import os

    from services.processor import analyze_with_openrouter, get_video_category
    from services.validation import (
        validate_durations,
        filter_overlapping_moments,
        evaluate_moment_phrase_metrics,
        _words_in_range,
    )
    from config.model_tiers import resolved_models

    result = {
        "id": video["id"],
        "ok": False,
        "category_match": None,
        "moments_selected": 0,
        "moments_valid": 0,
        "moments_truncated": 0,
        "verification_strict_pass": 0,
        "verification_strict_total": 0,
        "phrase_anchor_pass": 0,
        "phrase_anchor_total": 0,
        "errors": [],
    }

    loaded = _get_transcript(video)
    if not loaded:
        result["errors"].append("no_transcript")
        return result
    transcript, video_info = loaded

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API_KEY"),
    )
    excerpt = " ".join(
        (sg.get("text") or "").strip() for sg in (transcript.get("segments") or [])[:40]
    )[:1500]
    category = get_video_category(video_info, client, transcript_excerpt=excerpt)
    expected = video.get("expected_category")
    result["category_detected"] = category
    result["category_match"] = (category == expected) if expected else None
    _log(
        f"   📂 Categoría: {category} (esperada: {expected}) "
        f"{'✅' if result['category_match'] else '❌' if result['category_match'] is False else ''}",
        json_mode=json_mode,
    )

    try:
        analysis = analyze_with_openrouter(transcript, video_info)
    except Exception as e:
        result["errors"].append(f"analysis_failed: {str(e)[:150]}")
        return result

    moments = analysis.viral_moments
    result["moments_selected"] = len(moments)

    truncated = 0
    for m in moments:
        if m.start_time is not None and m.end_time is not None:
            if (m.end_time - m.start_time) > 60:
                truncated += 1
    valid = validate_durations(
        list(moments), min_duration=10, max_duration=60, transcript=transcript
    )
    valid = filter_overlapping_moments(valid, max_overlap_ratio=0.5)
    result["moments_valid"] = len(valid)
    result["moments_truncated"] = truncated

    last_phrase_punct = 0
    last_phrase_total = 0
    for m in valid:
        metrics = evaluate_moment_phrase_metrics(m, transcript)
        if not metrics.get("has_verification"):
            continue
        result["verification_strict_total"] += 1
        result["phrase_anchor_total"] += 1
        if metrics["strict_pass"]:
            result["verification_strict_pass"] += 1
        if metrics["anchor_pass"]:
            result["phrase_anchor_pass"] += 1
        last_phrase_total += 1
        if metrics["last_phrase_punct"]:
            last_phrase_punct += 1

    result["last_phrase_punct_rate"] = (
        last_phrase_punct / last_phrase_total if last_phrase_total else None
    )
    if result["phrase_anchor_total"]:
        result["phrase_anchor_rate"] = (
            result["phrase_anchor_pass"] / result["phrase_anchor_total"]
        )
    if result["verification_strict_total"]:
        result["verification_strict_rate"] = (
            result["verification_strict_pass"] / result["verification_strict_total"]
        )

    if with_copy and valid:
        from services.processor import generate_moment_copy_full
        from services.content_validators import clean_moment
        from services.scorer import judge_moment_scores

        copy_problems = 0
        copy_moments = 0
        copy_clean_moments = 0
        judge_deltas = []
        judge_ok = 0
        judge_total = 0

        for m in valid:
            clip_text = _words_in_range(
                transcript.get("segments") or [],
                float(m.start_time), float(m.end_time),
            )
            if not clip_text.strip():
                continue
            copy_moments += 1
            ok = generate_moment_copy_full(
                m,
                clip_text,
                category=category,
                language=transcript.get("language"),
            )
            problems = 0
            if ok:
                stats = clean_moment(m.model_dump())
                problems = len(stats.problems)
            copy_problems += problems
            if problems == 0:
                copy_clean_moments += 1

            judge_total += 1
            judge = judge_moment_scores(
                clip_text,
                hook=m.hook or "",
                viral_overlay=m.viral_overlay or "",
                category=category,
                clip_duration_sec=float(m.end_time - m.start_time),
            )
            if judge:
                judge_ok += 1
            if judge and m.scores:
                llm_avg = (m.scores.hook + m.scores.retention + m.scores.shareability) / 3
                judge_avg = (judge["hook"] + judge["retention"] + judge["shareability"]) / 3
                judge_deltas.append(abs(judge_avg - llm_avg))

        result["copy_problems"] = copy_problems
        result["copy_moments"] = copy_moments
        result["copy_clean_moments"] = copy_clean_moments
        result["judge_ok_count"] = judge_ok
        result["judge_total"] = judge_total
        if judge_deltas:
            import statistics
            result["judge_delta_avg"] = round(statistics.mean(judge_deltas), 2)

    result["models"] = resolved_models()
    result["ok"] = True
    return result


# ─── Tier e2e ────────────────────────────────────────────────────────────────

# Presupuesto por video (segundos de reloj). El job de main.py tiene su propio
# timeout de 30 min; este es más corto para que un video largo no bloquee la
# corrida: si se agota, el video queda como timed_out y se sigue con el resto.
DEFAULT_E2E_VIDEO_BUDGET_SEC = 25 * 60


def _prepare_e2e_runtime(*, json_mode: bool):
    """
    Deja el proceso listo para correr main.process_job en dry-run:
    EVAL_DRY_RUN=1 (obligatorio: sin eso el pipeline escribiría en Supabase y
    subiría a R2), Sentry apagado salvo que se pida explícitamente, y los
    logs del worker (que main.py manda a stdout) redirigidos a stderr cuando
    stdout tiene que ser JSON.
    """
    os.environ["EVAL_DRY_RUN"] = "1"
    os.environ.setdefault("SENTRY_DSN_WORKER", "")

    real_stdout = sys.stdout
    if json_mode:
        # main.py imprime al importar (setup_logging, validate_env) y crea su
        # StreamHandler sobre sys.stdout: durante el import, stdout es stderr.
        sys.stdout = sys.stderr
    try:
        import main  # noqa: F401 — importa el pipeline (setup_logging, validate_env)
    finally:
        sys.stdout = real_stdout

    if json_mode:
        import logging
        logger = logging.getLogger("worker")
        for h in logger.handlers:
            if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is real_stdout:
                h.setStream(sys.stderr)
    return main


def evaluate_video_e2e(
    video: dict,
    *,
    json_mode: bool = False,
    budget_sec: float = DEFAULT_E2E_VIDEO_BUDGET_SEC,
) -> dict:
    """
    Corre main.process_job sobre el video con EVAL_DRY_RUN=1 y arma el
    resultado por video: clips (uno por momento, ver
    eval_metrics.build_e2e_clip_record), costo (rollup de usage_tracker) y
    segundos de reloj.
    """
    from services import supabase_client as sbc
    from services import usage_tracker as ut
    from eval_metrics import build_e2e_clip_record

    main = _prepare_e2e_runtime(json_mode=json_mode)
    assert sbc.is_dry_run(), "EVAL_DRY_RUN no activo: abortando para no persistir"

    job_id = str(uuid.uuid4())
    result = {
        "id": video["id"],
        "youtube_id": video.get("youtube_id"),
        "job_id": job_id,
        "ok": False,
        "status": None,
        "timed_out": False,
        "clips": [],
        "clips_count": 0,
        "clips_rendered": 0,
        "cost_usd": None,
        "cost_by_task": None,
        "elapsed_sec": None,
        "errors": [],
    }

    sbc.reset_dry_run()
    ut.DRY_RUN_ROLLUPS.pop(job_id, None)
    job_data = {
        "id": job_id,
        "videoUrl": video.get("url") or f"https://www.youtube.com/watch?v={video.get('youtube_id')}",
        "userId": None,
        "tone": "profesional",
    }
    crash: list[str] = []

    def _run():
        try:
            main.process_job(job_data)
        except Exception as e:  # process_job ya captura casi todo; esto es red de seguridad
            crash.append(f"{type(e).__name__}: {str(e)[:200]}")

    _log(f"   🧪 dry-run job={job_id} (presupuesto {budget_sec / 60:.0f} min)", json_mode=json_mode)
    t0 = time.time()
    worker = threading.Thread(target=_run, name=f"e2e-{video['id']}", daemon=True)
    worker.start()
    worker.join(budget_sec)
    elapsed = round(time.time() - t0, 1)
    result["elapsed_sec"] = elapsed

    if worker.is_alive():
        result["timed_out"] = True
        result["errors"].append(f"timeout: superó {budget_sec / 60:.0f} min")
        _log(f"   ⏰ Timeout: el job sigue corriendo tras {elapsed:.0f}s — se excluye", json_mode=json_mode)
        return result
    if crash:
        result["errors"].extend(crash)

    job_state = sbc.DRY_RUN_JOBS.get(job_id) or {}
    result["status"] = job_state.get("status")
    result["video_title"] = job_state.get("video_title")
    if job_state.get("error_message"):
        result["errors"].append(f"job_failed: {str(job_state['error_message'])[:200]}")

    rows = [r for r in sbc.DRY_RUN_RESULTS if r.get("job_id") == job_id]
    by_moment: dict[int, list[dict]] = {}
    for r in rows:
        by_moment.setdefault(int(r.get("moment_index") or 0), []).append(r)
    clips = [
        build_e2e_clip_record(video, by_moment[mi])
        for mi in sorted(by_moment)
    ]
    result["clips"] = clips
    result["clips_count"] = len(clips)
    result["clips_rendered"] = sum(1 for c in clips if c["clip_rendered"])

    rollup = ut.DRY_RUN_ROLLUPS.pop(job_id, None)
    if rollup:
        result["cost_usd"] = rollup.get("total_cost_usd")
        result["cost_by_task"] = rollup.get("by_task")
        result["whisper_seconds"] = rollup.get("whisper_seconds")
        result["whisper_provider"] = rollup.get("whisper_provider")

    result["ok"] = result["status"] == "completed" and bool(clips)
    _log(
        f"   {'✅' if result['ok'] else '❌'} {len(clips)} clips "
        f"({result['clips_rendered']} renderizados) | "
        f"${result['cost_usd'] or 0:.4f} | {elapsed:.0f}s",
        json_mode=json_mode,
    )
    for c in clips:
        j = c.get("score_judge") or {}
        _log(
            f"      m{c['moment_index']}: {c['duration_chosen_sec']}s→{c['duration_final_sec']}s "
            f"juez={j.get('hook')}/{j.get('retention')}/{j.get('shareability')} "
            f"flags={c['clip_quality_issues'] or '-'} "
            f"wps={c['words_per_sec'] if c['words_per_sec'] is None else round(c['words_per_sec'], 2)} "
            f"cap={c['starts_capitalized']} "
            f"| {' '.join(c['first_words'][:6])}…",
            json_mode=json_mode,
        )
    return result


def _print_e2e_summary(summary: dict, failures: list[str], *, json_mode: bool = False) -> None:
    fmt_pct = lambda v: "n/a" if v is None else f"{v:.0%}"  # noqa: E731
    fmt_num = lambda v: "n/a" if v is None else f"{v:.2f}"  # noqa: E731
    p = lambda msg="": _log(msg, json_mode=json_mode)  # noqa: E731
    p("═══════════════════════════════════════════")
    p("📊 RESUMEN GOLDEN SET — tier e2e")
    p("═══════════════════════════════════════════")
    p(f"   Videos OK:               {summary['videos_ok']}/{summary['videos_evaluated']}")
    p(f"   Clips:                   {summary['clips_total']} ({fmt_pct(summary.get('clips_rendered_rate'))} renderizados)")
    p(
        f"   Juez (hook/ret/share):   {fmt_num(summary.get('judge_hook_avg'))} / "
        f"{fmt_num(summary.get('judge_retention_avg'))} / "
        f"{fmt_num(summary.get('judge_shareability_avg'))} → {fmt_num(summary.get('judge_avg'))}"
    )
    p(f"   Juez ≥7 en las tres:     {fmt_pct(summary.get('judge_all_ge7_rate'))}")
    p(f"   verification_failed:     {fmt_pct(summary.get('verification_failed_rate'))}")
    p(f"   late_hook:               {fmt_pct(summary.get('late_hook_rate'))}")
    p(f"   whisper_mismatch_last:   {fmt_pct(summary.get('whisper_mismatch_last_rate'))}")
    p(f"   Arrancan con mayúscula:  {fmt_pct(summary.get('capitalized_start_rate'))}")
    p(f"   Densidad fuera de rango: {fmt_pct(summary.get('density_out_of_range_rate'))}")
    p(
        f"   Duración elegida→final:  {fmt_num(summary.get('duration_chosen_avg'))}s → "
        f"{fmt_num(summary.get('duration_final_avg'))}s"
    )
    p(f"   Costo total:             ${summary.get('total_cost_usd') or 0:.4f}")
    p(f"   Tiempo total:            {(summary.get('total_seconds') or 0) / 60:.1f} min")
    p()
    if failures:
        p("❌ UMBRALES NO ALCANZADOS (informativos hasta tener baseline estable):")
        for f in failures:
            p(f"   - {f}")
    else:
        p("✅ Todos los umbrales OK")


def main() -> int:
    eval_dir = Path(__file__).resolve().parent
    if str(eval_dir) not in sys.path:
        sys.path.insert(0, str(eval_dir))
    from eval_metrics import (
        DEFAULT_TIER,
        TIER_ORDER,
        aggregate_e2e_results,
        aggregate_results,
        check_e2e_thresholds,
        check_thresholds,
        filter_videos_for_tier,
        resolve_tier_config,
    )

    parser = argparse.ArgumentParser(description="Golden set eval por tier")
    parser.add_argument(
        "--tier",
        choices=TIER_ORDER,
        default=DEFAULT_TIER,
        help="smoke (rápido) | analysis (default) | full (+ copy/juez) | e2e (pipeline real, dry-run)",
    )
    parser.add_argument("--copy", action="store_true", help="(legacy) equivale a --tier full")
    parser.add_argument("--video", help="Evaluar solo este id del golden set")
    parser.add_argument("--json", action="store_true", help="JSON en stdout; logs en stderr")
    parser.add_argument(
        "--video-budget-sec",
        type=float,
        default=DEFAULT_E2E_VIDEO_BUDGET_SEC,
        help="(e2e) segundos máximos por video antes de marcarlo timed_out",
    )
    args = parser.parse_args()

    tier = "full" if args.copy else args.tier
    json_mode = args.json

    golden = _load_golden_set()
    tier_cfg = resolve_tier_config(golden, tier)
    with_copy = tier_cfg["include_copy"]
    thresholds = tier_cfg["thresholds"]

    videos = filter_videos_for_tier(
        golden.get("videos") or [],
        tier,
        tier_cfg.get("video_ids"),
    )
    if args.video:
        videos = [v for v in videos if v["id"] == args.video]
        if not videos:
            _log(f"❌ Video '{args.video}' no encontrado o no aplica al tier {tier}", json_mode=json_mode)
            return 2

    if not videos:
        _log(f"❌ Sin videos para tier '{tier}'", json_mode=json_mode)
        return 2

    from config.model_tiers import resolved_models

    if tier == "e2e":
        return _main_e2e(
            videos, thresholds, json_mode=json_mode, budget_sec=args.video_budget_sec,
            aggregate=aggregate_e2e_results, check=check_e2e_thresholds,
        )

    _log(f"🏆 Golden set | tier={tier} | videos={len(videos)} | copy={'sí' if with_copy else 'no'}", json_mode=json_mode)
    _log(f"   Modelos: {resolved_models()}", json_mode=json_mode)
    if tier_cfg.get("description"):
        _log(f"   {tier_cfg['description']}", json_mode=json_mode)
    _log("", json_mode=json_mode)

    results = []
    for video in videos:
        _log(f"── {video['id']} ─────────────────────────────", json_mode=json_mode)
        try:
            results.append(evaluate_video(video, with_copy=with_copy, json_mode=json_mode))
        except Exception as e:
            _log(f"   ❌ Eval crash: {str(e)[:200]}", json_mode=json_mode)
            results.append({"id": video["id"], "ok": False, "errors": [f"crash: {str(e)[:150]}"]})
        _log("", json_mode=json_mode)

    summary = aggregate_results(results, with_copy=with_copy)
    summary["tier"] = tier
    summary["models"] = resolved_models()
    failures = check_thresholds(summary, thresholds, with_copy=with_copy)
    summary["failures"] = failures
    summary["passed"] = not failures

    if json_mode:
        _emit_json(summary)
    else:
        _log("═══════════════════════════════════════════")
        _log("📊 RESUMEN GOLDEN SET")
        _log("═══════════════════════════════════════════")
        fmt = lambda v: f"{v:.0%}" if isinstance(v, float) and v <= 1 else (
            f"{v:.2f}" if isinstance(v, float) else str(v)
        )
        _log(f"   Tier:                   {tier}")
        _log(f"   Videos OK:              {summary['videos_ok']}/{summary['videos_evaluated']}")
        _log(f"   Category accuracy:      {fmt(summary.get('category_accuracy'))}")
        _log(f"   Duration pass rate:     {fmt(summary.get('duration_pass_rate'))}")
        _log(f"   Phrase anchor rate:     {fmt(summary.get('phrase_anchor_pass_rate'))}")
        strict = summary.get("verification_strict_pass_rate")
        if strict is not None:
            _log(f"   Strict verification:    {fmt(strict)} (informativo)")
        punct = summary.get("last_phrase_punct_rate")
        if punct is not None:
            _log(f"   Last phrase punct:      {fmt(punct)}")
        _log(f"   Min moments/video:      {summary.get('min_moments_per_video')}")
        if with_copy:
            _log(f"   Copy clean rate:        {fmt(summary.get('copy_clean_rate'))}")
            if summary.get("judge_llm_delta_avg") is not None:
                _log(f"   Judge vs LLM delta:     {summary['judge_llm_delta_avg']} pts")
            _log(f"   Judge response rate:    {fmt(summary.get('judge_response_rate'))}")
        _log()
        if failures:
            _log("❌ REGRESIONES DETECTADAS:")
            for f in failures:
                _log(f"   - {f}")
        else:
            _log("✅ Todos los umbrales OK")

    return 1 if failures else 0


def _emit_json(summary: dict, stream=None) -> None:
    # Escritura directa al stream: main.py parchea print() hacia logging (con
    # timestamp), y el JSON tiene que salir limpio por stdout.
    stream = stream or sys.stdout
    stream.write(json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n")
    stream.flush()


def _main_e2e(videos, thresholds, *, json_mode, budget_sec, aggregate, check) -> int:
    from datetime import datetime, timezone

    from config.model_tiers import resolved_models
    from services.analysis_cache import PROMPT_VERSION

    _log(f"🏆 Golden set | tier=e2e | videos={len(videos)} | dry-run (sin persistir ni subir)", json_mode=json_mode)
    _log(f"   Modelos: {resolved_models()} | PROMPT_VERSION={PROMPT_VERSION}", json_mode=json_mode)
    _log("", json_mode=json_mode)

    # En modo JSON, stdout del proceso pasa a ser stderr mientras corre el
    # pipeline: yt-dlp (en proceso) y main.py escriben ahí sin pasar por
    # logging. El JSON final sale por el stdout real.
    real_stdout = sys.stdout
    if json_mode:
        sys.stdout = sys.stderr

    t0 = time.time()
    results = []
    try:
        for video in videos:
            _log(f"── {video['id']} ─────────────────────────────", json_mode=json_mode)
            try:
                results.append(evaluate_video_e2e(video, json_mode=json_mode, budget_sec=budget_sec))
            except Exception as e:
                _log(f"   ❌ Eval crash: {str(e)[:200]}", json_mode=json_mode)
                results.append({"id": video["id"], "ok": False, "clips": [], "errors": [f"crash: {str(e)[:150]}"]})
            _log("", json_mode=json_mode)
    finally:
        sys.stdout = real_stdout

    summary = aggregate(results)
    summary["tier"] = "e2e"
    summary["models"] = resolved_models()
    summary["prompt_version"] = PROMPT_VERSION
    summary["run_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    summary["git_commit"] = _git_commit()
    summary["wall_seconds"] = round(time.time() - t0, 1)
    failures = check(summary, thresholds)
    summary["failures"] = failures
    summary["passed"] = not failures

    if json_mode:
        _emit_json(summary, real_stdout)
    # El resumen legible va a stderr en modo JSON (queda en el .log)
    _print_e2e_summary(summary, failures, json_mode=json_mode)
    return 1 if failures else 0


def _git_commit() -> str | None:
    try:
        import subprocess
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=WORKER_DIR, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


if __name__ == "__main__":
    sys.exit(main())
