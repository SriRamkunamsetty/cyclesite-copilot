import pytest


@pytest.fixture()
def synthetic_env(monkeypatch):
    """Point the agent's services at the offline synthetic backend."""
    from cycle_agent import services

    monkeypatch.setenv("CYCLESITE_BACKEND", "synthetic")
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    services.reset_caches()
    yield
    services.reset_caches()
