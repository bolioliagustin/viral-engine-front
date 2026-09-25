"""Experimento mínimo del estudio de factibilidad de un evaluador aprendido.

`docs/ESTUDIO_ML_EVALUADOR.md` §7. Pregunta: con los datos que ya existen,
¿las señales que hoy tiene el pipeline (nota del Juez, `rank_score` de la
Pasada A, posición, duración, densidad) predicen lo que Agustín considera
"posteable" o "de lo mejor del episodio"? ¿Y alcanza la muestra para
entrenar algo?

Costo: US$0. No llama a ninguna API paga. Lee en **solo lectura**:
- `eval/runs/2026-09-25-seleccion-baseline-validado.json` (candidatos de la
  Pasada A, 7 videos × 3 repeticiones, con `rank_score`);
- `eval/referencias/*.json` (Referencias validadas de B60 y borradores);
- transcripts locales del eval (`downloads/eval_transcripts/`, opcional);
- Supabase de la beta, solo SELECT (`clip_feedback`, `content_results`,
  `analysis_cache.candidates_all`). Con `--sin-supabase` se saltean E1 y E3.

Experimentos:
- E1 · Posteable (22 clips etiquetados): AUC por señal, IC 95 % bootstrap.
- E2 · Referencias validadas de B60: ¿`rank_score` y compañía separan los
  candidatos que tocan una Referencia validada de los que no? Bootstrap por
  grupo de candidatos que se solapan entre repeticiones (sin fuga).
- E2b · Anexo A.2 de PLAN_MEJORA: Juez y Jev sobre 10 Referencias, contra la
  validación de Agustín (8 conservadas, 2 descartadas).
- E3 · Juez contra borradores de Referencias (Sonnet 5, sin validar) en 4
  videos: AUC por video y regresión logística leave-one-video-out.
- E4 · Tamaño de muestra: cuántas etiquetas hacen falta para cada pregunta.

Uso (desde worker/):
    python eval/experimentos/ml_factibilidad.py --json \
        > eval/runs/2026-09-25-ml-factibilidad.json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import numpy as np

WORKER_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKER_DIR))

from eval.eval_metrics import cobertura_nucleo  # noqa: E402

EVAL_DIR = WORKER_DIR / "eval"
REF_DIR = EVAL_DIR / "referencias"
TRANSCRIPTS_DIR = WORKER_DIR / "downloads" / "eval_transcripts"
SELECCION_RUN = EVAL_DIR / "runs" / "2026-09-25-seleccion-baseline-validado.json"

RNG = np.random.default_rng(20260925)
N_BOOT = 4000
TOCA_REFERENCIA = 0.5  # cobertura del núcleo para decir "el candidato toca la Referencia"


# ─── Estadística mínima (sin sklearn/scipy) ──────────────────────────────────

def auc(scores, labels) -> float | None:
    """AUC de Mann-Whitney con empates a 0,5. None si falta una clase."""
    s = np.asarray(scores, dtype=float)
    y = np.asarray(labels, dtype=bool)
    pos, neg = s[y], s[~y]
    if len(pos) == 0 or len(neg) == 0:
        return None
    diff = pos[:, None] - neg[None, :]
    return float(((diff > 0).sum() + 0.5 * (diff == 0).sum()) / diff.size)


def boot_auc(scores, labels, groups=None, n=N_BOOT) -> dict:
    """AUC con IC 95 % por bootstrap. Con `groups`, se remuestrean grupos
    enteros (candidatos casi idénticos entre repeticiones cuentan una vez)."""
    s = np.asarray(scores, dtype=float)
    y = np.asarray(labels, dtype=bool)
    point = auc(s, y)
    if point is None:
        return {"auc": None, "ic95": None, "n": int(len(s)), "n_pos": int(y.sum())}
    if groups is None:
        groups = np.arange(len(s))
    groups = np.asarray(groups)
    uniq = np.unique(groups)
    idx_by_g = {g: np.where(groups == g)[0] for g in uniq}
    vals = []
    for _ in range(n):
        pick = RNG.choice(uniq, size=len(uniq), replace=True)
        idx = np.concatenate([idx_by_g[g] for g in pick])
        a = auc(s[idx], y[idx])
        if a is not None:
            vals.append(a)
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return {
        "auc": round(point, 3),
        "ic95": [round(float(lo), 3), round(float(hi), 3)],
        "n": int(len(s)),
        "n_pos": int(y.sum()),
        "n_grupos": int(len(uniq)),
    }


def logistic_fit(X, y, l2=1.0, iters=500) -> np.ndarray:
    """Regresión logística con L2 por Newton (X ya estandarizada, con sesgo)."""
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    w = np.zeros(X.shape[1])
    reg = np.eye(X.shape[1]) * l2
    reg[0, 0] = 0.0  # el sesgo no se regulariza
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ w))
        grad = X.T @ (p - y) + reg @ w
        H = X.T @ (X * (p * (1 - p))[:, None]) + reg
        step = np.linalg.solve(H, grad)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return w


def wilson(k: int, n: int) -> list[float] | None:
    if n == 0:
        return None
    p = k / n
    z = 1.96
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(c - h, 3), round(c + h, 3)]


# ─── Datos ───────────────────────────────────────────────────────────────────

def cargar_referencias(youtube_id: str) -> dict | None:
    p = REF_DIR / f"{youtube_id}.json"
    return json.loads(p.read_text()) if p.exists() else None


def cargar_lineas(youtube_id: str) -> list[dict] | None:
    p = TRANSCRIPTS_DIR / f"{youtube_id}.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    t = d.get("transcript", d)
    return t.get("lines")


def rasgos_de_texto(lines: list[dict] | None, ini: float, fin: float) -> dict:
    """Rasgos baratos del texto del candidato, sin modelos."""
    if not lines:
        return {}
    sel = [l for l in lines if float(l["end"]) > ini and float(l["start"]) < fin]
    txt = " ".join((l.get("text") or "") for l in sel)
    dur = max(1.0, fin - ini)
    palabras = re.findall(r"\w+", txt)
    return {
        "palabras_por_seg": len(palabras) / dur,
        "preguntas_por_min": txt.count("?") / dur * 60,
        "exclamaciones_por_min": txt.count("!") / dur * 60,
        "lineas_por_min": len(sel) / dur * 60,
    }


def _grupos_por_solape(cands: list[dict]) -> list[int]:
    """Une candidatos (de distintas repeticiones) que se solapan > 50 % del más
    corto: son el mismo momento propuesto varias veces."""
    parent = list(range(len(cands)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(cands)):
        for j in range(i + 1, len(cands)):
            a, b = cands[i], cands[j]
            inter = max(0.0, min(a["end_time"], b["end_time"]) - max(a["start_time"], b["start_time"]))
            corto = min(a["end_time"] - a["start_time"], b["end_time"] - b["start_time"])
            if corto > 0 and inter / corto > 0.5:
                parent[find(i)] = find(j)
    return [find(i) for i in range(len(cands))]


def etiquetar_candidato(c: dict, validadas: list[dict], pendientes: list[dict], descartadas: list[dict]) -> str:
    """'mejor' si toca una Referencia validada; 'descartada' si toca solo una
    que Agustín descartó; 'incierto' si toca solo un borrador sin validar;
    'nada' si no toca ninguna."""
    toca = lambda refs: any(cobertura_nucleo(c, r) >= TOCA_REFERENCIA for r in refs)  # noqa: E731
    if toca(validadas):
        return "mejor"
    if toca(pendientes):
        return "incierto"
    if toca(descartadas):
        return "descartada"
    return "nada"


# ─── E2 · B60 contra Referencias validadas ───────────────────────────────────

def experimento_b60(seleccion: dict) -> dict:
    video = next(v for v in seleccion["videos"] if v["youtube_id"] == "B60BHDNFNxM")
    doc = cargar_referencias("B60BHDNFNxM")
    validadas = [m for m in doc["momentos"] if m.get("validado_por")]
    pendientes = [m for m in doc["momentos"] if not m.get("validado_por")]
    descartadas = doc.get("descartados") or []
    lineas = cargar_lineas("B60BHDNFNxM")
    dur = float(video["duracion_sec"])

    cands = []
    for rep in video["reps"]:
        for c in rep.get("candidatos") or []:
            c = dict(c, rep=rep["rep"])
            c["etiqueta"] = etiquetar_candidato(c, validadas, pendientes, descartadas)
            c["pos_rel"] = c["start_time"] / dur
            c["duracion"] = c["end_time"] - c["start_time"]
            c.update(rasgos_de_texto(lineas, c["start_time"], c["end_time"]))
            cands.append(c)
    grupos = _grupos_por_solape(cands)
    for c, g in zip(cands, grupos):
        c["grupo"] = g

    conteo = {}
    for c in cands:
        conteo[c["etiqueta"]] = conteo.get(c["etiqueta"], 0) + 1

    # Evaluación: 'mejor' contra 'nada' + 'descartada'; 'incierto' queda afuera.
    ev = [c for c in cands if c["etiqueta"] != "incierto"]
    y = [c["etiqueta"] == "mejor" for c in ev]
    g = [c["grupo"] for c in ev]
    rasgos = ["rank_score", "pos_rel", "duracion", "palabras_por_seg",
              "preguntas_por_min", "exclamaciones_por_min", "lineas_por_min"]
    por_rasgo = {}
    for r in rasgos:
        if all(r in c for c in ev):
            por_rasgo[r] = boot_auc([c[r] for c in ev], y, g)

    # Control de confusión: un candidato más largo "toca" una Referencia más
    # fácil. ¿rank_score predice algo más allá de la duración? Residuo de una
    # recta rank_score ~ duración, y AUC dentro de terciles de duración.
    rs = np.array([c["rank_score"] for c in ev], dtype=float)
    du = np.array([c["duracion"] for c in ev], dtype=float)
    yy = np.array(y, dtype=bool)
    b = np.polyfit(du, rs, 1)
    residuo = rs - np.polyval(b, du)
    cortes = np.percentile(du, [33.3, 66.7])
    tercil = np.digitize(du, cortes)
    auc_terciles = {}
    for t in range(3):
        m = tercil == t
        a = auc(rs[m], yy[m])
        auc_terciles[f"tercil_{t + 1}"] = {
            "n": int(m.sum()), "n_pos": int(yy[m].sum()),
            "auc_rank_score": round(a, 3) if a is not None else None,
        }
    confusion = {
        "corr_rank_score_duracion": round(float(np.corrcoef(rs, du)[0, 1]), 3),
        "auc_rank_score_residuo_de_duracion": boot_auc(residuo, y, g),
        "auc_rank_score_por_tercil_de_duracion": auc_terciles,
    }

    # Etiqueta estricta: el candidato CONTIENE el núcleo de una validada A (±2 s).
    from eval.eval_metrics import contiene_nucleo
    validadas_a = [m for m in validadas if m.get("calidad") == "A"]
    y_estricta = [any(contiene_nucleo(c, r) for r in validadas_a) for c in ev]
    estricta = {
        "n_pos": int(sum(y_estricta)),
        "auc_rank_score": boot_auc(rs, y_estricta, g),
        "auc_duracion": boot_auc(du, y_estricta, g),
    }

    # Precisión del top-k por rank_score, por repetición, contra validadas.
    prec = []
    for rep in video["reps"]:
        cs = sorted(rep.get("candidatos") or [], key=lambda c: -c["rank_score"])
        for k in (5, 10):
            top = cs[:k]
            ok = sum(1 for c in top if etiquetar_candidato(c, validadas, pendientes, descartadas) == "mejor")
            prec.append({"rep": rep["rep"], "k": k, "mejor": ok})
    # Tasa base: fracción de candidatos 'mejor' entre los evaluables.
    return {
        "n_candidatos": len(cands),
        "n_grupos_unicos": len(set(grupos)),
        "etiquetas": conteo,
        "tasa_base_mejor": round(sum(y) / len(y), 3) if y else None,
        "auc_por_rasgo": por_rasgo,
        "confusion_con_duracion": confusion,
        "etiqueta_estricta_contiene_nucleo_A": estricta,
        "precision_top_k_rank_score": prec,
        "referencias": {
            "validadas": len(validadas),
            "validadas_a": sum(1 for m in validadas if m.get("calidad") == "A"),
            "pendientes": len(pendientes),
            "descartadas": len(descartadas),
        },
    }


# ─── E2b · Anexo A.2: Juez y Jev contra la validación de B60 ─────────────────

# Tabla A.2 de docs/PLAN_MEJORA.md (Juez y Jev sobre el texto de las Líneas de
# 10 Referencias de la semilla) con el destino de cada una en la validación de
# Agustín del 25-sep (worker/eval/referencias/B60BHDNFNxM.json).
ANEXO_A2 = [
    # (candidato, Referencia, juez, jev)
    ("Cuti/Haaland", "R06", 20, 21.8),
    ("Peñarol", "R02", 16, 19.1),
    ("Bilardistas", "R09", 15, 18.6),
    ("Periodista", "R04", 19, 16.7),
    ("Jagger completa", "R08", 18, 14.8),
    ("Caniggia", "R01", 18, 8.8),
    ("Oso Yogi completo", "R03", 13, 8.2),
    ("Ledley King", "R10", 13, 10.5),
    ("Asociación ilícita", "R15", 9, 11.4),
    ("Luis Enrique", "R13", 10, 12.3),
]


def experimento_anexo_a2() -> dict:
    doc = cargar_referencias("B60BHDNFNxM")
    validadas = {m["id"] for m in doc["momentos"] if m.get("validado_por")}
    filas = []
    for nombre, ref, juez, jev in ANEXO_A2:
        filas.append({"candidato": nombre, "ref": ref, "juez": juez, "jev": jev,
                      "conservada": ref in validadas})
    y = [f["conservada"] for f in filas]
    return {
        "n": len(filas),
        "conservadas": sum(y),
        "descartadas": len(y) - sum(y),
        "auc_juez": round(auc([f["juez"] for f in filas], y), 3),
        "auc_jev": round(auc([f["jev"] for f in filas], y), 3),
        "pares_comparables": sum(y) * (len(y) - sum(y)),
        "filas": filas,
    }


# ─── Supabase (solo lectura) ─────────────────────────────────────────────────

def _supabase():
    from dotenv import load_dotenv
    load_dotenv(WORKER_DIR.parent / ".env")
    import contextlib
    from services.supabase_client import get_supabase
    with contextlib.redirect_stdout(sys.stderr):  # el cliente imprime al conectar
        return get_supabase()


def _jsonb(v):
    """Algunas columnas jsonb vuelven como string (se guardaron serializadas)."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def _video_id(url: str) -> str | None:
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})", url or "")
    return m.group(1) if m else None


def _duraciones_video(sb) -> dict[str, float]:
    """Duración por video desde los transcripts (jobs.video_duration viene vacío)."""
    dur = {}
    for p in TRANSCRIPTS_DIR.glob("*.json"):
        d = json.loads(p.read_text())
        t = d.get("transcript", d)
        if t.get("duration"):
            dur[p.stem] = float(t["duration"])
    rows = sb.table("transcription_cache").select("video_id,duration_seconds").execute().data or []
    for r in rows:
        vid = r["video_id"].split(":")[0]
        if r.get("duration_seconds"):
            dur.setdefault(vid, float(r["duration_seconds"]))
    return dur


def experimento_posteable(sb) -> dict:
    """E1: las 22 etiquetas de clip_feedback contra las señales del clip."""
    from eval.etiquetas import fetch_etiquetas
    import contextlib
    with contextlib.redirect_stdout(sys.stderr):
        _, por_momento = fetch_etiquetas()
    jobs = {j["id"]: j for j in (sb.table("jobs").select("id,video_url").execute().data or [])}
    dur = _duraciones_video(sb)
    filas = []
    for (job_id, mi), et in por_momento.items():
        rows = (
            sb.table("content_results")
            .select("start_time,end_time,score_judge,score_llm,words_per_sec,clip_quality_issues")
            .eq("job_id", job_id).eq("moment_index", mi).limit(1).execute().data
        )
        if not rows:
            continue
        r = rows[0]
        vid = _video_id(jobs.get(job_id, {}).get("video_url"))
        sj, sl, flags = (_jsonb(r.get(k)) for k in ("score_judge", "score_llm", "clip_quality_issues"))
        sj, sl, flags = sj or {}, sl or {}, flags or []
        fila = {
            "job": job_id[:8],
            "video": vid,
            "moment_index": mi,
            "posteable": et.posteable,
            "motivo": et.motivo,
            "juez_suma": sum(float(sj.get(k, 0)) for k in ("hook", "retention", "shareability")) if sj else None,
            "juez_hook": sj.get("hook"),
            "juez_retention": sj.get("retention"),
            "juez_shareability": sj.get("shareability"),
            "pasada_a_suma": sum(float(sl.get(k, 0)) for k in ("hook", "retention", "shareability")) if sl else None,
            "duracion": (r["end_time"] or 0) - (r["start_time"] or 0),
            "pos_rel": (r["start_time"] or 0) / dur[vid] if vid in dur else None,
            "palabras_por_seg": r.get("words_per_sec"),
            "n_flags": len(flags) if isinstance(flags, list) else None,
        }
        filas.append(fila)
    y = [f["posteable"] for f in filas]
    rasgos = ["juez_suma", "juez_hook", "juez_retention", "juez_shareability", "pasada_a_suma",
              "duracion", "pos_rel", "palabras_por_seg", "n_flags"]
    por_rasgo = {}
    for r in rasgos:
        sub = [(f[r], f["posteable"]) for f in filas if f[r] is not None]
        if sub:
            por_rasgo[r] = boot_auc([s for s, _ in sub], [p for _, p in sub])
    por_video = {}
    for f in filas:
        v = por_video.setdefault(f["video"], {"n": 0, "posteables": 0})
        v["n"] += 1
        v["posteables"] += int(f["posteable"])
    return {
        "n": len(filas),
        "posteables": sum(y),
        "posteable_rate": round(sum(y) / len(y), 3) if y else None,
        "posteable_rate_ic95": wilson(sum(y), len(y)),
        "por_video": por_video,
        "anotadores": 1,
        "auc_por_rasgo": por_rasgo,
    }


def experimento_juez_vs_borradores(sb) -> dict:
    """E3: candidatos con nota del Juez en analysis_cache (whisper_full) contra
    los borradores de Referencias (Sonnet 5, sin validar)."""
    rows = sb.table("analysis_cache").select("video_id,prompt_version,result").execute().data or []
    dur = _duraciones_video(sb)
    cands = []
    for r in rows:
        res = r["result"]
        res = json.loads(res) if isinstance(res, str) else (res or {})
        if "whisper_full" not in (r.get("prompt_version") or ""):
            continue
        vid = r["video_id"]
        doc = cargar_referencias(vid)
        if not doc or vid not in dur:
            continue
        refs = doc.get("momentos") or []
        excl = doc.get("excluir") or []
        for c in res.get("candidates_all") or []:
            js = c.get("judge_scores")
            if not isinstance(c, dict) or not js:
                continue
            c2 = {"start_time": float(c["start_time"]), "end_time": float(c["end_time"])}
            toca = any(cobertura_nucleo(c2, m) >= TOCA_REFERENCIA for m in refs)
            en_excl = any(
                max(0.0, min(c2["end_time"], e["fin"]) - max(c2["start_time"], e["inicio"]))
                > 0.5 * (c2["end_time"] - c2["start_time"])
                for e in excl
            )
            cands.append({
                "video": vid,
                "corrida": r["prompt_version"],
                "toca_referencia": toca,
                "en_exclusion": en_excl,
                "juez_suma": float(js["hook"]) + float(js["retention"]) + float(js["shareability"]),
                "w2_score": c.get("w2_score"),
                "rank_score": c.get("rank_score"),
                "pos_rel": c2["start_time"] / dur[vid],
                "duracion": c2["end_time"] - c2["start_time"],
            })
    rasgos = ["juez_suma", "w2_score", "rank_score", "pos_rel", "duracion"]
    por_video = {}
    for vid in sorted({c["video"] for c in cands}):
        sub = [c for c in cands if c["video"] == vid]
        y = [c["toca_referencia"] for c in sub]
        por_video[vid] = {
            "n": len(sub),
            "n_toca": sum(y),
            **{f"auc_{r}": auc([c[r] if c[r] is not None else 0 for c in sub], y) for r in rasgos},
        }
        por_video[vid] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in por_video[vid].items()}

    # Leave-one-video-out: logística sobre los 5 rasgos contra el mejor rasgo solo.
    videos = sorted({c["video"] for c in cands})
    oof = np.zeros(len(cands))
    X_all = np.array([[c[r] if c[r] is not None else 0.0 for r in rasgos] for c in cands], dtype=float)
    y_all = np.array([c["toca_referencia"] for c in cands], dtype=bool)
    v_all = np.array([c["video"] for c in cands])
    for vid in videos:
        tr, te = v_all != vid, v_all == vid
        mu, sd = X_all[tr].mean(0), X_all[tr].std(0) + 1e-9
        Xtr = np.hstack([np.ones((tr.sum(), 1)), (X_all[tr] - mu) / sd])
        Xte = np.hstack([np.ones((te.sum(), 1)), (X_all[te] - mu) / sd])
        w = logistic_fit(Xtr, y_all[tr], l2=5.0)
        oof[te] = Xte @ w
    auc_lovo_por_video = {vid: round(auc(oof[v_all == vid], y_all[v_all == vid]), 3) for vid in videos}
    return {
        "n": len(cands),
        "n_toca": int(y_all.sum()),
        "etiquetas": "borradores de Referencias (anthropic/claude-sonnet-5), SIN validar por Agustín",
        "auc_pooled_por_rasgo": {r: boot_auc(X_all[:, i], y_all, v_all) for i, r in enumerate(rasgos)},
        "por_video": por_video,
        "logistica_lovo": {
            "rasgos": rasgos,
            "auc_pooled_oof": boot_auc(oof, y_all, v_all),
            "auc_por_video_oof": auc_lovo_por_video,
        },
    }


# ─── E4 · Tamaño de muestra ──────────────────────────────────────────────────

def hanley_mcneil_se(a: float, n_pos: int, n_neg: int) -> float:
    q1 = a / (2 - a)
    q2 = 2 * a * a / (1 + a)
    var = (a * (1 - a) + (n_pos - 1) * (q1 - a * a) + (n_neg - 1) * (q2 - a * a)) / (n_pos * n_neg)
    return math.sqrt(var)


def n_para_auc(a1: float, a0: float = 0.5, prevalencia: float = 0.35,
               alfa_z: float = 1.96, poder_z: float = 0.8416) -> int:
    """Etiquetas totales para distinguir un AUC a1 de a0 (una sola señal)."""
    for n in range(10, 5000):
        npos = max(2, round(n * prevalencia))
        nneg = max(2, n - npos)
        se0 = hanley_mcneil_se(a0, npos, nneg)
        se1 = hanley_mcneil_se(a1, npos, nneg)
        if abs(a1 - a0) >= alfa_z * se0 + poder_z * se1:
            return n
    return -1


def n_para_delta_auc(a: float, delta: float, r: float = 0.5, prevalencia: float = 0.35) -> int:
    """Etiquetas para detectar que un rankeador nuevo mejora el AUC en `delta`
    sobre el actual, evaluados sobre los mismos clips (correlación r)."""
    for n in range(10, 20000, 5):
        npos = max(2, round(n * prevalencia))
        nneg = max(2, n - npos)
        s1 = hanley_mcneil_se(a, npos, nneg)
        s2 = hanley_mcneil_se(a + delta, npos, nneg)
        se_d = math.sqrt(s1 * s1 + s2 * s2 - 2 * r * s1 * s2)
        if delta >= (1.96 + 0.8416) * se_d:
            return n
    return -1


def n_para_proporcion(p: float, media_anchura: float) -> int:
    return math.ceil(1.96 ** 2 * p * (1 - p) / media_anchura ** 2)


def n_para_dos_proporciones(p1: float, p2: float) -> int:
    pbar = (p1 + p2) / 2
    num = (1.96 * math.sqrt(2 * pbar * (1 - pbar)) + 0.8416 * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
    return math.ceil(num / (p1 - p2) ** 2)


def tamanos_de_muestra(seleccion: dict) -> dict:
    recalls = [v["agregado"]["recall_completo"]["media"] for v in seleccion["videos"]]
    sd_videos = float(np.std(recalls, ddof=1))
    return {
        "auc_contra_azar": {f"auc_{a}": n_para_auc(a) for a in (0.6, 0.65, 0.7, 0.75, 0.8)},
        "delta_auc_mismos_clips_r05": {
            f"de_0.65_a_{0.65 + d:.2f}": n_para_delta_auc(0.65, d) for d in (0.05, 0.10, 0.15)
        },
        "ic95_auc_0.7_mitad_de_anchura": {
            str(n): round(1.96 * hanley_mcneil_se(0.7, round(n * 0.35), n - round(n * 0.35)), 3)
            for n in (22, 50, 100, 200, 400, 1000)
        },
        "precision_para_estimar_0.8_con_mas_menos": {
            str(h): n_para_proporcion(0.8, h) for h in (0.05, 0.10, 0.15)
        },
        "precision_0.65_a_0.80_por_brazo": n_para_dos_proporciones(0.65, 0.80),
        "precision_0.75_a_0.85_por_brazo": n_para_dos_proporciones(0.75, 0.85),
        "videos_para_recall": {
            "desvio_entre_videos_recall_completo": round(sd_videos, 3),
            "videos_para_ic95_mas_menos_10pp": math.ceil((1.96 * sd_videos / 0.10) ** 2),
            "videos_para_ic95_mas_menos_15pp": math.ceil((1.96 * sd_videos / 0.15) ** 2),
        },
        "eventos_por_variable": {
            "regla": "10–20 positivos por parámetro (Peduzzi 1996); Riley 2020 calcula según R² y prevalencia",
            "10_rasgos": {"positivos_min": 100, "positivos_holgado": 200},
            "embedding_768_con_regularizacion": "no aplica la regla; ver §2.3 del estudio",
        },
    }


# ─── Principal ───────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sin-supabase", action="store_true", help="saltea E1 y E3")
    ap.add_argument("--json", action="store_true", help="JSON a stdout")
    args = ap.parse_args()

    seleccion = json.loads(SELECCION_RUN.read_text())
    out = {
        "experimento": "ml_factibilidad",
        "fecha": "2026-09-25",
        "costo_api_usd": 0.0,
        "fuente_candidatos": SELECCION_RUN.name,
        "E2_b60_referencias_validadas": experimento_b60(seleccion),
        "E2b_anexo_a2_juez_jev": experimento_anexo_a2(),
        "E4_tamano_de_muestra": tamanos_de_muestra(seleccion),
    }
    if not args.sin_supabase:
        sb = _supabase()
        out["E1_posteable"] = experimento_posteable(sb)
        out["E3_juez_vs_borradores"] = experimento_juez_vs_borradores(sb)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
