"""OpenAI-compatible local transport, separate from collection and storage."""
from typing import Any

import httpx

from jobagent.settings import LLMSettings


class LLMError(ValueError):
    """Safe diagnostic without exposing prompts or response bodies."""


class LocalLLM:
    def __init__(self, settings: LLMSettings, client: httpx.Client | None = None):
        self.settings = settings
        self._client = client

    def generate_json(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "model": self.settings.model, "messages": messages, "temperature": 0,
            "max_tokens": self.settings.max_tokens, "stream": False,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "job_analysis", "strict": True, "schema": schema}},
        }
        # No tools, redirects, proxy environment, implicit downloads or cloud fallback.
        owned = self._client is None
        client = self._client or httpx.Client(
            timeout=httpx.Timeout(self.settings.timeout_seconds, connect=5),
            follow_redirects=False, trust_env=False,
        )
        try:
            response = client.post(self.settings.base_url + "/chat/completions", json=payload)
        except httpx.TimeoutException:
            raise LLMError("LM Studio timed out; no analysis was saved. Try again or adjust llm.timeout_seconds.") from None
        except httpx.TransportError:
            raise LLMError("Cannot reach LM Studio. Start its local API server and load the configured model.") from None
        finally:
            if owned:
                client.close()
        if response.status_code != 200:
            raise LLMError(f"LM Studio returned HTTP {response.status_code}; check model, context size and JSON schema support.")
        try:
            data = response.json()
            choice = data["choices"][0]
            if choice["finish_reason"] != "stop":
                raise LLMError("LM Studio response was incomplete; increase token/context budget or use a shorter ad.")
            content = choice["message"]["content"]
            model = data["model"]
            if not isinstance(content, str) or not isinstance(model, str) or not model:
                raise TypeError
            return {"content": content, "model": model}
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            if isinstance(exc, LLMError):
                raise
            raise LLMError("LM Studio returned an invalid API response.") from None
