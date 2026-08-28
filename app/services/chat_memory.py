"""Pure short-term-memory selection rules shared by API and tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemoryMessage:
    id: int
    message_order: int
    role: str
    content: str
    token_count: int
    tokenizer_name: str
    citations: list[dict[str, Any]] = field(default_factory=list)


def compatible_count(message: MemoryMessage, counter) -> int:
    if message.tokenizer_name == counter.tokenizer_name and message.token_count > 0:
        return message.token_count
    return counter.count_text(message.content).count


def complete_turns(messages: list[MemoryMessage]) -> list[list[MemoryMessage]]:
    """Pair each user with its assistant; omit misleading orphan assistants."""
    turns: list[list[MemoryMessage]] = []
    pending = None
    for message in sorted(messages, key=lambda item: item.message_order):
        if message.role == "user":
            if pending is not None:
                turns.append([pending])
            pending = message
        elif message.role == "assistant" and pending is not None:
            turns.append([pending, message])
            pending = None
    if pending is not None:
        turns.append([pending])
    return turns


def select_recent_turns(messages, budget: int, counter) -> list[MemoryMessage]:
    """Newest complete turns that fit, returned oldest-to-newest."""
    selected: list[list[MemoryMessage]] = []
    used = 0
    for turn in reversed(complete_turns(messages)):
        turn_cost = sum(compatible_count(message, counter) + 4 for message in turn)
        if used + turn_cost > budget:
            break
        selected.append(turn)
        used += turn_cost
    return [message for turn in reversed(selected) for message in turn]


def select_passages(passages, budget: int, counter):
    """Rank-order, deduplicate, and stop at the transcript token budget."""
    selected = []
    seen = set()
    used = 0
    for passage in passages:
        if passage.chunk_id in seen:
            continue
        cost = counter.count_text(passage.text).count + 16
        if cost > budget - used:
            continue
        selected.append(passage)
        seen.add(passage.chunk_id)
        used += cost
    return selected


def messages_to_summarize(messages, checkpoint: int, threshold: int, counter,
                          keep_recent_messages: int = 4):
    """Return an older prefix only after it crosses the configured threshold."""
    unsummarized = [
        message for message in sorted(messages, key=lambda item: item.message_order)
        if message.message_order > checkpoint
    ]
    if len(unsummarized) <= keep_recent_messages:
        return []
    candidates = unsummarized[:-keep_recent_messages]
    total = sum(compatible_count(message, counter) + 4 for message in candidates)
    return candidates if total >= threshold else []


def bound_text(text: str, budget: int, counter) -> str:
    """Token-measured emergency bound for an unexpectedly verbose summary."""
    value = (text or "").strip()
    while value and counter.count_text(value).count > budget:
        value = value[: max(1, len(value) * 3 // 4)].rstrip()
    return value
