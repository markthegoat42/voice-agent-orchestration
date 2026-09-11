"""
Idempotency layer for voice-agent webhooks.

Telephony and voice platforms retry webhooks. Retries are a feature: the
provider cannot tell the difference between "your endpoint is down" and
"your endpoint is slow", so it sends the event again. If the handler is
not idempotent, one phone call books two appointments and sends two
confirmation texts.

This module is the fix I landed after that happened in production. It
derives a stable key from the event payload, records it, and tells the
caller whether the event has already been handled.

Design notes:

- The key is derived from the payload, not from a header. Providers are
  inconsistent about delivery IDs, and some omit them on retry.
- Entries expire. A call ID is only worth remembering for as long as
  retries are plausible, and an unbounded store is its own outage.
- `seen()` is a single atomic check-and-set. Checking first and writing
  after leaves a window where two concurrent retries both pass.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

DEFAULT_TTL_SECONDS = 60 * 60 * 6


def derive_key(event: Mapping[str, Any], *, fields: tuple[str, ...]) -> str:
    """Build a stable idempotency key from selected fields of an event.

    Only the named fields participate, so noisy metadata that changes
    between retries (timestamps, attempt counters, trace IDs) cannot
    make the same logical event look new.

    Raises KeyError if a required field is missing, which is deliberate:
    silently hashing a partial payload produces a key that collides with
    nothing and defeats the whole mechanism.
    """
    if not fields:
        raise ValueError("at least one field is required to derive a key")

    material = {}
    for name in fields:
        if name not in event:
            raise KeyError(f"event is missing required key field: {name!r}")
        material[name] = event[name]

    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class Clock(Protocol):
    def __call__(self) -> float: ...


@dataclass
class InMemoryDedupeStore:
    """Process-local idempotency store.

    Suitable for a single worker. For multiple workers, back `seen()`
    with the same check-and-set semantics in Redis (SET NX EX) or a
    unique constraint in Postgres. The interface is the same; only the
    storage changes.
    """

    ttl_seconds: int = DEFAULT_TTL_SECONDS
    clock: Clock = time.monotonic
    _entries: dict[str, float] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def seen(self, key: str) -> bool:
        """Atomically record `key` and report whether it was already present.

        Returns True when this key has been handled before, meaning the
        caller should drop the event.
        """
        now = self.clock()
        with self._lock:
            self._purge_expired(now)
            expires_at = self._entries.get(key)
            if expires_at is not None and expires_at > now:
                return True
            self._entries[key] = now + self.ttl_seconds
            return False

    def forget(self, key: str) -> None:
        """Drop a key. Used when downstream handling failed and the
        event should be allowed through on the provider's next retry."""
        with self._lock:
            self._entries.pop(key, None)

    def __len__(self) -> int:
        with self._lock:
            self._purge_expired(self.clock())
            return len(self._entries)

    def _purge_expired(self, now: float) -> None:
        expired = [k for k, exp in self._entries.items() if exp <= now]
        for k in expired:
            del self._entries[k]


class WebhookDeduplicator:
    """Wraps a store with the field selection for a given event source."""

    def __init__(
        self,
        store: InMemoryDedupeStore | None = None,
        *,
        key_fields: tuple[str, ...] = ("call_id", "event"),
    ) -> None:
        # `is None`, not `or`: this class defines __len__, so an empty
        # store is falsy and `store or InMemoryDedupeStore()` would
        # quietly throw away the caller's store on every construction.
        self.store = InMemoryDedupeStore() if store is None else store
        self.key_fields = key_fields

    def is_duplicate(self, event: Mapping[str, Any]) -> bool:
        return self.store.seen(derive_key(event, fields=self.key_fields))

    def release(self, event: Mapping[str, Any]) -> None:
        """Undo the record for an event whose handling failed, so the
        provider's retry is allowed to run."""
        self.store.forget(derive_key(event, fields=self.key_fields))
