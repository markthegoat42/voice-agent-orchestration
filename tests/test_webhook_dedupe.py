import threading

import pytest

from src.webhook_dedupe import (
    InMemoryDedupeStore,
    WebhookDeduplicator,
    derive_key,
)


def call_event(**overrides):
    event = {
        "call_id": "call_9f2c1",
        "event": "call_ended",
        "attempt": 1,
        "received_at": "2026-03-04T18:22:10Z",
    }
    event.update(overrides)
    return event


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_same_call_same_event_is_one_key():
    a = derive_key(call_event(), fields=("call_id", "event"))
    b = derive_key(call_event(attempt=4), fields=("call_id", "event"))
    assert a == b


def test_retry_metadata_does_not_change_the_key():
    a = derive_key(call_event(), fields=("call_id", "event"))
    b = derive_key(
        call_event(attempt=9, received_at="2026-03-04T18:25:00Z"),
        fields=("call_id", "event"),
    )
    assert a == b


def test_different_event_on_same_call_is_a_different_key():
    ended = derive_key(call_event(), fields=("call_id", "event"))
    started = derive_key(call_event(event="call_started"), fields=("call_id", "event"))
    assert ended != started


def test_missing_key_field_is_loud():
    with pytest.raises(KeyError):
        derive_key({"event": "call_ended"}, fields=("call_id", "event"))


def test_no_fields_is_rejected():
    with pytest.raises(ValueError):
        derive_key(call_event(), fields=())


def test_first_delivery_passes_and_retry_is_dropped():
    dedupe = WebhookDeduplicator()
    assert dedupe.is_duplicate(call_event()) is False
    assert dedupe.is_duplicate(call_event(attempt=2)) is True
    assert dedupe.is_duplicate(call_event(attempt=3)) is True


def test_entry_expires_after_ttl():
    clock = FakeClock()
    store = InMemoryDedupeStore(ttl_seconds=300, clock=clock)
    dedupe = WebhookDeduplicator(store)

    assert dedupe.is_duplicate(call_event()) is False
    clock.advance(299)
    assert dedupe.is_duplicate(call_event()) is True

    clock.advance(2)
    assert dedupe.is_duplicate(call_event()) is False


def test_expired_entries_are_purged_from_the_store():
    clock = FakeClock()
    store = InMemoryDedupeStore(ttl_seconds=60, clock=clock)
    for i in range(50):
        store.seen(f"key-{i}")
    assert len(store) == 50

    clock.advance(61)
    assert len(store) == 0


def test_release_lets_a_failed_event_through_again():
    dedupe = WebhookDeduplicator()
    event = call_event()

    assert dedupe.is_duplicate(event) is False
    dedupe.release(event)
    assert dedupe.is_duplicate(event) is False
    assert dedupe.is_duplicate(event) is True


def test_concurrent_retries_produce_exactly_one_winner():
    """The bug this module exists for. Two workers pick up the provider's
    retry at the same moment; exactly one must be allowed through."""
    dedupe = WebhookDeduplicator()
    event = call_event()
    results: list[bool] = []
    lock = threading.Lock()
    barrier = threading.Barrier(24)

    def attempt():
        barrier.wait()
        outcome = dedupe.is_duplicate(event)
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=attempt) for _ in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(False) == 1, "exactly one delivery should be handled"
    assert results.count(True) == 23
