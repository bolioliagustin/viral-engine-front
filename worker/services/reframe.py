"""
Reencuadre vertical por escena — W5 (docs/PLAN_CALIDAD.md §9 Fase 1, fila D;
evidencia: docs/ANALISIS_OPUS_CLIP.md §2.5, §4 punto 3).

Qué resuelve: hoy `to_vertical_9_16`/`generate_clip` siempre hacen "Fit"
(el 16:9 original flotando centrado sobre fondo desenfocado). Opus Clip, sobre
el mismo video, detecta caras/paneles de videollamada y elige entre layouts
(Fill, Fit, Split, Three, Four, Screen), expresados como una lista de
recortes en porcentajes sobre el frame original.

Alcance de esta versión (deliberadamente acotado, ver ANALISIS_OPUS_CLIP.md
§6 fila D): sin seguimiento cuadro a cuadro, sin TalkNet (hablante activo),
sin YOLOX (paneles/objetos). Se hace detección de escena (PySceneDetect) +
muestreo de caras por escena (OpenCV YuNet, liviano y CPU-only) + un layout
FIJO por escena dominante — nada de recortes que cambien dentro del clip.

Módulo aislado: no importa nada de `services.*` (clip_generator, processor,
etc.). Las funciones que deciden (`choose_layout`, el agrupado de caras
estables) son puras — reciben datos ya calculados, no archivos — para poder
testearlas sin video real ni cv2 real de por medio en la mayoría de los
casos.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2

# ── Modelo de caras (YuNet, OpenCV Zoo) ───────────────────────────────────
# Fuente: https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet
# Archivo: face_detection_yunet_2023mar.onnx (variante de input shape fijo;
# ver worker/models/README.md sobre por qué esta y no la de shape dinámico).
# Licencia: Apache-2.0 (repo opencv_zoo) — ver worker/models/LICENSE.
MODEL_PATH = Path(__file__).parent.parent / "models" / "face_detection_yunet_2023mar.onnx"

# ── Constantes de heurística ───────────────────────────────────────────────
DEFAULT_SCENE_THRESHOLD = 27.0       # PySceneDetect ContentDetector default
DEFAULT_SAMPLES_PER_SCENE = 5        # medido en la validación (WORKER.md)
FACE_GROUP_DIST_PCT = 0.15           # mismos centros -> misma cara si <15% del ancho
FACE_STABLE_MIN_SAMPLES = 3          # de 5 muestras (mayoría) para contar como estable
PANEL_BORDER_TOLERANCE_PCT = 0.10    # la costura de panel se busca a ±10% del centro
FACE_SCORE_THRESHOLD = 0.7
FACE_NMS_THRESHOLD = 0.3
FACE_TOP_K = 10

# "fill": qué tan cerrado es el recorte alrededor de la cara (1.0 = usa toda
# la altura del frame original, sin zoom). 0.72 da un plano tipo "medio
# primer plano" — más cerrado que la altura completa, pero sin ser un
# recorte solo-cara. No hay detección de hombros/torso (fuera de alcance),
# así que es un valor fijo, no calculado por escena.
FILL_CROP_HEIGHT_PCT = 0.72
FILL_FACE_TARGET_Y_PCT = 0.38        # la cara queda al ~38% de la altura del recorte

# "split": ancho del recorte de cada cara, como múltiplo de su propio ancho
# detectado, acotado a lo que Opus midió en el video de referencia (~45-48%
# del ancho del frame original) — ver ANALISIS_OPUS_CLIP.md §2.5.
SPLIT_CROP_WIDTH_MIN_PCT = 0.40
SPLIT_CROP_WIDTH_MAX_PCT = 0.55
SPLIT_CROP_WIDTH_FACE_MULTIPLIER = 2.6


# ── Modelos de datos ────────────────────────────────────────────────────────

@dataclass
class Face:
    """Una detección de cara en un frame, en porcentajes del frame [0, 1]."""
    x_pct: float
    y_pct: float
    w_pct: float
    h_pct: float
    confidence: float

    @property
    def center_x_pct(self) -> float:
        return self.x_pct + self.w_pct / 2

    @property
    def center_y_pct(self) -> float:
        return self.y_pct + self.h_pct / 2

    @property
    def area_pct(self) -> float:
        return self.w_pct * self.h_pct


@dataclass
class StableFace:
    """Cara que aparece en >= FACE_STABLE_MIN_SAMPLES de las muestras de una escena."""
    center_x_pct: float
    center_y_pct: float
    w_pct: float
    h_pct: float
    samples_seen: int
    total_samples: int


@dataclass
class SceneAnalysis:
    """Resultado de analizar una escena: caras estables + heurística de panel."""
    start_sec: float
    end_sec: float
    stable_faces: list[StableFace] = field(default_factory=list)
    has_panel_split: bool = False
    samples_taken: int = 0


@dataclass
class CropArea:
    """
    Un recorte del frame ORIGINAL -> una región de destino en el frame final,
    todo en porcentajes [0, 1]. FFmpeg lo traduce a `crop=iw*w:ih*h:iw*x:ih*y`
    (relativo al frame de entrada, sin necesitar sus dimensiones en pixeles).
    """
    src_x_pct: float
    src_y_pct: float
    src_w_pct: float
    src_h_pct: float
    dst_x_pct: float
    dst_y_pct: float
    dst_w_pct: float
    dst_h_pct: float


@dataclass
class LayoutPlan:
    """
    `name`: "split" | "fill" | "fit". `crops`: lista de CropArea (vacía para
    "fit", que reusa el filtro de fondo desenfocado existente en
    clip_generator.py sin necesidad de datos de recorte).
    """
    name: str
    crops: list[CropArea] = field(default_factory=list)


# ── Detección de escenas (PySceneDetect) ────────────────────────────────────

def detect_scenes(video_path: str, threshold: float = DEFAULT_SCENE_THRESHOLD) -> list[tuple[float, float]]:
    """
    Cortes de cámara dentro de `video_path` (típicamente ya el segmento del
    clip, no el video fuente completo — ver generate_clip/W5 en WORKER.md).

    Si PySceneDetect no está instalado o falla (video corrupto, formato raro),
    devuelve una sola escena que cubre todo el archivo, igual que si no
    hubiera cortes de cámara.
    """
    try:
        from scenedetect import open_video, SceneManager
        from scenedetect.detectors import ContentDetector
    except Exception:
        return _single_scene_fallback(video_path)

    try:
        video = open_video(video_path)
        duration_sec = video.duration.get_seconds()
        scene_manager = SceneManager()
        scene_manager.add_detector(ContentDetector(threshold=threshold))
        scene_manager.detect_scenes(video, show_progress=False)
        scene_list = scene_manager.get_scene_list()
        if not scene_list:
            return [(0.0, duration_sec)]
        return [(s.get_seconds(), e.get_seconds()) for s, e in scene_list]
    except Exception:
        return _single_scene_fallback(video_path)


def _single_scene_fallback(video_path: str) -> list[tuple[float, float]]:
    cap = cv2.VideoCapture(video_path)
    try:
        if not cap.isOpened():
            return [(0.0, 0.0)]
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
        duration = (frame_count / fps) if fps > 0 else 0.0
    finally:
        cap.release()
    return [(0.0, duration)]


# ── Detección de caras (OpenCV YuNet) ───────────────────────────────────────

_detector_cache: dict[tuple[int, int], "cv2.FaceDetectorYN"] = {}


def _get_face_detector(width: int, height: int):
    key = (width, height)
    detector = _detector_cache.get(key)
    if detector is None:
        detector = cv2.FaceDetectorYN.create(
            str(MODEL_PATH), "", (width, height),
            score_threshold=FACE_SCORE_THRESHOLD,
            nms_threshold=FACE_NMS_THRESHOLD,
            top_k=FACE_TOP_K,
        )
        _detector_cache[key] = detector
    return detector


def detect_faces(frame) -> list[Face]:
    """
    Caras en un frame BGR (el que devuelve `cv2.VideoCapture.read()`).

    Puro respecto del modelo: no toca disco ni el resto del pipeline más
    allá de leer el .onnx versionado en worker/models/. Si el modelo no
    está disponible (no debería pasar en el contenedor, pero sí en un venv
    sin descargar el archivo), devuelve lista vacía en vez de crashear todo
    el render — reencuadre cae a "fit" igual que si no hubiera caras.
    """
    if frame is None or getattr(frame, "size", 0) == 0:
        return []
    h, w = frame.shape[:2]
    if w <= 0 or h <= 0:
        return []
    try:
        detector = _get_face_detector(w, h)
        _, faces = detector.detect(frame)
    except Exception:
        return []
    if faces is None:
        return []

    out: list[Face] = []
    for f in faces:
        x, y, fw, fh = float(f[0]), float(f[1]), float(f[2]), float(f[3])
        conf = float(f[-1])
        x = max(0.0, x)
        y = max(0.0, y)
        fw = max(0.0, min(fw, w - x))
        fh = max(0.0, min(fh, h - y))
        if fw <= 0 or fh <= 0:
            continue
        out.append(Face(x_pct=x / w, y_pct=y / h, w_pct=fw / w, h_pct=fh / h, confidence=conf))
    return out


# ── Análisis de escena: agrupar caras + heurística de panel ────────────────

def _group_stable_faces(all_faces: list[list[Face]], total_samples: int) -> list[StableFace]:
    """
    Agrupa detecciones de todas las muestras por posición: misma cara si el
    centro está a < FACE_GROUP_DIST_PCT del ancho/alto del centro promedio
    del grupo. Un grupo cuenta como "estable" si aparece en >= 3 de 5
    (FACE_STABLE_MIN_SAMPLES) muestras.
    """
    groups: list[list[Face]] = []
    for faces_in_sample in all_faces:
        for f in faces_in_sample:
            match = None
            for g in groups:
                n = len(g)
                gx = sum(gf.center_x_pct for gf in g) / n
                gy = sum(gf.center_y_pct for gf in g) / n
                if abs(f.center_x_pct - gx) < FACE_GROUP_DIST_PCT and abs(f.center_y_pct - gy) < FACE_GROUP_DIST_PCT:
                    match = g
                    break
            if match is None:
                groups.append([f])
            else:
                match.append(f)

    stable: list[StableFace] = []
    for g in groups:
        seen = len(g)
        if seen < FACE_STABLE_MIN_SAMPLES:
            continue
        stable.append(StableFace(
            center_x_pct=sum(f.center_x_pct for f in g) / seen,
            center_y_pct=sum(f.center_y_pct for f in g) / seen,
            w_pct=sum(f.w_pct for f in g) / seen,
            h_pct=sum(f.h_pct for f in g) / seen,
            samples_seen=seen,
            total_samples=total_samples,
        ))
    return stable


def _frame_has_panel_seam(frame) -> bool:
    """
    Heurística de panel de videollamada: dos mitades de brillo/color
    distinto separadas por una línea vertical de alto contraste, dentro de
    ±PANEL_BORDER_TOLERANCE_PCT del centro del frame.
    """
    if frame is None:
        return False
    h, w = frame.shape[:2]
    if w < 20:
        return False
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype("float32")
    col_means = gray.mean(axis=0)

    center = w // 2
    band = max(1, int(w * PANEL_BORDER_TOLERANCE_PCT))
    lo, hi = max(1, center - band), min(w - 1, center + band)
    if hi <= lo:
        return False

    central_diffs = [abs(float(col_means[i]) - float(col_means[i - 1])) for i in range(lo, hi)]
    seam_strength = max(central_diffs) if central_diffs else 0.0

    all_diffs = sorted(abs(float(col_means[i]) - float(col_means[i - 1])) for i in range(1, w))
    background = all_diffs[len(all_diffs) // 2] if all_diffs else 0.0

    left_mean = float(gray[:, :center].mean())
    right_mean = float(gray[:, center:].mean())
    halves_differ = abs(left_mean - right_mean) > 4.0

    return seam_strength > 20.0 and seam_strength > background * 3.0 and halves_differ


def _detect_panel_split(frames: list) -> bool:
    """Mayoría de las muestras con costura de panel -> escena de videollamada."""
    if not frames:
        return False
    votes = sum(1 for fr in frames if _frame_has_panel_seam(fr))
    return votes >= (len(frames) // 2 + 1)


def analyze_scene(video_path: str, start: float, end: float, samples: int = DEFAULT_SAMPLES_PER_SCENE) -> SceneAnalysis:
    """
    Muestrea `samples` frames repartidos uniformemente en [start, end],
    detecta caras en cada uno, agrupa por posición y devuelve las caras
    estables + si hay panel de videollamada.
    """
    duration = max(end - start, 0.0)
    if samples <= 0 or duration <= 0:
        return SceneAnalysis(start_sec=start, end_sec=end, stable_faces=[], samples_taken=0)

    cap = cv2.VideoCapture(video_path)
    try:
        if not cap.isOpened():
            return SceneAnalysis(start_sec=start, end_sec=end, stable_faces=[], samples_taken=0)

        if samples == 1:
            timestamps = [start + duration / 2]
        else:
            step = duration / samples
            timestamps = [start + step * (i + 0.5) for i in range(samples)]

        all_faces: list[list[Face]] = []
        frames_read: list = []
        for ts in timestamps:
            cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            all_faces.append(detect_faces(frame))
            frames_read.append(frame)

        stable = _group_stable_faces(all_faces, samples)
        has_panel = _detect_panel_split(frames_read)
        return SceneAnalysis(
            start_sec=start, end_sec=end, stable_faces=stable,
            has_panel_split=has_panel, samples_taken=len(frames_read),
        )
    finally:
        cap.release()


# ── Elección de layout (pura: recibe SceneAnalysis, no toca video) ─────────

def _faces_in_distinct_halves(faces: list[StableFace]) -> bool:
    if len(faces) != 2:
        return False
    a, b = faces
    return (a.center_x_pct < 0.5) != (b.center_x_pct < 0.5)


def _split_layout(faces: list[StableFace], target_w: int, target_h: int) -> LayoutPlan:
    # Cara de la izquierda arriba, cara de la derecha abajo — el orden que
    # Opus usa en el video de referencia (ANALISIS_OPUS_CLIP.md §2.5).
    ordered = sorted(faces, key=lambda f: f.center_x_pct)
    crops = []
    for i, face in enumerate(ordered):
        crop_w_pct = max(SPLIT_CROP_WIDTH_MIN_PCT, min(SPLIT_CROP_WIDTH_MAX_PCT, face.w_pct * SPLIT_CROP_WIDTH_FACE_MULTIPLIER))
        crop_x_pct = min(max(face.center_x_pct - crop_w_pct / 2, 0.0), 1.0 - crop_w_pct)
        crops.append(CropArea(
            src_x_pct=crop_x_pct, src_y_pct=0.0,
            src_w_pct=crop_w_pct, src_h_pct=1.0,
            dst_x_pct=0.0, dst_y_pct=0.0 if i == 0 else 0.5,
            dst_w_pct=1.0, dst_h_pct=0.5,
        ))
    return LayoutPlan(name="split", crops=crops)


def _fill_layout(face: StableFace, target_w: int, target_h: int) -> LayoutPlan:
    target_ar = target_w / target_h  # 9:16 -> 0.5625
    source_ar = 16.0 / 9.0            # el video fuente siempre es 16:9 en este pipeline

    crop_h_pct = FILL_CROP_HEIGHT_PCT
    crop_w_pct = crop_h_pct * target_ar / source_ar

    crop_x_pct = min(max(face.center_x_pct - crop_w_pct / 2, 0.0), 1.0 - crop_w_pct)
    crop_y_pct = face.center_y_pct - FILL_FACE_TARGET_Y_PCT * crop_h_pct
    crop_y_pct = min(max(crop_y_pct, 0.0), 1.0 - crop_h_pct)

    return LayoutPlan(name="fill", crops=[CropArea(
        src_x_pct=crop_x_pct, src_y_pct=crop_y_pct,
        src_w_pct=crop_w_pct, src_h_pct=crop_h_pct,
        dst_x_pct=0.0, dst_y_pct=0.0, dst_w_pct=1.0, dst_h_pct=1.0,
    )])


def _fit_layout() -> LayoutPlan:
    # Sin crops: clip_generator.py interpreta name == "fit" (o layout None)
    # como "usar el filtro de fondo desenfocado existente, sin cambios".
    return LayoutPlan(name="fit", crops=[])


def choose_layout(analysis: SceneAnalysis, target_w: int = 720, target_h: int = 1280) -> LayoutPlan:
    """
    split: 2 caras estables en mitades horizontales distintas, o panel de
           videollamada detectado (aunque las caras no se hayan agrupado en
           mitades limpias, p.ej. un panel con las dos cámaras desplazadas).
    fill:  1 cara estable -> recorte 9:16 centrado en ella.
    fit:   0 caras, 3+ caras, o 2 caras que NO están en mitades distintas ni
           hay panel -> fondo desenfocado (comportamiento actual).
    """
    faces = analysis.stable_faces
    n = len(faces)

    if n == 2 and (_faces_in_distinct_halves(faces) or analysis.has_panel_split):
        return _split_layout(faces, target_w, target_h)

    if n == 1:
        return _fill_layout(faces[0], target_w, target_h)

    return _fit_layout()


# ── Orquestación por clip completo ──────────────────────────────────────────

def plan_reframe_for_clip(
    video_path: str,
    start_sec: float,
    end_sec: float,
    samples_per_scene: int = DEFAULT_SAMPLES_PER_SCENE,
    scene_threshold: float = DEFAULT_SCENE_THRESHOLD,
    target_w: int = 720,
    target_h: int = 1280,
) -> LayoutPlan:
    """
    Punto de entrada único para generate_clip (REFRAME_MODE=auto): detecta
    escenas en `video_path`, analiza la MÁS LARGA dentro de [start_sec,
    end_sec] (alcance acotado V1 — un solo layout por clip, no uno por
    escena, porque eso requeriría cortar el render en vez de solo el
    análisis) y devuelve el LayoutPlan resultante.
    """
    scenes = detect_scenes(video_path, threshold=scene_threshold)
    windowed = [(max(s, start_sec), min(e, end_sec)) for s, e in scenes]
    windowed = [(s, e) for s, e in windowed if e - s > 0.15]
    if not windowed:
        windowed = [(start_sec, end_sec)]

    dominant_start, dominant_end = max(windowed, key=lambda se: se[1] - se[0])
    analysis = analyze_scene(video_path, dominant_start, dominant_end, samples=samples_per_scene)
    return choose_layout(analysis, target_w=target_w, target_h=target_h)
