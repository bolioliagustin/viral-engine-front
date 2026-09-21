"""
Addendum W14 (docs/PLAN_CALIDAD.md): el job 4c6e4410 (21-sep-2026) murió por
el timeout fijo de 30 min llegando a `evaluating`. `compute_job_timeout_sec`
lo reemplaza por uno dinámico según la duración del video.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "development")

import main  # noqa: E402


class TestComputeJobTimeoutSec:
    def test_video_de_14_min_usa_el_piso(self):
        assert main.compute_job_timeout_sec(14 * 60) == 30 * 60

    def test_video_de_54_min(self):
        assert round(main.compute_job_timeout_sec(54 * 60) / 60) == 47

    def test_video_de_77_min(self):
        assert round(main.compute_job_timeout_sec(77 * 60) / 60) == 61

    def test_video_de_90_min_no_llega_al_techo(self):
        assert round(main.compute_job_timeout_sec(90 * 60) / 60) == 69

    def test_duracion_desconocida_usa_el_piso(self):
        assert main.compute_job_timeout_sec(None) == 30 * 60
        assert main.compute_job_timeout_sec(0) == 30 * 60

    def test_nunca_pasa_el_techo(self):
        assert main.compute_job_timeout_sec(10 * 60 * 60) == 75 * 60
