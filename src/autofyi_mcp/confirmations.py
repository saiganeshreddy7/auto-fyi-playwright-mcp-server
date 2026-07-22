from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from autofyi_mcp.errors import ConfirmationError


def _canonical_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_phrase(value: str) -> str:
    return " ".join(value.strip().split()).casefold()


@dataclass(frozen=True, slots=True)
class PendingAction:
    confirmation_id: str
    action: str
    endpoint: str
    client_id: str
    client_name: str
    payload: dict[str, Any]
    payload_hash: str
    required_confirmation: str
    preview: dict[str, Any]
    created_at: float
    expires_at: float

    def public(self) -> dict[str, Any]:
        return {
            "confirmation_id": self.confirmation_id,
            "action": self.action,
            "client_id": self.client_id,
            "client_name": self.client_name,
            "payload_hash": self.payload_hash,
            "required_confirmation": self.required_confirmation,
            "expires_in_seconds": max(0, int(self.expires_at - time.time())),
            "preview": deepcopy(self.preview),
        }


class ConfirmationStore:
    def __init__(self, ttl_seconds: int = 600, clock: Callable[[], float] = time.time) -> None:
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._items: dict[str, PendingAction] = {}
        self._cancelled: set[str] = set()
        self._used: set[str] = set()
        self._lock = threading.Lock()

    def create(
        self,
        *,
        action: str,
        endpoint: str,
        client_id: str,
        client_name: str,
        payload: dict[str, Any],
        required_confirmation: str,
        preview: dict[str, Any],
    ) -> PendingAction:
        now = self.clock()
        item = PendingAction(
            confirmation_id=secrets.token_urlsafe(18),
            action=action,
            endpoint=endpoint,
            client_id=client_id,
            client_name=client_name,
            payload=deepcopy(payload),
            payload_hash=_canonical_hash(payload),
            required_confirmation=required_confirmation,
            preview=deepcopy(preview),
            created_at=now,
            expires_at=now + self.ttl_seconds,
        )
        with self._lock:
            self._items[item.confirmation_id] = item
        return item

    def get(self, confirmation_id: str) -> PendingAction:
        with self._lock:
            if confirmation_id in self._cancelled:
                raise ConfirmationError("This prepared action was cancelled.")
            if confirmation_id in self._used:
                raise ConfirmationError(
                    "This confirmation was already used and cannot be replayed."
                )
            item = self._items.get(confirmation_id)
            if item is None:
                raise ConfirmationError("Unknown confirmation ID. Prepare the action again.")
            if self.clock() >= item.expires_at:
                self._items.pop(confirmation_id, None)
                raise ConfirmationError("This confirmation expired. Re-read FYI and prepare again.")
            if _canonical_hash(item.payload) != item.payload_hash:
                self._items.pop(confirmation_id, None)
                raise ConfirmationError("Prepared payload integrity check failed.")
            return item

    def verify_phrase(self, confirmation_id: str, phrase: str) -> PendingAction:
        item = self.get(confirmation_id)
        if not hmac.compare_digest(
            _normalize_phrase(phrase), _normalize_phrase(item.required_confirmation)
        ):
            raise ConfirmationError(
                "Confirmation text does not match. Ask the user to provide the exact phrase shown in the preview."
            )
        return item

    def consume(self, confirmation_id: str, phrase: str) -> PendingAction:
        self.verify_phrase(confirmation_id, phrase)
        with self._lock:
            item = self._items.pop(confirmation_id)
            self._used.add(confirmation_id)
            return item

    def cancel(self, confirmation_id: str) -> dict[str, Any]:
        self.get(confirmation_id)
        with self._lock:
            self._items.pop(confirmation_id, None)
            self._cancelled.add(confirmation_id)
        return {"cancelled": True, "confirmation_id": confirmation_id}
