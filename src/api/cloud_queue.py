"""Authenticated Cloud Tasks dispatch; the payload is only an opaque job ID."""

from __future__ import annotations

from typing import Any, cast

import google.auth
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2 import id_token


def verify_task_token(authorization: str, *, audience: str, service_account_email: str) -> bool:
    """True only for a Google-signed OIDC token minted for this worker by the Cloud Tasks service account."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    try:
        claims = cast(Any, id_token).verify_oauth2_token(token, Request(), audience=audience.rstrip("/"))
    except (ValueError, GoogleAuthError):  # fail closed, also when Google's certs are unreachable
        return False
    return bool(claims.get("email_verified")) and claims.get("email") == service_account_email


class CloudTaskDispatcher:
    def __init__(self, *, project: str, location: str, queue: str, worker_url: str,
                 service_account_email: str) -> None:
        if not all((project, location, queue, worker_url, service_account_email)):
            raise ValueError("Cloud Tasks project, location, queue, worker URL and service account are required.")
        self.parent = f"projects/{project}/locations/{location}/queues/{queue}"
        self.worker_url = worker_url.rstrip("/")
        self.service_account_email = service_account_email
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        self.session: Any = cast(Any, AuthorizedSession)(credentials)

    def dispatch(self, job_id: str) -> None:
        task: dict[str, Any] = {"task": {"name": f"{self.parent}/tasks/{job_id}",
                         "dispatchDeadline": "1800s",
                         "httpRequest": {"httpMethod": "POST",
                                         "url": f"{self.worker_url}/internal/run/{job_id}",
                                         "oidcToken": {"serviceAccountEmail": self.service_account_email,
                                                       "audience": self.worker_url}}}}
        response = self.session.post(f"https://cloudtasks.googleapis.com/v2/{self.parent}/tasks",
                                     json=task, timeout=15)
        if response.status_code == 409:
            return  # The same task was already enqueued for this idempotent job.
        response.raise_for_status()
