import os

import pytest

# The app's Settings insists on a Gemini key and an API token at import time.
os.environ.setdefault("GEMINI_API_KEY", "test-key-not-real")
os.environ.setdefault("API_BEARER_TOKEN", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("GEMINI_MODEL", "gemini-test")

from tests.fixtures.synthetic import make_synthetic  # noqa: E402


@pytest.fixture(scope="session")
def synthetic():
    """60 noisy days with a 4-day illness on days 30-33."""
    return make_synthetic()


@pytest.fixture(scope="session")
def linear():
    """60 noise-free days, no illness: a perfectly linear cut at a known TDEE."""
    return make_synthetic(noise=False, illness=False)
