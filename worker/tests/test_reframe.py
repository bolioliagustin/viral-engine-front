"""
Tests W5 — reencuadre vertical por escena (docs/PLAN_CALIDAD.md §9 Fase 1,
fila D; evidencia docs/ANALISIS_OPUS_CLIP.md §2.5, §4 punto 3).

Cubre las partes puras (no tocan video real): agrupado de caras estables,
`choose_layout` y el filtro FFmpeg que arma clip_generator a partir de un
LayoutPlan — incluido el snapshot de que `layout=None` no cambia nada del
filtro de "fit" que ya existía antes de W5.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from services.reframe import (
    Face,
    StableFace,
    SceneAnalysis,
    LayoutPlan,
    CropArea,
    choose_layout,
    _group_stable_faces,
    _single_scene_fallback,
    FILL_FACE_TARGET_Y_PCT,
)
from services.clip_generator import _build_reframe_filter


def _stable(cx: float, cy: float, w: float = 0.15, h: float = 0.25, seen: int = 5, total: int = 5) -> StableFace:
    return StableFace(center_x_pct=cx, center_y_pct=cy, w_pct=w, h_pct=h, samples_seen=seen, total_samples=total)


# ── choose_layout ────────────────────────────────────────────────────────

class TestChooseLayout:
    def test_dos_caras_en_mitades_distintas_da_split(self):
        analysis = SceneAnalysis(start_sec=0.0, end_sec=10.0, stable_faces=[_stable(0.25, 0.4), _stable(0.75, 0.4)])
        layout = choose_layout(analysis)
        assert layout.name == "split"
        assert len(layout.crops) == 2

    def test_dos_caras_mismo_lado_con_panel_detectado_da_split(self):
        # las dos caras quedaron del mismo lado (ej. cámara desplazada) pero
        # la heurística de panel de videollamada sí las marcó como panel.
        analysis = SceneAnalysis(
            start_sec=0.0, end_sec=10.0,
            stable_faces=[_stable(0.3, 0.4), _stable(0.45, 0.4)],
            has_panel_split=True,
        )
        layout = choose_layout(analysis)
        assert layout.name == "split"

    def test_dos_caras_mismo_lado_sin_panel_da_fit(self):
        analysis = SceneAnalysis(
            start_sec=0.0, end_sec=10.0,
            stable_faces=[_stable(0.3, 0.4), _stable(0.4, 0.4)],
            has_panel_split=False,
        )
        layout = choose_layout(analysis)
        assert layout.name == "fit"

    def test_una_cara_da_fill_con_cara_al_38_por_ciento_de_altura(self):
        face = _stable(0.5, 0.45)
        analysis = SceneAnalysis(start_sec=0.0, end_sec=10.0, stable_faces=[face])
        layout = choose_layout(analysis, target_w=720, target_h=1280)
        assert layout.name == "fill"
        assert len(layout.crops) == 1
        crop = layout.crops[0]
        face_y_within_crop = (face.center_y_pct - crop.src_y_pct) / crop.src_h_pct
        assert face_y_within_crop == pytest.approx(FILL_FACE_TARGET_Y_PCT, abs=0.01)

    def test_fill_crop_mantiene_aspect_ratio_9_16(self):
        analysis = SceneAnalysis(start_sec=0.0, end_sec=10.0, stable_faces=[_stable(0.5, 0.5)])
        layout = choose_layout(analysis, target_w=720, target_h=1280)
        crop = layout.crops[0]
        # el crop, en pixeles de un frame fuente 16:9, tiene que dar 9:16
        source_w, source_h = 1920.0, 1080.0
        crop_w_px = crop.src_w_pct * source_w
        crop_h_px = crop.src_h_pct * source_h
        assert crop_w_px / crop_h_px == pytest.approx(720 / 1280, abs=0.01)

    def test_sin_caras_estables_da_fit(self):
        analysis = SceneAnalysis(start_sec=0.0, end_sec=10.0, stable_faces=[])
        layout = choose_layout(analysis)
        assert layout.name == "fit"
        assert layout.crops == []

    def test_tres_caras_da_fit(self):
        analysis = SceneAnalysis(
            start_sec=0.0, end_sec=10.0,
            stable_faces=[_stable(0.2, 0.4), _stable(0.5, 0.4), _stable(0.8, 0.4)],
        )
        layout = choose_layout(analysis)
        assert layout.name == "fit"

    def test_split_ordena_izquierda_arriba_derecha_abajo(self):
        # el orden de entrada no debería importar: siempre izq->arriba
        analysis = SceneAnalysis(start_sec=0.0, end_sec=10.0, stable_faces=[_stable(0.75, 0.4), _stable(0.25, 0.4)])
        layout = choose_layout(analysis)
        assert layout.crops[0].dst_y_pct == 0.0
        assert layout.crops[1].dst_y_pct == 0.5
        assert layout.crops[0].src_x_pct < layout.crops[1].src_x_pct


# ── Agrupado de caras estables (samples < 3/5 no cuentan) ──────────────────

class TestGroupStableFaces:
    def test_cara_en_2_de_5_muestras_no_es_estable(self):
        all_faces = [
            [Face(0.40, 0.30, 0.15, 0.25, 0.9)],
            [Face(0.41, 0.31, 0.15, 0.25, 0.9)],
            [],
            [],
            [],
        ]
        stable = _group_stable_faces(all_faces, total_samples=5)
        assert stable == []

    def test_cara_en_3_de_5_muestras_es_estable(self):
        all_faces = [
            [Face(0.40, 0.30, 0.15, 0.25, 0.9)],
            [Face(0.41, 0.31, 0.15, 0.25, 0.9)],
            [Face(0.40, 0.30, 0.15, 0.25, 0.9)],
            [],
            [],
        ]
        stable = _group_stable_faces(all_faces, total_samples=5)
        assert len(stable) == 1
        assert stable[0].samples_seen == 3
        assert stable[0].total_samples == 5

    def test_dos_caras_distintas_se_agrupan_por_separado(self):
        sample = [Face(0.20, 0.30, 0.15, 0.25, 0.9), Face(0.70, 0.30, 0.15, 0.25, 0.9)]
        all_faces = [sample, sample, sample, sample, sample]
        stable = _group_stable_faces(all_faces, total_samples=5)
        assert len(stable) == 2
        centers = sorted(f.center_x_pct for f in stable)
        assert centers[0] == pytest.approx(0.275, abs=0.01)
        assert centers[1] == pytest.approx(0.775, abs=0.01)

    def test_caras_cercanas_menos_de_15_por_ciento_se_fusionan(self):
        all_faces = [
            [Face(0.40, 0.30, 0.15, 0.25, 0.9)],
            [Face(0.45, 0.30, 0.15, 0.25, 0.9)],  # a 5% de distancia, misma cara
            [Face(0.42, 0.30, 0.15, 0.25, 0.9)],
        ]
        stable = _group_stable_faces(all_faces, total_samples=3)
        assert len(stable) == 1
        assert stable[0].samples_seen == 3


# ── Fallback sin PySceneDetect / video ilegible ─────────────────────────────

class TestSingleSceneFallback:
    def test_video_inexistente_da_escena_de_duracion_cero(self):
        scenes = _single_scene_fallback("/no/existe/video_reframe_test.mp4")
        assert scenes == [(0.0, 0.0)]


# ── Filtro FFmpeg (clip_generator._build_reframe_filter) ───────────────────

class TestBuildReframeFilter:
    def test_layout_none_da_exactamente_el_filtro_de_siempre(self):
        filt = _build_reframe_filter(None, 720, 1280, 25)
        expected = (
            "[0:v]split=2[bg][fg];"
            "[bg]scale=720:1280:force_original_aspect_ratio=increase,"
            "crop=720:1280,boxblur=luma_radius=25:luma_power=1[bg2];"
            "[fg]scale=720:-2:force_original_aspect_ratio=decrease[fg2];"
            "[bg2][fg2]overlay=(W-w)/2:(H-h)/2:format=auto,format=yuv420p"
        )
        assert filt == expected

    def test_layout_fit_explicito_da_el_mismo_filtro_que_none(self):
        filt_fit = _build_reframe_filter(LayoutPlan(name="fit", crops=[]), 720, 1280, 25)
        filt_none = _build_reframe_filter(None, 720, 1280, 25)
        assert filt_fit == filt_none

    def test_split_filtro_tiene_crop_scale_y_vstack(self):
        layout = LayoutPlan(name="split", crops=[
            CropArea(0.05, 0.0, 0.46, 1.0, 0.0, 0.0, 1.0, 0.5),
            CropArea(0.55, 0.0, 0.45, 1.0, 0.0, 0.5, 1.0, 0.5),
        ])
        filt = _build_reframe_filter(layout, 720, 1280, 25)
        assert "[0:v]split=2[a][b]" in filt
        assert filt.count("crop=w=iw*") == 2
        assert "x=iw*0.050000:y=ih*0.000000" in filt
        assert "x=iw*0.550000:y=ih*0.000000" in filt
        assert "scale=720:640[a2]" in filt
        assert "scale=720:640[b2]" in filt  # 1280 - 1280//2 == 640
        assert "[a2][b2]vstack=2,format=yuv420p" in filt
        assert "boxblur" not in filt

    def test_split_dimensiones_de_mitades_suman_target_height(self):
        layout = LayoutPlan(name="split", crops=[
            CropArea(0.0, 0.0, 0.5, 1.0, 0.0, 0.0, 1.0, 0.5),
            CropArea(0.5, 0.0, 0.5, 1.0, 0.0, 0.5, 1.0, 0.5),
        ])
        filt = _build_reframe_filter(layout, 720, 1281, 25)  # alto impar, a propósito
        assert "scale=720:640[a2]" in filt
        assert "scale=720:641[b2]" in filt  # 1281 - 640 = 641

    def test_fill_filtro_tiene_crop_y_scale_al_target(self):
        layout = LayoutPlan(name="fill", crops=[
            CropArea(0.30, 0.10, 0.22, 0.72, 0.0, 0.0, 1.0, 1.0),
        ])
        filt = _build_reframe_filter(layout, 720, 1280, 25)
        assert "crop=w=iw*0.220000:h=ih*0.720000:x=iw*0.300000:y=ih*0.100000" in filt
        assert "scale=720:1280" in filt
        assert "boxblur" not in filt
        assert "vstack" not in filt

    def test_layout_con_nombre_desconocido_lanza_error(self):
        from services.clip_generator import ClipGenerationError
        layout = LayoutPlan(name="three", crops=[CropArea(0, 0, 1, 1, 0, 0, 1, 1)])
        with pytest.raises(ClipGenerationError):
            _build_reframe_filter(layout, 720, 1280, 25)

    def test_layout_con_crops_vacios_cae_a_fit_aunque_el_nombre_no_sea_fit(self):
        # defensivo: si por algún bug choose_layout devolviera "split" sin
        # crops, no debe crashear el render — cae al filtro de siempre.
        layout = LayoutPlan(name="split", crops=[])
        filt = _build_reframe_filter(layout, 720, 1280, 25)
        assert _build_reframe_filter(None, 720, 1280, 25) == filt
