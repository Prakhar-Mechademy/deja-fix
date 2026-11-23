"""Pytest isolation: every test gets its own database and its own fakes.

The test files install their fake LLM functions inside the test (install_fakes), and this
fixture restores everything afterwards, so `pytest tests/` works as a whole.
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, llm  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("DEJAFIX_DB", str(tmp_path / "test.db"))
    for name in ("embed", "generate", "generate_json", "_client"):
        monkeypatch.setattr(llm, name, getattr(llm, name))
    monkeypatch.setattr(time, "sleep", time.sleep)
    for key, value in list(vars(config).items()):
        if key.isupper():
            monkeypatch.setattr(config, key, value)
    yield
