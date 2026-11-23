"""Client for any OpenAI-compatible /v1 API: LM Studio, llama.cpp server, vLLM, LocalAI,
or a hosted service (only with EVIDEX_ALLOW_REMOTE=true). Same interface as OllamaClient.

Structured output: the answer JSON schema goes in `response_format` (json_schema). If a
server does not support that, the client falls back to json_object, then to plain
output, with the schema in the instructions; the code still parses and checks the JSON.
Ollama's num_ctx and keep_alive do not exist in this API: the server's own settings apply.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .config import Config
from .env import Endpoint
from .ollama_client import ChatResult, ModelClient, OllamaError

# json_schema (OpenAI, vLLM, LM Studio) -> json_object with the schema attached (llama.cpp
# enforces it as a grammar) -> plain json_object -> no response_format.
_MODES = ("json_schema", "json_object_schema", "json_object", "none")


class OpenAICompatClient(ModelClient):
    def __init__(self, config: Config, endpoint: Endpoint, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self.endpoint = endpoint
        self._client = self._http(endpoint, transport)
        self._mode = 0  # index into _MODES; lowered once if the server rejects a mode

    async def _request(self, method: str, path: str, payload: dict | None = None) -> httpx.Response:
        try:
            return await self._client.request(method, path, json=payload)
        except httpx.TimeoutException as exc:
            raise OllamaError(f"{self.endpoint.label} did not answer within "
                              f"{self.config.ollama.timeout_seconds:g} s") from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"Cannot reach {self.endpoint.label} at {self.endpoint.base_url}: {exc}") from exc

    async def list_models(self) -> list[dict[str, Any]]:
        resp = await self._request("GET", "/models")
        if resp.status_code != 200:
            raise self._status_error(resp)
        data = resp.json()
        items = data.get("data", data.get("models", [])) if isinstance(data, dict) else data
        out = []
        for m in items or []:
            name = m.get("id") or m.get("name") if isinstance(m, dict) else str(m)
            if name:
                out.append({"name": name, "model": name})
        return out

    async def chat(self, messages: list[dict[str, str]], *, schema: dict[str, Any] | None = None,
                   model: str | None = None, **option_overrides: Any) -> ChatResult:
        o = self.config.ollama
        base: dict[str, Any] = {
            "model": model or self.config.models.answer,
            "messages": messages,
            "temperature": option_overrides.get("temperature", o.temperature),
            "max_tokens": option_overrides.get("num_predict", o.num_predict),
            "stream": False,
        }
        t0 = time.perf_counter()
        while True:
            mode = _MODES[self._mode] if schema is not None else "none"
            payload = dict(base)
            if schema is not None:
                payload["messages"] = self._with_schema_note(messages, schema)
                if mode == "json_schema":
                    payload["response_format"] = {"type": "json_schema",
                                                  "json_schema": {"name": "response", "schema": schema}}
                elif mode == "json_object_schema":
                    payload["response_format"] = {"type": "json_object", "schema": schema}
                elif mode == "json_object":
                    payload["response_format"] = {"type": "json_object"}
            resp = await self._request("POST", "/chat/completions", payload)
            # Servers reject an unsupported response_format with 400, 422 or (llama.cpp) 500.
            if resp.status_code in (400, 422, 500) and schema is not None and self._mode < len(_MODES) - 1 \
                    and ("response_format" in resp.text or "json" in resp.text.lower()):
                self._mode += 1  # this server does not support that output mode; try the next one
                continue
            if resp.status_code != 200:
                raise self._status_error(resp)
            break
        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise OllamaError(f"{self.endpoint.label} returned an unexpected chat response") from exc
        usage = data.get("usage") or {}
        ms = (time.perf_counter() - t0) * 1000
        return ChatResult(content=_strip_fences(content), model=data.get("model", base["model"]),
                          prompt_tokens=int(usage.get("prompt_tokens", 0)),
                          output_tokens=int(usage.get("completion_tokens", 0)),
                          total_ms=ms, prompt_eval_ms=0.0, eval_ms=ms)

    @staticmethod
    def _with_schema_note(messages: list[dict[str, str]], schema: dict[str, Any]) -> list[dict[str, str]]:
        note = {"role": "system", "content": "Reply with one JSON object only, no other text, matching this JSON "
                                             "schema: " + json.dumps(schema)}
        return [note, *messages]

    async def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        if not texts:
            return []
        resp = await self._request("POST", "/embeddings", {"model": model or self.config.models.embedding,
                                                           "input": texts})
        if resp.status_code != 200:
            raise self._status_error(resp)
        items = (resp.json() or {}).get("data")
        if not isinstance(items, list) or len(items) != len(texts):
            raise OllamaError(f"{self.endpoint.label} returned an unexpected embedding response")
        items = sorted(items, key=lambda d: d.get("index", 0))
        return [d["embedding"] for d in items]


def _strip_fences(text: str) -> str:
    """Some servers wrap JSON in ```json fences when no response_format is enforced."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()
