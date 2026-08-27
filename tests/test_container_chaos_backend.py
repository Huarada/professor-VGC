"""Wiring tests for PROFESSORVGC_CHAOS_BACKEND through the Container.

Construction-only for the Firestore branch (no real network/credentials
here — just confirms the Container validates config the same way for
either backend), plus the shared-instance caching behavior that matters
most for the Firestore case (see chaos_repository()'s own docstring).
"""

from __future__ import annotations

import pytest

from src.adapters.chaos.chaos_repository import ChaosRepository
from src.config import Settings
from src.domain.exceptions import ConfigurationError
from src.services.container import Container


# _env_file=None on every Settings() below is deliberate, not decoration:
# this suite must stay hermetic (README's own promise — "no Node or API
# keys REQUIRED to pass") regardless of what a contributor's own real .env
# happens to set PROFESSORVGC_CHAOS_BACKEND/FIRESTORE_* to. Without it, a
# real .env with chaos_backend=firestore silently flips these tests onto a
# real network call to real Firestore — reported: exactly this happened
# once this project's own local .env was updated for live Firestore use.


def test_local_backend_is_the_default(sample_chaos_path):
    container = Container(Settings(_env_file=None, chaos_data_path=sample_chaos_path))
    assert isinstance(container.chaos_repository(), ChaosRepository)


def test_chaos_repository_is_cached_and_shared_between_adapters(sample_chaos_path):
    container = Container(Settings(_env_file=None, chaos_data_path=sample_chaos_path))
    repo = container.chaos_repository()
    assert container.chaos_repository() is repo  # cached, not rebuilt
    assert container.chaos()._repo is repo
    assert container._chaos_strategy()._repo is repo


def test_unknown_chaos_backend_raises_configuration_error(sample_chaos_path):
    container = Container(
        Settings(_env_file=None, chaos_data_path=sample_chaos_path, chaos_backend="s3")
    )
    with pytest.raises(ConfigurationError):
        container.chaos_repository()


def test_firestore_backend_without_project_id_raises_configuration_error():
    container = Container(
        Settings(_env_file=None, chaos_backend="firestore", firestore_project_id=None)
    )
    with pytest.raises(ConfigurationError):
        container.chaos_repository()


def test_shutdown_clears_the_cached_chaos_repository(sample_chaos_path):
    container = Container(Settings(_env_file=None, chaos_data_path=sample_chaos_path))
    container.chaos_repository()
    container.shutdown()
    assert container._chaos_repo is None
