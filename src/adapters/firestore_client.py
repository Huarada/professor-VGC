"""Firestore client factory shared by every Firestore adapter; the package is
imported lazily (ConfigurationError if missing).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from src.domain.exceptions import ConfigurationError

if TYPE_CHECKING:
    from google.cloud.firestore import Client


def build_firestore_client(
    project_id: str,
    database_id: str,
    credentials_path: str | None,
    grpc_ca_bundle_path: str | None = None,
) -> "Client":
    try:
        from google.cloud import firestore
    except ImportError as exc:  # pragma: no cover - env dependent
        raise ConfigurationError(
            "The 'google-cloud-firestore' package is not installed. "
            "Run: pip install google-cloud-firestore"
        ) from exc
    if grpc_ca_bundle_path:
        # Must be set before the first gRPC channel; never overrides an operator's value.
        os.environ.setdefault("GRPC_DEFAULT_SSL_ROOTS_FILE_PATH", grpc_ca_bundle_path)
    if not project_id:
        raise ConfigurationError(
            "A GCP project id is required (PROFESSORVGC_FIRESTORE_PROJECT_ID)"
        )
    if credentials_path:
        try:
            from google.oauth2.service_account import Credentials
        except ImportError as exc:  # pragma: no cover - env dependent
            raise ConfigurationError(
                "The 'google-auth' package is not installed. Run: pip install google-auth"
            ) from exc
        try:
            # google-auth's own stubs don't type this classmethod's return
            # (a stub-completeness gap, not a real typing issue — mirrors
            # the same class of gap noted in gemini_provider.py).
            credentials = Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
                credentials_path
            )
        except (OSError, ValueError) as exc:
            raise ConfigurationError(
                f"Unable to load the Firestore service account key at "
                f"'{credentials_path}': {exc}"
            ) from exc
        return firestore.Client(
            project=project_id, database=database_id, credentials=credentials
        )
    # No explicit key path: fall back to Application Default Credentials
    # (gcloud auth application-default login, or GOOGLE_APPLICATION_CREDENTIALS).
    return firestore.Client(project=project_id, database=database_id)
