"""LLM client abstraction for MurmurFS.

Provides a pluggable interface for LLM calls used during squash, sync, and merge.
Includes retry logic with exponential backoff and configurable timeouts.
"""

from __future__ import annotations

import os
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import requests

from murmurfs.config import Config


class LLMError(Exception):
    """Raised when an LLM call fails after all retries."""


@dataclass
class LLMResponse:
    """LLM response with text content and token usage."""

    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @property
    def usage(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


class LLMClient(ABC):
    """Abstract base for LLM completions."""

    @abstractmethod
    def complete(self, prompt: str, system: str = "") -> str:
        """Send a prompt and return the LLM's text response."""
        ...

    def complete_with_usage(self, prompt: str, system: str = "") -> LLMResponse:
        """Send a prompt and return an LLMResponse with token usage.

        Default implementation wraps complete() with zero usage.
        Subclasses should override to provide real token counts.
        """
        return LLMResponse(text=self.complete(prompt, system))


class OpenAILLMClient(LLMClient):
    """LLM client using an OpenAI-compatible HTTP API.

    Configuration via Config object or environment variables:
        MURMURFS_LLM_BASE_URL  (default: https://api.openai.com/v1)
        MURMURFS_LLM_MODEL     (default: gpt-4o)
        MURMURFS_LLM_API_KEY   (required)

    Retry: up to max_retries (default 3) with exponential backoff.
    Timeout: configurable, default 60 seconds.
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: int | None = None,
        max_retries: int | None = None,
        config: Config | None = None,
    ):
        llm_cfg = config.llm if config else None
        self.base_url = (
            base_url
            or (llm_cfg.base_url if llm_cfg else None)
            or os.environ.get("MURMURFS_LLM_BASE_URL", "https://api.openai.com/v1")
        ).rstrip("/")
        self.model = (
            model
            or (llm_cfg.model if llm_cfg else None)
            or os.environ.get("MURMURFS_LLM_MODEL", "gpt-4o")
        )
        # Resolve API key: explicit > env var from config > default env var
        if api_key:
            self.api_key = api_key
        else:
            env_name = llm_cfg.api_key_env if llm_cfg else "MURMURFS_LLM_API_KEY"
            self.api_key = os.environ.get(env_name, "")

        self.timeout = (
            timeout
            or (llm_cfg.timeout if llm_cfg else None)
            or int(os.environ.get("MURMURFS_LLM_TIMEOUT", "60"))
        )
        self.max_retries = (
            max_retries
            or (llm_cfg.max_retries if llm_cfg else None)
            or int(os.environ.get("MURMURFS_LLM_MAX_RETRIES", "3"))
        )

    def complete(self, prompt: str, system: str = "") -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_exception: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.post(
                    url,
                    json={
                        "model": self.model,
                        "messages": messages,
                    },
                    headers=headers,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]

            except requests.exceptions.Timeout as e:
                last_exception = e
                wait = 2 ** (attempt - 1)
                print(
                    f"LLM request timed out (attempt {attempt}/{self.max_retries}), "
                    f"retrying in {wait}s...",
                    file=sys.stderr,
                )
                if attempt < self.max_retries:
                    time.sleep(wait)

            except requests.exceptions.ConnectionError as e:
                last_exception = e
                wait = 2 ** (attempt - 1)
                print(
                    f"LLM connection failed (attempt {attempt}/{self.max_retries}): {e}",
                    file=sys.stderr,
                )
                if attempt < self.max_retries:
                    time.sleep(wait)

            except requests.exceptions.HTTPError as e:
                last_exception = e
                status = e.response.status_code if e.response is not None else "unknown"
                # Don't retry on 4xx client errors (except 429 rate limit)
                if e.response is not None and 400 <= e.response.status_code < 500 and e.response.status_code != 429:
                    raise LLMError(
                        f"LLM API returned client error (HTTP {status}): {e}"
                    ) from e
                wait = 2 ** (attempt - 1)
                print(
                    f"LLM API error HTTP {status} (attempt {attempt}/{self.max_retries}), "
                    f"retrying in {wait}s...",
                    file=sys.stderr,
                )
                if attempt < self.max_retries:
                    time.sleep(wait)

            except requests.exceptions.RequestException as e:
                last_exception = e
                wait = 2 ** (attempt - 1)
                print(
                    f"LLM request failed (attempt {attempt}/{self.max_retries}): {e}",
                    file=sys.stderr,
                )
                if attempt < self.max_retries:
                    time.sleep(wait)

        raise LLMError(
            f"LLM call failed after {self.max_retries} attempts: {last_exception}"
        ) from last_exception

    def _call_with_usage(self, prompt: str, system: str = "") -> LLMResponse:
        """Internal: make the API call and return an LLMResponse with token counts."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_exception: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.post(
                    url,
                    json={
                        "model": self.model,
                        "messages": messages,
                    },
                    headers=headers,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                return LLMResponse(
                    text=text,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                )

            except requests.exceptions.Timeout as e:
                last_exception = e
                wait = 2 ** (attempt - 1)
                if attempt < self.max_retries:
                    time.sleep(wait)

            except requests.exceptions.ConnectionError as e:
                last_exception = e
                wait = 2 ** (attempt - 1)
                if attempt < self.max_retries:
                    time.sleep(wait)

            except requests.exceptions.HTTPError as e:
                last_exception = e
                if e.response is not None and 400 <= e.response.status_code < 500 and e.response.status_code != 429:
                    raise LLMError(
                        f"LLM API returned client error (HTTP {e.response.status_code}): {e}"
                    ) from e
                wait = 2 ** (attempt - 1)
                if attempt < self.max_retries:
                    time.sleep(wait)

            except requests.exceptions.RequestException as e:
                last_exception = e
                wait = 2 ** (attempt - 1)
                if attempt < self.max_retries:
                    time.sleep(wait)

        raise LLMError(
            f"LLM call failed after {self.max_retries} attempts: {last_exception}"
        ) from last_exception

    def complete_with_usage(self, prompt: str, system: str = "") -> LLMResponse:
        """Send a prompt and return an LLMResponse with token usage."""
        return self._call_with_usage(prompt, system)


class MockLLMClient(LLMClient):
    """Mock LLM client for testing.

    Usage:
        client = MockLLMClient(responses={"squash": "SUMMARY: ...\\nFULL: ..."})
        # or with a callable:
        client = MockLLMClient(responses=lambda prompt, system: "response")

    Falls back to a default response if no match is found.
    """

    def __init__(
        self,
        responses: dict[str, str] | None = None,
        default: str | None = None,
        _handler: callable | None = None,
    ):
        self.responses = responses or {}
        self.default = default or "SUMMARY: mock summary\nFULL: mock full intent"
        self._handler = _handler
        self.calls: list[dict] = []

    def complete(self, prompt: str, system: str = "") -> str:
        self.calls.append({"prompt": prompt, "system": system})

        if self._handler is not None:
            return self._handler(prompt, system)

        # Try to match by keyword
        prompt_lower = prompt.lower()
        for key, response in self.responses.items():
            if key.lower() in prompt_lower:
                return response

        return self.default

    def complete_with_usage(self, prompt: str, system: str = "") -> LLMResponse:
        """Return mock response with simulated token counts."""
        text = self.complete(prompt, system)
        prompt_tokens = len(prompt.split()) * 2  # rough estimate
        completion_tokens = len(text.split()) * 2
        return LLMResponse(
            text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )

    @classmethod
    def with_handler(cls, handler: callable) -> MockLLMClient:
        """Create a MockLLMClient with a callable handler."""
        return cls(_handler=handler)
