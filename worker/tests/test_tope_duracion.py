"""
W20 parte 3 — tope real de duración en el worker
(docs/briefs/W20-operacion-purga-y-tope.md, decisión A5 de PLAN_MEJORA).
Todo con mocks: nada toca Supabase ni descarga.
"""
import os
from unittest.mock import patch

import pytest


class TestMensaje:
    def test_151_min_con_tope_150(self):
        import main

        assert main.video_too_long_message(151 * 60, 150) == "El video dura 151 min; el máximo es 150 min"

    def test_en_el_tope_entra(self):
        import main

        assert main.video_too_long_message(150 * 60, 150) is None

    def test_duracion_desconocida_no_bloquea(self, capsys):
        import main

        assert main.video_too_long_message(None, 150) is None
        assert main.video_too_long_message(0, 150) is None
        assert "desconocida" in capsys.readouterr().out

    @pytest.mark.parametrize("raw,expected", [(None, 150.0), ("", 150.0), ("120", 120.0),
                                              ("abc", 150.0), ("0", 150.0)])
    def test_default_150_y_override(self, raw, expected):
        import main

        env = {k: v for k, v in os.environ.items() if k != "MAX_VIDEO_MINUTES"}
        if raw is not None:
            env["MAX_VIDEO_MINUTES"] = raw
        with patch.dict(os.environ, env, clear=True):
            assert main.max_video_minutes() == expected


def _run_job(transcript_duration, early_duration=0):
    """Corre process_job hasta después del transcript. La Pasada A está
    mockeada para cortar ahí: si el tope no frena el job, el error es otro."""
    import main

    calls = {"analysis": 0}

    def _analysis(*_a, **_k):
        calls["analysis"] += 1
        raise RuntimeError("pasada A alcanzada")

    transcript = {"text": "hola", "segments": [], "language": "es", "duration": transcript_duration}
    info = {"id": "vidLargo001", "title": "T", "duration": transcript_duration}
    with patch.dict(os.environ, {"MAX_VIDEO_MINUTES": "150"}), \
         patch("services.yt_transcript.get_video_metadata", return_value={"duration": early_duration}), \
         patch("services.yt_transcript.get_youtube_transcript", return_value=(transcript, info)) as gyt, \
         patch("services.processor.analyze_with_openrouter", side_effect=_analysis), \
         patch("services.supabase_client.update_job_progress"), \
         patch("services.transcript_cache.save_transcript"), \
         patch.object(main, "update_job_status") as status, \
         patch.object(main, "update_job_error") as error, \
         patch.object(main, "get_supabase", return_value=None), \
         patch.object(main, "finalize_job_usage"), \
         patch.object(main, "cleanup_all"), \
         patch.object(main, "cleanup_clips"):
        main.process_job({"id": "job-w20", "videoUrl": "https://youtu.be/vidLargo001", "userId": "u1"})
    return {"error": error, "status": status, "transcript_calls": gyt.call_count, **calls}


class TestJob:
    def test_151_min_falla_con_mensaje_y_devuelve_el_credito(self):
        r = _run_job(151 * 60)
        # update_job_error → status 'failed' → trigger F1 release_credit_on_job_failed
        r["error"].assert_called_once_with("job-w20", "El video dura 151 min; el máximo es 150 min")
        assert r["analysis"] == 0, "no se paga la Pasada A"
        assert ("job-w20", "completed") not in [c.args[:2] for c in r["status"].call_args_list]

    def test_duracion_temprana_corta_antes_del_transcript(self):
        r = _run_job(151 * 60, early_duration=151 * 60)
        r["error"].assert_called_once_with("job-w20", "El video dura 151 min; el máximo es 150 min")
        assert r["transcript_calls"] == 0, "no se baja ni se transcribe"

    def test_duracion_desconocida_no_bloquea(self):
        r = _run_job(0)
        assert r["analysis"] == 1, "el job sigue hasta la Pasada A"
        r["error"].assert_called_once_with("job-w20", "pasada A alcanzada")

    def test_dentro_del_tope_sigue(self):
        r = _run_job(111 * 60)
        assert r["analysis"] == 1
