from __future__ import annotations

import os

import pytest
from hypothesis import HealthCheck, settings

from tests.fixtures.builders import build_rich_docx

settings.register_profile("dev", max_examples=60)
settings.register_profile(
    "ci", max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))


@pytest.fixture(scope="session")
def rich_docx() -> bytes:
    return build_rich_docx()
