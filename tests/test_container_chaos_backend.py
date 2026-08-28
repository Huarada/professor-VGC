"""Wiring tests for Container.chaos_repository() — Firestore,
UNCONDITIONALLY (there is no local-file backend anymore; see config.py's
own comment on why that's a deliberate requirement, not a preference).

patch_firestore_repo (conftest.py) redirects the real
FirestoreChaosRepository construction onto a fake Firestore client, so
these stay hermetic without network/credentials — except the missing-
project-id test, which needs no fake at all: that error fires before any
network call is ever attempted.
"""

from __future__ import annotations

import pytest

from src.adapters.chaos.firestore_chaos_repository import FirestoreChaosRepository
from src.config import Settings
from src.domain.exceptions import ConfigurationError
from src.services.container import Container


def test_chaos_repository_is_always_firestore(patch_firestore_repo):
    container = Container(Settings(_env_file=None, firestore_project_id="test-project"))
    assert isinstance(container.chaos_repository(), FirestoreChaosRepository)


def test_chaos_repository_is_cached_and_shared_between_adapters(patch_firestore_repo):
    container = Container(Settings(_env_file=None, firestore_project_id="test-project"))
    repo = container.chaos_repository()
    assert container.chaos_repository() is repo  # cached, not rebuilt
    assert container.chaos()._repo is repo
    assert container._chaos_strategy()._repo is repo


def test_missing_project_id_raises_configuration_error_with_no_network():
    """No patch_firestore_repo needed: FirestoreChaosRepository's own
    constructor checks project_id BEFORE ever touching the network (see
    firestore_chaos_repository._build_client) — this is the PRIMARY error
    case now that there is no local-file fallback to silently degrade to."""
    container = Container(Settings(_env_file=None, firestore_project_id=None))
    with pytest.raises(ConfigurationError):
        container.chaos_repository()


def test_shutdown_clears_the_cached_chaos_repository(patch_firestore_repo):
    container = Container(Settings(_env_file=None, firestore_project_id="test-project"))
    container.chaos_repository()
    container.shutdown()
    assert container._chaos_repo is None
