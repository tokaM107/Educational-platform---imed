"""Provider-aware counting and deterministic token-budget selection."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import logging

import tiktoken
from google.genai import local_tokenizer, types


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CountedTokens:
    count: int
    tokenizer_name: str
    estimated: bool = False


class PromptTooLarge(ValueError):
    pass


class ProviderTokenCounter:
    """Prefer Gemini's tokenizer; use a conservative BPE estimate if absent.

    The fallback is explicitly named and inflated, so a count made for one
    tokenizer is never mistaken for an exact count for another model.
    """

    def __init__(self, model_name: str, client=None):
        self.model_name = model_name
        self.client = client
        self._local = None
        self._local_attempted = False
        self._fallback = tiktoken.get_encoding("o200k_base")

    @property
    def tokenizer_name(self) -> str:
        if self._local is not None:
            return f"google-genai-local:{self.model_name}"
        return f"tiktoken:o200k_base:conservative-for:{self.model_name}"

    def _local_tokenizer(self):
        if not self._local_attempted:
            self._local_attempted = True
            try:
                self._local = local_tokenizer.LocalTokenizer(self.model_name)
            except (ConnectionError, OSError, ValueError) as error:
                logger.warning("Gemini local tokenizer unavailable: %s", error)
        return self._local

    def count_text(self, text: str) -> CountedTokens:
        tokenizer = self._local_tokenizer()
        if tokenizer is not None:
            result = tokenizer.count_tokens(text or "")
            return CountedTokens(result.total_tokens or 0, self.tokenizer_name)

        # BPE is tokenizer-based (not characters/words), but not Gemini-exact.
        # Inflate by 25% for safe mixed Arabic/English budgeting.
        raw = len(self._fallback.encode(text or ""))
        return CountedTokens(
            max(1, (raw * 5 + 3) // 4), self.tokenizer_name, estimated=True
        )

    def count_messages(self, messages, system_instruction="") -> CountedTokens:
        total = self.count_text(system_instruction).count if system_instruction else 0
        estimated = self._local is None
        # Gemini message framing/role separators. Kept explicit so history does
        # not consume the transcript budget invisibly.
        total += 4
        for role, content in messages:
            total += 4 + self.count_text(role).count + self.count_text(content).count
        return CountedTokens(total, self.tokenizer_name, estimated)

    def count_complete_prompt(self, system_instruction, messages) -> CountedTokens:
        """Use the provider endpoint when available, otherwise conservative local."""
        if self.client is not None:
            contents = [
                types.Content(
                    role="model" if role == "assistant" else "user",
                    parts=[types.Part(text=content)],
                )
                for role, content in messages
            ]
            try:
                result = self.client.models.count_tokens(
                    model=self.model_name,
                    contents=contents,
                    config=types.CountTokensConfig(
                        system_instruction=system_instruction
                    ),
                )
                return CountedTokens(
                    int(result.total_tokens or 0),
                    f"google-api:{self.model_name}",
                    estimated=False,
                )
            except Exception as error:  # counting must degrade, never lose a chat
                logger.warning("provider token count failed: %s", error)
        return self.count_messages(messages, system_instruction)


def effective_input_limit(settings) -> int:
    return min(
        settings.chat_max_input_tokens,
        settings.llm_context_window
        - settings.chat_max_output_tokens
        - settings.chat_safety_margin_tokens,
    )


def validate_prompt_size(count: int, settings) -> None:
    limit = effective_input_limit(settings)
    if count > limit:
        raise PromptTooLarge(f"assembled prompt uses {count} tokens; limit is {limit}")
