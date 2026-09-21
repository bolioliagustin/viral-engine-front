"""
W11 — Subtítulos v2: bloques cortos, MAYÚSCULAS, palabra clave resaltada
(docs/PLAN_CALIDAD.md §9 Fase 1; docs/ANALISIS_OPUS_CLIP.md §2.5).

Cubre las funciones puras de agrupado/keywords/ASS de
services/clip_generator.py sin necesitar FFmpeg.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.clip_generator import (  # noqa: E402
    OVERLAY_STYLES,
    SUBTITLE_STYLES,
    ClipGenerationError,
    _v2_block_to_ass_text,
    _v2_blocks_with_timing,
    _v2_words_to_ass,
    detect_keywords_v2,
    group_words_v2,
)


def _w(word: str, start: float, end: float) -> dict:
    return {"word": word, "start": start, "end": end}


# ═══════════════════════════════════════════════════════════════════════════
# group_words_v2
# ═══════════════════════════════════════════════════════════════════════════

class TestGroupWordsV2:
    def test_bloques_de_hasta_3_palabras(self):
        words = [_w(f"palabra{i}", i * 0.2, i * 0.2 + 0.15) for i in range(7)]
        groups = group_words_v2(words)
        assert all(len(g) <= 3 for g in groups)
        # 7 palabras contiguas (gaps chicos) -> 3+3+1
        assert [len(g) for g in groups] == [3, 3, 1]

    def test_corta_en_puntuacion_fuerte(self):
        words = [
            _w("Hola", 0.0, 0.2),
            _w("mundo.", 0.25, 0.5),
            _w("Cómo", 0.55, 0.7),
            _w("estás", 0.75, 1.0),
        ]
        groups = group_words_v2(words)
        texts = [[w["word"] for w in g] for g in groups]
        assert texts == [["Hola", "mundo."], ["Cómo", "estás"]]

    def test_corta_en_gap_mayor_a_035(self):
        words = [
            _w("Hola", 0.0, 0.2),
            _w("mundo", 0.25, 0.5),   # gap 0.05 -> mismo bloque
            _w("después", 1.2, 1.5),  # gap 0.7 -> corta acá
        ]
        groups = group_words_v2(words)
        texts = [[w["word"] for w in g] for g in groups]
        assert texts == [["Hola", "mundo"], ["después"]]

    def test_gap_justo_en_el_umbral_035_corta(self):
        words = [
            _w("uno", 0.0, 0.2),
            _w("dos", 0.55, 0.8),  # gap exactamente 0.35
        ]
        groups = group_words_v2(words)
        assert len(groups) == 2

    def test_particula_no_queda_sola_al_final_de_bloque(self):
        # "el", "la", "de" son partículas — al cortar por conteo (3 max),
        # si la 3ra palabra es partícula se arrastra la siguiente.
        words = [
            _w("Encontré", 0.0, 0.3),
            _w("la", 0.32, 0.4),
            _w("solución", 0.42, 0.7),
            _w("perfecta", 0.72, 1.0),
        ]
        groups = group_words_v2(words, max_words=2)
        texts = [[w["word"] for w in g] for g in groups]
        # Sin la regla, cortaría ["Encontré","la"] dejando "la" sola al
        # final. Con la regla, se arrastra "solución" al primer bloque.
        assert texts[0] == ["Encontré", "la", "solución"]
        assert "la" not in texts[1] if len(texts) > 1 else True

    def test_lista_vacia(self):
        assert group_words_v2([]) == []

    def test_ignora_palabras_vacias(self):
        words = [_w("", 0.0, 0.1), _w("Hola", 0.1, 0.3), _w("   ", 0.3, 0.4)]
        groups = group_words_v2(words)
        assert [[w["word"] for w in g] for g in groups] == [["Hola"]]


# ═══════════════════════════════════════════════════════════════════════════
# _v2_blocks_with_timing
# ═══════════════════════════════════════════════════════════════════════════

class TestBlocksWithTiming:
    def test_bloque_menor_a_025s_se_funde_con_el_anterior(self):
        groups = [
            [_w("Hola", 0.0, 0.4)],
            [_w("ok", 0.45, 0.55)],  # dura 0.10s < 0.25 -> se funde
        ]
        blocks = _v2_blocks_with_timing(groups)
        assert len(blocks) == 1
        assert [w["word"] for w in blocks[0]["words"]] == ["Hola", "ok"]

    def test_primer_bloque_corto_se_funde_hacia_adelante(self):
        groups = [
            [_w("eh", 0.0, 0.1)],   # dura 0.10s, es el primero
            [_w("bueno", 0.15, 0.6)],
        ]
        blocks = _v2_blocks_with_timing(groups)
        assert len(blocks) == 1
        assert [w["word"] for w in blocks[0]["words"]] == ["eh", "bueno"]

    def test_no_funde_a_traves_de_un_silencio(self):
        groups = [
            [_w("ok", 0.0, 0.1)],       # corto, pero...
            [_w("después", 0.7, 1.0)],  # ...el gap es 0.6s >= 0.5 (silencio)
        ]
        blocks = _v2_blocks_with_timing(groups)
        # No se funden: quedan dos bloques (uno de ellos sigue < 0.25s,
        # pero fusionarlo cruzaría el silencio).
        assert len(blocks) == 2

    def test_no_extiende_el_final_dentro_de_un_silencio(self):
        groups = [
            [_w("Hola", 0.0, 0.4)],
            [_w("Chau", 1.0, 1.3)],  # gap 0.6s >= 0.5 -> silencio
        ]
        blocks = _v2_blocks_with_timing(groups)
        # El primer bloque NO se estira hacia el segundo (sin silencio se
        # estiraría hasta next_start - gap_sec).
        assert blocks[0]["end"] == pytest.approx(0.4, abs=1e-6)

    def test_extiende_el_final_cuando_no_hay_silencio(self):
        groups = [
            [_w("Hola", 0.0, 0.4)],
            [_w("mundo", 0.5, 0.8)],  # gap 0.1s -> no es silencio
        ]
        blocks = _v2_blocks_with_timing(groups)
        assert blocks[0]["end"] > 0.4

    def test_lista_vacia(self):
        assert _v2_blocks_with_timing([]) == []


# ═══════════════════════════════════════════════════════════════════════════
# detect_keywords_v2
# ═══════════════════════════════════════════════════════════════════════════

def _blocks(*word_lists):
    return [{"words": [_w(w, i, i + 0.2) for i, w in enumerate(wl)]} for wl in word_lists]


class TestDetectKeywordsV2:
    def test_con_moment_keywords_marca_hasta_dos_por_bloque(self):
        blocks = _blocks(["El", "sarampión", "es", "más", "contagioso"])
        picks = detect_keywords_v2(blocks, moment_keywords=["sarampión", "contagioso"])
        assert picks == [[1, 4]]

    def test_con_moment_keywords_sin_match_no_marca_nada(self):
        blocks = _blocks(["Hola", "mundo"])
        picks = detect_keywords_v2(blocks, moment_keywords=["inflación", "R0"])
        assert picks == [[]]

    def test_heuristica_numeros(self):
        blocks = _blocks(["Cuesta", "500", "dólares"])
        picks = detect_keywords_v2(blocks, moment_keywords=None)
        assert picks == [[1]]

    def test_heuristica_mayuscula_que_no_arranca_bloque(self):
        blocks = _blocks(["viajó", "a", "Argentina"])
        picks = detect_keywords_v2(blocks, moment_keywords=None)
        assert picks == [[2]]

    def test_heuristica_no_marca_mayuscula_si_arranca_el_bloque(self):
        # "Argentina" es la primera palabra del bloque -> no cuenta como
        # "mayúscula que no arranca oración", pero sigue siendo >=7 letras.
        blocks = _blocks(["Argentina", "gana"])
        picks = detect_keywords_v2(blocks, moment_keywords=None)
        assert picks == [[0]]  # por la regla de >=7 letras, no por mayúscula

    def test_heuristica_palabra_larga(self):
        blocks = _blocks(["es", "impresionante", "che"])
        picks = detect_keywords_v2(blocks, moment_keywords=None)
        assert picks == [[1]]

    def test_heuristica_excluye_particulas(self):
        blocks = _blocks(["para", "el", "de"])
        picks = detect_keywords_v2(blocks, moment_keywords=None)
        assert picks == [[]]

    def test_heuristica_maximo_uno_por_bloque(self):
        blocks = _blocks(["500", "kilómetros", "recorridos"])
        picks = detect_keywords_v2(blocks, moment_keywords=None)
        assert len(picks[0]) == 1

    def test_sin_moment_keywords_vacio_usa_heuristica(self):
        blocks = _blocks(["100", "por", "ciento"])
        picks_none = detect_keywords_v2(blocks, moment_keywords=None)
        picks_empty = detect_keywords_v2(blocks, moment_keywords=[])
        assert picks_none == picks_empty == [[0]]


# ═══════════════════════════════════════════════════════════════════════════
# _v2_block_to_ass_text — MAYÚSCULAS + color
# ═══════════════════════════════════════════════════════════════════════════

class TestBlockToAssText:
    def test_todo_en_mayusculas(self):
        block = {"words": [_w("hola", 0, 0.2), _w("Mundo", 0.2, 0.4)]}
        text = _v2_block_to_ass_text(block, [])
        assert text == "HOLA MUNDO"

    def test_palabra_clave_primaria_en_verde(self):
        block = {"words": [_w("dato", 0, 0.2), _w("sorprendente", 0.2, 0.4)]}
        text = _v2_block_to_ass_text(block, [1])
        assert r"\c&H0027F804&" in text  # #04F827 -> &H00BBGGRR
        assert "SORPRENDENTE" in text
        assert r"{\r}" in text

    def test_dos_palabras_clave_primaria_y_secundaria(self):
        block = {"words": [_w("uno", 0, 0.2), _w("dos", 0.2, 0.4), _w("tres", 0.4, 0.6)]}
        text = _v2_block_to_ass_text(block, [0, 2])
        assert r"\c&H0027F804&" in text     # primario verde
        assert r"\c&H0003FDFF&" in text     # secundario amarillo #FFFD03

    def test_sin_picks_sin_tags_de_color(self):
        block = {"words": [_w("hola", 0, 0.2)]}
        text = _v2_block_to_ass_text(block, [])
        assert r"\c" not in text


# ═══════════════════════════════════════════════════════════════════════════
# _v2_words_to_ass — documento completo
# ═══════════════════════════════════════════════════════════════════════════

class TestV2WordsToAss:
    def _clip_words(self):
        return [
            _w("El", 0.0, 0.15),
            _w("sarampión", 0.18, 0.6),
            _w("es", 0.63, 0.75),
            _w("muy", 0.78, 0.95),
            _w("contagioso.", 0.98, 1.3),
            # Silencio de 0.7s (>= 0.5) antes de la próxima frase.
            _w("Por", 2.0, 2.15),
            _w("eso", 2.18, 2.3),
            _w("cuidate", 2.33, 2.6),
        ]

    def test_genera_ass_con_bangers_y_mayusculas(self, tmp_path):
        out = str(tmp_path / "subs_v2.ass")
        _v2_words_to_ass(
            words=self._clip_words(),
            output_path=out,
            base_style=SUBTITLE_STYLES["tiktok_viral_v2"],
            play_res_x=720,
            play_res_y=1280,
            clip_duration_sec=3.0,
            moment_keywords=["sarampión", "contagioso"],
        )
        content = Path(out).read_text(encoding="utf-8")
        assert "Bangers" in content
        assert "SARAMPIÓN" in content or "SARAMPIÓN." in content
        assert "sarampión" not in content  # todo mayúsculas
        assert r"\t(0,60,\fscx112\fscy112)" in content  # pop
        assert r"\c&H0027F804&" in content  # keyword resaltada

    def test_sin_texto_durante_silencio(self, tmp_path):
        out = str(tmp_path / "subs_v2.ass")
        _v2_words_to_ass(
            words=self._clip_words(),
            output_path=out,
            base_style=SUBTITLE_STYLES["tiktok_viral_v2"],
            play_res_x=720,
            play_res_y=1280,
            clip_duration_sec=3.0,
        )
        content = Path(out).read_text(encoding="utf-8")
        events = [l for l in content.splitlines() if l.startswith("Dialogue:")]
        # Ningún evento cruza el hueco de silencio 1.3s -> 2.0s.
        for line in events:
            parts = line.split(",")
            start_str, end_str = parts[1], parts[2]

            def _to_sec(t: str) -> float:
                h, m, s = t.split(":")
                return int(h) * 3600 + int(m) * 60 + float(s)

            start, end = _to_sec(start_str), _to_sec(end_str)
            assert not (start < 1.3 and end > 2.0)

    def test_sin_words_lanza_error(self, tmp_path):
        out = str(tmp_path / "subs_v2.ass")
        with pytest.raises(ClipGenerationError):
            _v2_words_to_ass(
                words=[],
                output_path=out,
                base_style=SUBTITLE_STYLES["tiktok_viral_v2"],
                play_res_x=720,
                play_res_y=1280,
                clip_duration_sec=3.0,
            )


# ═══════════════════════════════════════════════════════════════════════════
# Estilo viejo intacto
# ═══════════════════════════════════════════════════════════════════════════

class TestEstiloViejoIntacto:
    def test_tiktok_viral_subtitulos_sin_cambios(self):
        # Snapshot del estilo de subtítulos tiktok_viral tal como estaba
        # antes de W11 — si esto falla, se tocó el estilo viejo por error.
        assert SUBTITLE_STYLES["tiktok_viral"] == {
            "FontName": "Liberation Sans",
            "FontSize": "44",
            "PrimaryColour": "&H00FFFFFF",
            "OutlineColour": "&H00000000",
            "BorderStyle": "1",
            "Outline": "4",
            "Shadow": "0",
            "Bold": "1",
            "Spacing": "0",
            "Alignment": "2",
            "MarginV": "340",
        }

    def test_clean_y_podcast_sin_cambios(self):
        assert SUBTITLE_STYLES["clean"]["FontName"] == "Liberation Sans"
        assert SUBTITLE_STYLES["clean"]["BorderStyle"] == "4"
        assert SUBTITLE_STYLES["podcast"]["PrimaryColour"] == "&H0000FFFF"

    def test_tiktok_viral_v2_existe_y_no_pisa_los_otros(self):
        assert "tiktok_viral_v2" in SUBTITLE_STYLES
        assert SUBTITLE_STYLES["tiktok_viral_v2"]["FontName"] == "Bangers"
        assert len(SUBTITLE_STYLES) == 4

    def test_overlay_question_y_stat_sin_cambios(self):
        assert OVERLAY_STYLES["question"]["PrimaryColour"] == "&H0000FFFF"
        assert OVERLAY_STYLES["stat"]["BorderStyle"] == "4"


# ═══════════════════════════════════════════════════════════════════════════
# Pasada B: keywords (W11 punto 2)
# ═══════════════════════════════════════════════════════════════════════════

class TestPasadaBKeywords:
    def _moment(self):
        from models.schemas import ViralMoment
        return ViralMoment(
            start_time=0, end_time=30, hook="Borrador",
            viral_overlay="BORRADOR", emotional_trigger="curiosidad",
        )

    def _mock_client(self, payload: dict):
        import json
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.usage = None
        resp.choices = [MagicMock(message=MagicMock(content=json.dumps(payload)))]
        client = MagicMock()
        client.chat.completions.create.return_value = resp
        return client

    def _base_payload(self, **overrides):
        payload = {
            "twitter_thread": "\n\n".join([f"Tweet {i} sobre el sarampión." * 6 for i in range(7)]),
            "linkedin_post": "El sarampión es muy contagioso. " * 20,
            "tiktok_caption": "Dato viral #salud",
            "hook": "El sarampión es más contagioso que el COVID.",
            "viral_overlay": "MÁS CONTAGIOSO",
        }
        payload.update(overrides)
        return payload

    def test_pasada_b_devuelve_keywords(self):
        from services.processor import generate_moment_copy_full
        moment = self._moment()
        client = self._mock_client(self._base_payload(keywords=["sarampión", "COVID", "500"]))

        ok = generate_moment_copy_full(moment, "texto del clip", client=client)

        assert ok is True
        assert moment.keywords == ["sarampión", "COVID", "500"]

    def test_sin_keywords_no_rompe(self):
        from services.processor import generate_moment_copy_full
        moment = self._moment()
        client = self._mock_client(self._base_payload())  # sin "keywords"

        ok = generate_moment_copy_full(moment, "texto del clip", client=client)

        assert ok is True
        assert moment.keywords is None

    def test_keywords_no_lista_se_ignora(self):
        from services.processor import generate_moment_copy_full
        moment = self._moment()
        client = self._mock_client(self._base_payload(keywords="sarampión, COVID"))

        generate_moment_copy_full(moment, "texto del clip", client=client)

        assert moment.keywords is None

    def test_keywords_se_recortan_a_12(self):
        from services.processor import generate_moment_copy_full
        moment = self._moment()
        client = self._mock_client(self._base_payload(keywords=[f"palabra{i}" for i in range(20)]))

        generate_moment_copy_full(moment, "texto del clip", client=client)

        assert len(moment.keywords) == 12


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


class TestResolucionCanonicaASS:
    """Los subtítulos y el cartel se posicionan igual en cualquier resolución.

    Los MarginV/FontSize están calibrados en píxeles sobre 720×1280. Cuando
    W9-B agregó el preview de 480×854, interpretarlos en la escala de salida
    subía los subtítulos del 61 % al 41 % del alto y agrandaba la fuente: el
    cartel del hook y el subtítulo se superponían (medido en un render real
    el 21-sep-2026). Fijar PlayResX/Y a la resolución canónica hace que
    libass escale todo proporcionalmente.
    """

    def test_play_res_canonico_es_720x1280(self):
        from services.clip_generator import ASS_PLAY_RES_X, ASS_PLAY_RES_Y
        assert (ASS_PLAY_RES_X, ASS_PLAY_RES_Y) == (720, 1280)

    def test_generate_clip_usa_el_play_res_canonico_en_todas_las_resoluciones(self):
        """generate_clip no puede pasar el tamaño de salida como PlayRes."""
        import re
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent / "services" / "clip_generator.py"
        cuerpo = src.read_text(encoding="utf-8")
        # Ninguna llamada a los constructores de ASS puede usar W/H ni el
        # tamaño del video: todas van por la constante.
        assert "play_res_x=W," not in cuerpo
        assert "play_res_y=H," not in cuerpo
        assert "play_res_x=video_meta.width," not in cuerpo
        assert re.search(r"_srt_to_ass\(\s*srt_path, ass_path, SUBTITLE_STYLES\[subtitle_style\],\s*ASS_PLAY_RES_X, ASS_PLAY_RES_Y", cuerpo)

    def test_subtitulo_y_cartel_no_se_superponen_en_preview(self, tmp_path):
        """El bloque del subtítulo arranca bien por debajo del cartel."""
        from services.clip_generator import (
            _srt_to_ass, _build_overlay_ass, SUBTITLE_STYLES, OVERLAY_STYLES,
            ASS_PLAY_RES_X, ASS_PLAY_RES_Y,
        )
        srt = tmp_path / "s.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:06,000\nSUBTITULO ABAJO\n\n", encoding="utf-8")
        sub_ass = tmp_path / "sub.ass"
        ov_ass = tmp_path / "ov.ass"
        _srt_to_ass(str(srt), str(sub_ass), SUBTITLE_STYLES["tiktok_viral_v2"],
                    ASS_PLAY_RES_X, ASS_PLAY_RES_Y)
        _build_overlay_ass(
            text="CARTEL DEL HOOK", start_sec=0.0, duration_sec=5.0,
            style=OVERLAY_STYLES["tiktok_viral"], alignment=8, margin_v=100,
            play_res_x=ASS_PLAY_RES_X, play_res_y=ASS_PLAY_RES_Y,
            output_path=str(ov_ass),
        )
        # El subtítulo está anclado abajo (Alignment 2) y el cartel arriba
        # (Alignment 8); con el mismo PlayRes la separación es estable.
        sub_txt = sub_ass.read_text(encoding="utf-8")
        ov_txt = ov_ass.read_text(encoding="utf-8")
        assert f"PlayResY: {ASS_PLAY_RES_Y}" in sub_txt
        assert f"PlayResY: {ASS_PLAY_RES_Y}" in ov_txt
        margen_sub = int([l for l in sub_txt.splitlines() if l.startswith("Style:")][0].split(",")[21])
        margen_ov = int([l for l in ov_txt.splitlines() if l.startswith("Style:")][0].split(",")[21])
        # subtítulo: desde abajo; cartel: desde arriba. Suma < alto ⇒ no chocan.
        assert margen_sub + margen_ov < ASS_PLAY_RES_Y
