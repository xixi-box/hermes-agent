"""Session-local Azure continuation selection without moving an already replayed anchor."""

import hashlib
import json
from typing import Any


def _message_digest(message: dict[str, Any]) -> bytes:
    return hashlib.sha256(json.dumps(message, sort_keys=True).encode()).digest()


def _checkpoint_boundary(messages: list[dict[str, Any]], *, issuer_kind: str, issuer_model: str) -> int:
    from agent.codex_responses_adapter import _replay_reasoning_items

    for index in range(len(messages) - 1, -1, -1):
        eligible = _replay_reasoning_items(
            messages[index], seen_item_ids=set(), current_issuer_kind=issuer_kind,
            current_issuer_model=issuer_model, native_compaction_eligible=True,
        )
        if any(item.get("type") == "compaction" for item in eligible):
            return index + 1
    return 0


class AzureReasoningReplay:
    """Pin one eligible response per append-only history epoch.

    A new transport (resume), route/session switch, or rewritten history (compression,
    checkpoint replacement, encrypted-content recovery) starts an epoch. Its first
    request retains the existing newest-eligible-response compatibility policy.
    Post-tool suppression must remove the anchor for Azure validity; it also retires
    all reasoning already seen, so later requests can only anchor newly appended
    responses and never reinsert reasoning into the suppressed request's prefix.
    """

    def __init__(self) -> None:
        self.route: Any = None
        self.history: tuple[bytes, ...] = ()
        self.anchor: int | None = None
        self.retired_before = 0

    def select(self, messages: list[dict[str, Any]], *, route: Any, enabled: bool,
               issuer_kind: str, issuer_model: str, native_compaction_active: bool) -> int:
        from agent.codex_responses_adapter import _replay_reasoning_items

        checkpoint = _checkpoint_boundary(
            messages, issuer_kind=issuer_kind, issuer_model=issuer_model,
        ) if native_compaction_active else 0
        route = (route, checkpoint)
        history = tuple(_message_digest(msg) for msg in messages)
        if route != self.route or history[:len(self.history)] != self.history:
            self.anchor = None
            self.retired_before = checkpoint
        self.route, self.history = route, history
        if not enabled:
            self.anchor = None
            self.retired_before = len(messages)
            return 0
        if self.anchor is None:
            for index in range(len(messages) - 1, self.retired_before - 1, -1):
                msg = messages[index]
                if msg.get("role") != "assistant":
                    continue
                eligible = _replay_reasoning_items(
                    msg, seen_item_ids=set(), current_issuer_kind=issuer_kind,
                    current_issuer_model=issuer_model, native_compaction_eligible=False,
                )
                if any(item.get("type") == "reasoning" for item in eligible):
                    self.anchor = index
                    break
        return 0 if self.anchor is None else self.anchor + 1
