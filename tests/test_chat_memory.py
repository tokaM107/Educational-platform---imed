from types import SimpleNamespace

from app.services.chat_memory import (
    MemoryMessage, messages_to_summarize, select_passages, select_recent_turns,
)
from app.services.token_budget import (
    CountedTokens, PromptTooLarge, ProviderTokenCounter, validate_prompt_size,
)


class Counter:
    tokenizer_name = "counter:v2"
    def count_text(self, text):
        return CountedTokens(len(text.split()) or 1, self.tokenizer_name)


def msg(identifier, order, role, text, count=0, tokenizer="old:model"):
    return MemoryMessage(identifier, order, role, text, count, tokenizer)


def test_recent_history_is_selected_by_tokens_in_chronological_order():
    messages = [
        msg(1, 1, "user", "old question"), msg(2, 2, "assistant", "old answer"),
        msg(3, 3, "user", "new question"), msg(4, 4, "assistant", "new answer"),
    ]
    selected = select_recent_turns(messages, budget=12, counter=Counter())
    assert [item.id for item in selected] == [3, 4]


def test_recent_history_keeps_user_assistant_turns_together():
    messages = [msg(1, 1, "user", "q"), msg(2, 2, "assistant", "a")]
    assert select_recent_turns(messages, budget=5, counter=Counter()) == []
    assert [item.id for item in select_recent_turns(messages, 10, Counter())] == [1, 2]


def test_incompatible_stored_tokenizer_is_recounted():
    # Stored count=1 would fit, but recounting v2 correctly rejects this turn.
    messages = [msg(1, 1, "user", "one two three", count=1),
                msg(2, 2, "assistant", "four five six", count=1)]
    assert select_recent_turns(messages, budget=10, counter=Counter()) == []


def test_transcript_chunks_are_deduplicated_and_token_bounded():
    passages = [
        SimpleNamespace(chunk_id=1, text="one two", distance=.1),
        SimpleNamespace(chunk_id=1, text="duplicate", distance=.2),
        SimpleNamespace(chunk_id=2, text="three four five", distance=.3),
    ]
    assert [item.chunk_id for item in select_passages(passages, 18, Counter())] == [1]
    assert [item.chunk_id for item in select_passages(passages, 40, Counter())] == [1, 2]


def test_summary_only_updates_for_old_unsummarized_messages_over_threshold():
    messages = [msg(i, i, "user" if i % 2 else "assistant", "one two")
                for i in range(1, 9)]
    candidates = messages_to_summarize(
        messages, checkpoint=0, threshold=20, counter=Counter())
    assert [item.id for item in candidates] == [1, 2, 3, 4]
    assert messages_to_summarize(messages, 0, 30, Counter()) == []


def test_full_model_context_limit_is_enforced():
    settings = SimpleNamespace(chat_max_input_tokens=12000,
                               llm_context_window=2000,
                               chat_max_output_tokens=1200,
                               chat_safety_margin_tokens=500)
    validate_prompt_size(300, settings)
    try:
        validate_prompt_size(301, settings)
    except PromptTooLarge as error:
        assert "limit is 300" in str(error)
    else:
        raise AssertionError("oversized prompt was accepted")


def test_complete_prompt_prefers_official_provider_count():
    calls = []
    class Models:
        def count_tokens(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(total_tokens=321)
    counter = ProviderTokenCounter(
        "gemini-test", client=SimpleNamespace(models=Models()))
    result = counter.count_complete_prompt("system", [("user", "مرحبا hello")])
    assert result == CountedTokens(321, "google-api:gemini-test", estimated=False)
    assert calls[0]["model"] == "gemini-test"
