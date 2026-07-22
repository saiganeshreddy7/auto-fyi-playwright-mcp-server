from __future__ import annotations

import pytest

from autofyi_mcp.confirmations import ConfirmationStore
from autofyi_mcp.errors import ConfirmationError


def make(store: ConfirmationStore):
    return store.create(
        action="split_interim",
        endpoint="/split",
        client_id="C1",
        client_name="Client One",
        payload={"amount": 100},
        required_confirmation="SPLIT Client One 01 Jul 2026",
        preview={"amount": 100},
    )


def test_wrong_phrase_does_not_consume() -> None:
    store = ConfirmationStore()
    item = make(store)
    with pytest.raises(ConfirmationError, match="does not match"):
        store.consume(item.confirmation_id, "yes")
    assert store.get(item.confirmation_id) == item


def test_confirmation_is_single_use() -> None:
    store = ConfirmationStore()
    item = make(store)
    store.consume(item.confirmation_id, " split   client one 01 jul 2026 ")
    with pytest.raises(ConfirmationError, match="already used"):
        store.get(item.confirmation_id)


def test_expired_confirmation_is_rejected() -> None:
    now = [100.0]
    store = ConfirmationStore(ttl_seconds=10, clock=lambda: now[0])
    item = make(store)
    now[0] = 111.0
    with pytest.raises(ConfirmationError, match="expired"):
        store.get(item.confirmation_id)


def test_cancelled_confirmation_is_rejected() -> None:
    store = ConfirmationStore()
    item = make(store)
    store.cancel(item.confirmation_id)
    with pytest.raises(ConfirmationError, match="cancelled"):
        store.get(item.confirmation_id)
