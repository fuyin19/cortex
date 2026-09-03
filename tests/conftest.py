"""Explicit real Core injection; tests never infer another repository's location."""
from pathlib import Path
import os
import pytest


@pytest.fixture(autouse=True)
def candidate_core_environment(monkeypatch):
    configured = os.environ.get("CORTEX_REAL_CORE_RUNNER")
    assert configured and Path(configured).is_absolute() and Path(configured).is_file(), (
        "Set CORTEX_REAL_CORE_RUNNER to the actual Core 1.2.1 Candidate runner; integration must not skip"
    )
    monkeypatch.setenv("ANTI_ENTROPY_CORE_RUNNER", configured)
