"""Google Gemini API (aistudio.google.com/docs).

Answers and embeddings go through Gemini's OpenAI-compatible endpoint
(`gemini.base_url`, API key as a Bearer token), so the tested OpenAICompatClient does the
work: JSON schema in `response_format`, the same fallbacks, the same parsing. This module
adds what is specific to Gemini:

- the model list from the native API (`GET gemini.models_url`, key in `x-goog-api-key`,
  paged with pageSize/pageToken), split into answer models (generateContent) and embedding
  models (embedContent), each marked free tier or not (`gemini.free_tier_models`);
- `reasoning_effort` (dropped if a model refuses it) and `max_output_tokens`, because
  thinking tokens count towards the output limit;
- free-tier rate limits: a 429 waits and retries `gemini.rate_limit_retries` times.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx

from .config import Config
from .env import Endpoint
from .ollama_client import OllamaError
from .openai_client import OpenAICompatClient


MODEL_LIST_SECONDS = 60


class GeminiClient(OpenAICompatClient):
    def __init__(self, config: Config, endpoint: Endpoint, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(config, endpoint, transport)
        self._transport = transport
        self._reasoning = config.gemini.reasoning_effort.strip()
        self._models: tuple[float, list[dict[str, Any]]] | None = None

    def _max_tokens(self) -> int:
        return self.config.gemini.max_output_tokens

    def _extra_payload(self) -> dict[str, Any]:
        return {"reasoning_effort": self._reasoning} if self._reasoning else {}

    def _drop_unsupported(self, resp: httpx.Response, payload: dict[str, Any]) -> bool:
        # Some models cannot change (or turn off) thinking; then use the model's default.
        if resp.status_code == 400 and self._reasoning and re.search(r"reasoning|thinking", resp.text, re.I):
            self._reasoning = ""
            payload.pop("reasoning_effort", None)
            return True
        return False

    async def _post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        g = self.config.gemini
        for attempt in range(g.rate_limit_retries + 1):
            resp = await self._request("POST", path, payload)
            if resp.status_code != 429 or attempt == g.rate_limit_retries:
                break
            await asyncio.sleep(_retry_after(resp, g.rate_limit_wait_seconds))
        if resp.status_code == 429:
            raise OllamaError(rate_limit_message(resp))
        return resp

    async def list_models(self) -> list[dict[str, Any]]:
        # The health check runs every 15 s; the model list rarely changes, so keep it a minute.
        if self._models and time.monotonic() - self._models[0] < MODEL_LIST_SECONDS:
            return self._models[1]
        key = self.endpoint.api_key.get_secret_value() if self.endpoint.api_key else ""
        models = await list_gemini_models(self.config, key, self._transport)
        self._models = (time.monotonic(), models)
        return models


def _retry_after(resp: httpx.Response, default: float) -> float:
    """Seconds to wait: the Retry-After header, or Google's `retryDelay` ("17s"), capped at
    the configured wait."""
    raw = resp.headers.get("retry-after")
    m = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', resp.text)
    try:
        wait = float(raw) if raw else float(m.group(1)) if m else default
    except ValueError:
        wait = default
    return max(0.0, min(wait, default))


def rate_limit_message(resp: httpx.Response) -> str:
    daily = "PerDay" in resp.text or "per day" in resp.text.lower()
    return ("Gemini free-tier limit reached (HTTP 429). " +
            ("The daily request limit is used up; it resets at midnight Pacific time." if daily else
             "Too many requests per minute; wait a minute and ask again.") +
            " Limits are per Google Cloud project: see aistudio.google.com/rate-limit.")


async def list_gemini_models(config: Config, api_key: str, transport: httpx.AsyncBaseTransport | None = None,
                             models_url: str | None = None) -> list[dict[str, Any]]:
    """Every model the key can use at models_url (default: gemini.models_url), as
    {name, display_name, kind, free_tier, ...}. Reads Google's list ({"models": [...]},
    supportedGenerationMethods, paged) and the OpenAI one ({"data": [{"id": ...}]}).
    free_tier is known for Google only (gemini.free_tier_models); otherwise None."""
    from .config import is_google_url

    if not api_key:
        raise OllamaError("No API key yet. Add it in Settings, AI models.")
    g = config.gemini
    url = (models_url or g.models_url).strip().rstrip("/")
    google = is_google_url(url)
    headers = {"x-goog-api-key": api_key} if google else {"Authorization": f"Bearer {api_key}"}
    out: list[dict[str, Any]] = []
    token = ""
    async with httpx.AsyncClient(timeout=httpx.Timeout(config.ollama.timeout_seconds), transport=transport,
                                 headers=headers) as client:
        for _ in range(20):  # 1000 per page; 20 pages is far more than any service lists
            params = ({"pageSize": 1000} | ({"pageToken": token} if token else {})) if google else {}
            try:
                resp = await client.get(url, params=params)
            except httpx.HTTPError as exc:
                raise OllamaError(f"Cannot reach {url}: {exc}") from exc
            if resp.status_code in (400, 401, 403):
                raise OllamaError("The API refused this API key"
                                  + (". Check it at aistudio.google.com/api-keys." if google else " for this URL."))
            if resp.status_code == 404:
                raise OllamaError(f"Nothing found at {url}. Check the models URL.")
            if resp.status_code == 429:
                raise OllamaError(rate_limit_message(resp))
            if resp.status_code != 200:
                raise OllamaError(f"{url} returned HTTP {resp.status_code} for the model list.")
            try:
                data = resp.json()
            except ValueError as exc:
                raise OllamaError(f"{url} did not return a model list. Check the models URL.") from exc
            items = data.get("models", data.get("data")) if isinstance(data, dict) else data
            if not isinstance(items, list):
                raise OllamaError(f"{url} did not return a model list. Check the models URL.")
            for m in items:
                if not isinstance(m, dict):
                    continue
                if google:
                    methods = m.get("supportedGenerationMethods") or []
                    kind = "answer" if "generateContent" in methods else "embedding" if "embedContent" in methods else None
                    name = str(m.get("name", "")).removeprefix("models/")
                else:
                    name = str(m.get("id") or m.get("name") or "")
                    kind = "embedding" if "embed" in name.lower() else "answer"
                if kind and name:
                    out.append({"name": name, "model": name, "display_name": m.get("displayName") or name,
                                "kind": kind, "free_tier": g.is_free_tier(name) if google else None,
                                "input_token_limit": m.get("inputTokenLimit"),
                                "output_token_limit": m.get("outputTokenLimit")})
            token = (data.get("nextPageToken") or "") if google and isinstance(data, dict) else ""
            if not token:
                break
    return out
