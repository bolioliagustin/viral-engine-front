"""
W14 (docs/PLAN_CALIDAD.md): services/progress.py es el seam de progreso que
downloader.py y transcriber.py usan sin conocer Supabase ni job_id. Sin hook
registrado (tests, eval, tier e2e) `report` tiene que ser no-op.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import services.progress as progress  # noqa: E402


class TestReportSinHook:
    def test_report_es_no_op_sin_hook(self):
        progress.set_progress_hook(None)
        progress.report("download_audio", 1, 10, "no debería explotar")


class TestReportConHook:
    def test_report_llama_al_hook_con_la_forma_del_contrato(self):
        received = []
        progress.set_progress_hook(
            lambda step, current, total, message: received.append((step, current, total, message))
        )
        try:
            progress.report("transcribe_tramos", 3, 8, "Transcribiendo: tramo 3 de 8")
        finally:
            progress.set_progress_hook(None)
        assert received == [("transcribe_tramos", 3, 8, "Transcribiendo: tramo 3 de 8")]

    def test_excepcion_del_hook_no_se_propaga(self):
        def _boom(*_args):
            raise RuntimeError("el hook explotó")

        progress.set_progress_hook(_boom)
        try:
            progress.report("download_audio", 1, 2, "x")  # no debe lanzar
        finally:
            progress.set_progress_hook(None)

    def test_set_progress_hook_none_desregistra(self):
        received = []
        progress.set_progress_hook(lambda *a: received.append(a))
        progress.set_progress_hook(None)
        progress.report("download_audio", 1, 2, "x")
        assert received == []


class TestTranscribingPercentage:
    """0-15 es la franja que el contrato de P1 reserva a `transcribing`."""

    def test_arranca_en_cero(self):
        assert progress.transcribing_percentage("download_audio", 0, 100) == 0

    def test_descarga_ocupa_la_primera_mitad_de_la_franja(self):
        assert progress.transcribing_percentage("download_audio", 25, 100) == 2
        assert progress.transcribing_percentage("download_audio", 100, 100) == 8

    def test_tramos_ocupan_la_segunda_mitad_de_la_franja(self):
        assert progress.transcribing_percentage("transcribe_tramos", 4, 8) == 11
        assert progress.transcribing_percentage("transcribe_tramos", 8, 8) == 15

    def test_no_retrocede_al_pasar_de_descarga_a_transcripcion(self):
        fin_descarga = progress.transcribing_percentage("download_audio", 100, 100)
        inicio_tramos = progress.transcribing_percentage("transcribe_tramos", 1, 8)
        assert inicio_tramos >= fin_descarga

    def test_total_cero_no_rompe(self):
        assert progress.transcribing_percentage("download_audio", 0, 0) == 0

    def test_step_desconocido_ocupa_toda_la_franja(self):
        assert progress.transcribing_percentage("otra_cosa", 1, 2) == 8
