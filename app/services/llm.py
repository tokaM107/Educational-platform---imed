"""Thin wrapper around Gemini text generation.

Flash models return 503 when they are busy and 429 when the free-tier quota is
hit, so every call is retried and then retried once more on a fallback model
before giving up.
"""

import logging
import time

from google import genai
from google.genai import errors
from google.genai import types

from app.config import get_settings


logger = logging.getLogger(__name__)

RETRYABLE = (429, 500, 502, 503, 504)


class LLMUnavailable(RuntimeError):
    """Every model and retry failed. Callers can still show the video segment."""


class ChatModel:

    def __init__(self, settings=None, client=None):

        self.settings = settings or get_settings()

        self.client = client or genai.Client(
            api_key=self.settings.require_gemini_api_key()
        )

    def generate(self, system_instruction, user_prompt, response_schema, history=None,
                 max_output_tokens=2048):
        """One grounded reply, validated against `response_schema`.

        `history` is [(role, text), ...] oldest first.

        `max_output_tokens` covers thinking as well as the reply, and a
        truncated answer is not parsable JSON — it comes back as no reply at
        all. A few sentences of tutoring fit in the default; a whole weekly
        report does not, so callers with a larger reply must raise it.
        """

        contents = self._build_contents(user_prompt, history)

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            # Low temperature: the job is to restate the lecture, not to be
            # creative with it.
            temperature=0.2,
            max_output_tokens=max_output_tokens,
            response_mime_type="application/json",
            response_schema=response_schema,
        )

        models = [self.settings.chat_model]

        if self.settings.chat_fallback_model != self.settings.chat_model:
            models.append(self.settings.chat_fallback_model)

        last_error = None

        for model in models:

            for attempt in range(2):

                try:
                    response = self.client.models.generate_content(
                        model=model,
                        contents=contents,
                        config=config,
                    )

                    if response.parsed is None:
                        raise LLMUnavailable(
                            f"{model} returned no parsable reply"
                        )

                    return response.parsed

                except errors.APIError as error:

                    last_error = error

                    if getattr(error, "code", None) not in RETRYABLE:
                        raise

                    logger.warning(
                        "chat model %s failed (%s), attempt %s",
                        model,
                        getattr(error, "code", "?"),
                        attempt + 1,
                    )

                    time.sleep(1.5 * (attempt + 1))

        raise LLMUnavailable(str(last_error))

    def _build_contents(self, user_prompt, history):

        contents = []

        for role, text in history or []:

            contents.append(
                types.Content(
                    # Gemini only knows "user" and "model"
                    role="model" if role in ("tutor", "model", "assistant") else "user",
                    parts=[types.Part(text=text)],
                )
            )

        contents.append(
            types.Content(role="user", parts=[types.Part(text=user_prompt)])
        )

        return contents
