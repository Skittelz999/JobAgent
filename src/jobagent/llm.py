"""Explicit local or OpenAI transport; no automatic provider fallback."""
import copy
import os
from typing import Any, Protocol

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
                "name": "job_analysis", "strict": True, "schema": openai_schema(schema, enforce_limit=False)}},
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
            raise LLMError("LM Studio timed out; no valid result was produced. Try again or adjust llm.timeout_seconds.") from None
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


class LLMProvider(Protocol):
    settings: LLMSettings
    def generate_json(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]: ...


def openai_schema(schema: dict, *, enforce_limit: bool = True) -> dict:
    """Share source-ID enums so long ads do not repeat hundreds of enum values."""
    result = copy.deepcopy(schema)
    shared = {}
    def visit(node):
        if isinstance(node, dict):
            values = node.get("enum")
            if values and all(isinstance(v, str) and v.startswith("p") and v[1:].isdigit() for v in values):
                key = tuple(values)
                if key not in shared:
                    shared[key] = "SourcePassageId" + (str(len(shared) + 1) if shared else "")
                node.clear()
                node["$ref"] = "#/$defs/" + shared[key]
            else:
                for value in list(node.values()):
                    visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(result)
    if enforce_limit and sum(len(ids) for ids in shared) > 900:
        raise LLMError("Too many source passages for OpenAI structured output; nothing was sent.")
    for ids, name in shared.items():
        result.setdefault("$defs", {})[name] = {"type": "string", "enum": list(ids)}
    return result


class OpenAILLM:
    endpoint = "https://api.openai.com/v1/responses"

    def __init__(self, settings: LLMSettings, client: httpx.Client | None = None):
        self.settings = settings.model_copy(update={"provider": "openai", "model": settings.openai_model})
        self._client = client

    def generate_json(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise LLMError("OPENAI_API_KEY is missing. Set it in the server/CLI environment before using OpenAI.")
        payload = {"model": self.settings.model, "input": messages,
                   "max_output_tokens": self.settings.max_tokens, "store": False,
                   "text": {"format": {"type": "json_schema", "name": "job_analysis", "strict": True,
                                       "schema": openai_schema(schema)}}}
        owned = self._client is None
        client = self._client or httpx.Client(timeout=httpx.Timeout(self.settings.timeout_seconds, connect=10),
                                              follow_redirects=False, trust_env=False)
        try:
            response = client.post(self.endpoint, json=payload, headers={"Authorization": "Bearer " + key},
                                   follow_redirects=False)
        except httpx.TimeoutException:
            raise LLMError("OpenAI timed out. No valid result was produced; retry explicitly if needed.") from None
        except httpx.TransportError:
            raise LLMError("Cannot reach OpenAI. Check the network and retry explicitly.") from None
        finally:
            if owned:
                client.close()
        if response.status_code != 200:
            hints = {401: "Check OPENAI_API_KEY.", 403: "Check project/model access.",
                     429: "Check quota and rate limits; retry later.", 400: "Check model and structured-output support."}
            raise LLMError(f"OpenAI returned HTTP {response.status_code}. " + hints.get(response.status_code, "Retry later."))
        try:
            data = response.json()
            if data.get("status") != "completed":
                raise LLMError("OpenAI response was incomplete or failed. Increase token budget if needed; no partial result was accepted.")
            texts = []
            for item in data["output"]:
                if item.get("type") != "message":
                    continue
                if item.get("status") not in (None, "completed"):
                    raise LLMError("OpenAI message was incomplete; analysis rejected.")
                for part in item["content"]:
                    if part.get("type") == "refusal":
                        raise LLMError("OpenAI declined this analysis; no result was accepted.")
                    if part.get("type") == "output_text":
                        texts.append(part["text"])
            model = data["model"]
            if len(texts) != 1 or not isinstance(texts[0], str) or not texts[0] or not isinstance(model, str) or not model:
                raise ValueError
            return {"content": texts[0], "model": model}
        except LLMError:
            raise
        except (ValueError, KeyError, TypeError, AttributeError):
            raise LLMError("OpenAI returned an invalid API response.") from None


def create_provider(settings: LLMSettings) -> LLMProvider:
    return OpenAILLM(settings) if settings.provider == "openai" else LocalLLM(settings)
