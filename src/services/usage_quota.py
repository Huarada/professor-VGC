"""Per-visitor daily usage quota for paid LLM providers.

The deployed app spends the operator's own provider keys (there is no
per-user key field), so each provider can be capped at N analyses per
visitor per UTC day. One analysis = one Analyze click, however many model
calls the pipeline makes inside it.

Visitors are never stored in clear: the bucket key is an HMAC of the visitor
id (the client IP on Cloud Run) under a server-side secret, so the stored
documents cannot be reversed into IP addresses by brute force without it.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Mapping
from datetime import datetime, timezone

from src.domain.exceptions import UsageLimitExceededError
from src.domain.interfaces import UsageQuotaStore


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UsageQuotaService:
    """Enforces ``limits`` (provider -> analyses per visitor per UTC day;
    0 or absent = unlimited) against an atomic :class:`UsageQuotaStore`."""

    def __init__(
        self,
        store: UsageQuotaStore | None,
        limits: Mapping[str, int],
        *,
        secret: str,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._store = store
        self._limits = {name.lower(): max(0, value) for name, value in limits.items()}
        self._secret = secret.encode("utf-8")
        self._clock = clock

    def limit(self, provider: str) -> int:
        """Daily analyses allowed per visitor for ``provider``; 0 = unlimited."""
        return self._limits.get(provider.lower(), 0) if self._store is not None else 0

    def remaining(self, provider: str, visitor: str) -> int | None:
        """Analyses ``visitor`` has left today with ``provider`` (``None`` = unlimited).

        Raises:
            UsageQuotaError: The store could not be read.
        """
        limit = self.limit(provider)
        if not limit or self._store is None:
            return None
        return max(0, limit - self._store.claimed(self._bucket(provider, visitor), limit))

    def consume(self, provider: str, visitor: str) -> int | None:
        """Spend one analysis of ``visitor``'s daily quota for ``provider``.

        Returns:
            The analyses left after this one (``None`` = unlimited).

        Raises:
            UsageLimitExceededError: The daily quota is already used up.
            UsageQuotaError: The store could not be updated.
        """
        limit = self.limit(provider)
        if not limit or self._store is None:
            return None
        slot = self._store.claim(self._bucket(provider, visitor), limit)
        if slot is None:
            raise UsageLimitExceededError(
                f"Daily limit reached: {limit} {provider} analyses per visitor per day "
                f"(resets at 00:00 UTC)."
            )
        return limit - slot

    def _bucket(self, provider: str, visitor: str) -> str:
        day = self._clock().astimezone(timezone.utc).strftime("%Y-%m-%d")
        digest = hmac.new(self._secret, visitor.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{day}_{provider.lower()}_{digest[:32]}"
