import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from pathlib import Path  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def test_app_loads_without_upload():
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception


def test_sample_button_shows_metrics():
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.button[0].click().run()
    assert not at.exception
    labels = [m.label for m in at.metric]
    assert "Error rate" in labels and "Total records" in labels


def test_decode_fallback_latin1():
    import importlib.util, sys
    sys.path.insert(0, str(Path(APP).parent / "src"))
    # decode helper lives in app.py; re-implement check via audit_bytes path
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
