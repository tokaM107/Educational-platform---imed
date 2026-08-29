"""Dependency-free provisional budgeting plus official Gemini validation."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import time

import httpx
from google.genai import errors, types


logger = logging.getLogger(__name__)
RETRYABLE = (429, 500, 502, 503, 504)


@dataclass(frozen=True)
class CountedTokens:
    count: int
    tokenizer_name: str
    estimated: bool = False


class PromptTooLarge(ValueError):
    pass


class ExactTokenCountUnavailable(RuntimeError):
    """Gemini could not validate the final prompt, so generation is unsafe."""


class ProviderTokenCounter:
    """Estimate cheaply while assembling; validate final prompts with Gemini.

    The estimate deliberately requires no local model or tokenizer. UTF-8 byte
    length divided by two is conservative for typical mixed Arabic/English
    chat: English is usually substantially over-counted and Arabic characters
    occupy multiple bytes. It is selection metadata only and is never called
    an exact Gemini token count.
    """

    ESTIMATOR_VERSION = "utf8-bytes-div-2:v1"

    def __init__(self, model_name: str, client=None, *, retry_attempts=2,
                 retry_delay_seconds=0.25):
        self.model_name = model_name
        self.client = client
        self.retry_attempts = max(1, int(retry_attempts))
        self.retry_delay_seconds = max(0.0, float(retry_delay_seconds))

    @property
    def tokenizer_name(self) -> str:
        return f"estimate:{self.ESTIMATOR_VERSION}:for:{self.model_name}"

    def count_text(self, text: str) -> CountedTokens:
        byte_count = len((text or "").encode("utf-8"))
        return CountedTokens(
            max(1, (byte_count + 1) // 2),
            self.tokenizer_name,
            estimated=True,
        )

    def count_messages(self, messages, system_instruction="") -> CountedTokens:
        total = self.count_text(system_instruction).count if system_instruction else 0
        # Conservative allowance for roles, part boundaries and separators.
        total += 8
        for role, content in messages:
            total += 8 + self.count_text(role).count + self.count_text(content).count
        return CountedTokens(total, self.tokenizer_name, estimated=True)

    def count_complete_prompt(self, system_instruction, messages) -> CountedTokens:
        """Return a provider count or fail closed after limited transient retries.

        google-genai 2.17 does not expose Developer API's
        ``generateContentRequest`` field through Models.count_tokens. When the
        real client transport is present, use that documented field so system
        instructions and role structure exactly match generation. Lightweight
        test clients can implement Models.count_tokens directly.
        """
        if self.client is None:
            raise ExactTokenCountUnavailable(
                "Gemini count_tokens client is not configured"
            )

        contents = [
            types.Content(
                role="model" if role in ("assistant", "model", "tutor") else "user",
                parts=[types.Part(text=content)],
            )
            for role, content in messages
        ]

        last_error = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                total = self._count_once(system_instruction, contents)
                return CountedTokens(
                    total,
                    f"google-api:{self.model_name}",
                    estimated=False,
                )
            except (errors.APIError, httpx.TransportError) as error:
                last_error = error
                code = getattr(error, "code", None)
                transient = code in RETRYABLE or isinstance(
                    error, httpx.TransportError
                )
                logger.warning(
                    "token_count_failed provider=gemini model=%s attempt=%s "
                    "error_type=%s status=%s transient=%s",
                    self.model_name, attempt, type(error).__name__,
                    code or "timeout", transient,
                )
                if not transient or attempt == self.retry_attempts:
                    break
                if self.retry_delay_seconds:
                    time.sleep(self.retry_delay_seconds * attempt)
            except Exception as error:
                last_error = error
                logger.warning(
                    "token_count_failed provider=gemini model=%s attempt=%s "
                    "error_type=%s status=unknown transient=false",
                    self.model_name, attempt, type(error).__name__,
                )
                break

        raise ExactTokenCountUnavailable(
            "Gemini could not validate the assembled prompt token count"
        ) from last_error

    def _count_once(self, system_instruction, contents) -> int:
        api_client = getattr(self.client, "_api_client", None)
        if api_client is not None:
            request = {
                "generateContentRequest": {
                    "model": f"models/{self.model_name}",
                    "contents": [
                        content.model_dump(
                            mode="json", by_alias=True, exclude_none=True
                        )
                        for content in contents
                    ],
                }
            }
            if system_instruction:
                request["generateContentRequest"]["systemInstruction"] = {
                    "role": "user",
                    "parts": [{"text": system_instruction}],
                }
            response = api_client.request(
                "post", f"models/{self.model_name}:countTokens", request
            )
            payload = response.body
            if isinstance(payload, (str, bytes, bytearray)):
                payload = json.loads(payload)
            if not isinstance(payload, dict) or payload.get("totalTokens") is None:
                raise ValueError("Gemini countTokens response omitted totalTokens")
            return int(payload["totalTokens"])

        # Small fake clients in unit tests use the public method as their exact
        # count seam. Production google.genai.Client always has _api_client.
        labelled_contents = list(contents)
        if system_instruction:
            labelled_contents.insert(0, types.Content(
                role="user",
                parts=[types.Part(text=(
                    "<system_instruction>\n"
                    f"{system_instruction}\n"
                    "</system_instruction>"
                ))],
            ))
        result = self.client.models.count_tokens(
            model=self.model_name,
            contents=labelled_contents,
        )
        if result.total_tokens is None:
            raise ValueError("Gemini count_tokens response omitted total_tokens")
        return int(result.total_tokens)


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
