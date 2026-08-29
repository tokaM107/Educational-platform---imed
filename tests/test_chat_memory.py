from types import SimpleNamespace
import inspect

import httpx
import pytest

from app.services.chat_memory import (
    MemoryMessage, messages_to_summarize, select_passages, select_recent_turns,
)
from app.services import token_budget
from app.services.token_budget import (
    CountedTokens, ExactTokenCountUnavailable, PromptTooLarge,
    ProviderTokenCounter, validate_prompt_size,
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
    assert "config" not in calls[0]
    assert calls[0]["contents"][0].parts[0].text == (
        "<system_instruction>\nsystem\n</system_instruction>"
    )


def test_real_client_shape_counts_exact_generate_content_request():
    calls = []
    class ApiClient:
        def request(self, method, path, body):
            calls.append((method, path, body))
            return SimpleNamespace(body={"totalTokens": 41})
    client = SimpleNamespace(_api_client=ApiClient())
    counter = ProviderTokenCounter("gemini-test", client)

    counted = counter.count_complete_prompt(
        "system", [("assistant", "prior answer"), ("user", "question")]
    )

    request = calls[0][2]["generateContentRequest"]
    assert counted == CountedTokens(41, "google-api:gemini-test", False)
    assert calls[0][:2] == ("post", "models/gemini-test:countTokens")
    assert request["model"] == "models/gemini-test"
    assert request["systemInstruction"]["parts"][0]["text"] == "system"
    assert [content["role"] for content in request["contents"]] == [
        "model", "user"
    ]


def test_provider_count_failure_fails_closed_without_local_fallback():
    class BrokenModels:
        def count_tokens(self, **kwargs):
            raise OSError("temporary provider failure")

    counter = ProviderTokenCounter(
        "gemini-test", SimpleNamespace(models=BrokenModels())
    )
    with pytest.raises(ExactTokenCountUnavailable):
        counter.count_complete_prompt("system", [("user", "hello " * 30)])


def test_provisional_estimator_has_no_local_model_or_huggingface_dependency():
    source = inspect.getsource(token_budget)
    counter = ProviderTokenCounter("gemini-4-test")
    passages = [
        SimpleNamespace(chunk_id=1, text="سري للغاية pneumatic bones", distance=.1),
        SimpleNamespace(chunk_id=2, text="second passage", distance=.2),
    ]

    selected = select_passages(passages, budget=200, counter=counter)
    counted = counter.count_text("عظمة pneumatic bone")

    assert [item.chunk_id for item in selected] == [1, 2]
    assert counted.estimated is True
    assert counted.tokenizer_name.startswith("estimate:utf8-bytes-div-2:v1")
    assert "local_tokenizer" not in source
    assert "transformers" not in source
    assert "torchvision" not in source


@pytest.mark.parametrize("network_error", [
    httpx.ReadTimeout("temporary timeout"),
    httpx.ConnectError("temporary DNS/connect failure"),
])
def test_official_count_retries_a_transient_transport_error_then_succeeds(
    network_error,
):
    class Models:
        calls = 0
        def count_tokens(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise network_error
            return SimpleNamespace(total_tokens=42)

    models = Models()
    counter = ProviderTokenCounter(
        "gemini-test", SimpleNamespace(models=models),
        retry_attempts=2, retry_delay_seconds=0,
    )

    counted = counter.count_complete_prompt("system", [("user", "question")])

    assert counted == CountedTokens(42, "google-api:gemini-test", False)
    assert models.calls == 2
