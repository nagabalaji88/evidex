"""Environment settings from a .env file (repository root, git-ignored) and real
environment variables. See .env.example for every variable.

- Real environment variables win over the .env file.
- Values set here override the matching config.yaml values (the Settings page shows which).
- API keys live only here, never in config.yaml, and are never returned by the API.
- Everything is local by default: an endpoint on a public internet host is refused unless
  EVIDEX_ALLOW_REMOTE=true (CLAUDE.md: never call cloud AI APIs unless you choose to).
"""

from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, SecretStr

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE_VAR = "EVIDEX_ENV_FILE"

Provider = Literal["ollama", "openai", "gemini"]
# Where the Settings page saves the Gemini API key. GEMINI_API_KEY (the name Google's docs use)
# is read too, if the first is not set.
GEMINI_KEY_VARS = ("EVIDEX_GEMINI_API_KEY", "GEMINI_API_KEY")

# EVIDEX_* variable -> (config section, key, type)
OVERRIDES: dict[str, tuple[str, str, type]] = {
    "EVIDEX_ANSWER_MODEL": ("models", "answer", str),
    "EVIDEX_EMBEDDING_MODEL": ("models", "embedding", str),
    "EVIDEX_EMBEDDING_QUERY_PREFIX": ("models", "embedding_query_prefix", str),
    "EVIDEX_EMBEDDING_DOC_PREFIX": ("models", "embedding_doc_prefix", str),
    "EVIDEX_OLLAMA_BASE_URL": ("ollama", "base_url", str),
    "EVIDEX_TIMEOUT_SECONDS": ("ollama", "timeout_seconds", float),
    "EVIDEX_NUM_CTX": ("ollama", "num_ctx", int),
    "EVIDEX_RERANKER_ENABLED": ("reranker", "enabled", bool),
    "EVIDEX_RERANKER_MODEL": ("reranker", "model", str),
    "EVIDEX_VECTOR_STORE": ("storage", "vector_store", str),
    "EVIDEX_SQLITE": ("storage", "sqlite", str),
}

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


class EnvError(ValueError):
    pass


class Endpoint(BaseModel):
    """Where a model runs: Ollama's native API, or any OpenAI-compatible /v1 API
    (LM Studio, llama.cpp server, vLLM, LocalAI, or a hosted service)."""

    model_config = ConfigDict(frozen=True)

    provider: Provider = "ollama"
    base_url: str
    api_key: SecretStr | None = None

    @property
    def host(self) -> str:
        return urlparse(self.base_url).netloc or self.base_url

    @property
    def label(self) -> str:
        if self.provider == "gemini":
            return "Google Gemini API"
        return "Ollama" if self.provider == "ollama" else f"OpenAI-compatible · {self.host}"

    def headers(self) -> dict[str, str]:
        key = self.api_key.get_secret_value() if self.api_key else ""
        return {"Authorization": f"Bearer {key}"} if key else {}

    def public(self) -> dict[str, Any]:
        """Safe to show: never includes the key itself."""
        return {"provider": self.provider, "base_url": self.base_url, "label": self.label,
                "api_key_set": bool(self.api_key and self.api_key.get_secret_value())}


def gemini_api_key(env: dict[str, str]) -> SecretStr | None:
    for name in GEMINI_KEY_VARS:
        value = (env.get(name) or "").strip()
        if value:
            return SecretStr(value)
    return None


def env_file_path() -> Path:
    return Path(os.environ.get(ENV_FILE_VAR, REPO_ROOT / ".env"))


def parse_env_text(text: str) -> dict[str, str]:
    """KEY=VALUE lines; # comments; optional `export `; single or double quotes."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
            if raw.strip().endswith('"'):
                value = value.replace('\\"', '"').replace("\\n", "\n")
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()  # trailing comment
        out[key] = value
    return out


def set_env_value(name: str, value: str | None, path: Path | None = None) -> Path:
    """Write NAME=value into the .env file (created if missing; other lines and comments kept),
    or remove the line when value is None. Also updates os.environ, so it applies at once."""
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", name):
        raise EnvError(f"not a variable name: {name!r}")
    if value is not None and ("\n" in value or "\r" in value):
        raise EnvError(f"{name} must be one line")
    path = path or env_file_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    line_re = re.compile(rf"^\s*(export\s+)?{name}\s*=")
    new = f'{name}="{value}"' if value is not None else None
    out, done = [], False
    for line in lines:
        if line_re.match(line):
            if new is not None and not done:
                out.append(new)
            done = True
            continue
        out.append(line)
    if new is not None and not done:
        out.append(new)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    return path


def load_env_file(path: Path | None = None) -> dict[str, str]:
    """Read the .env file and put its values into os.environ without overriding real
    environment variables (so HF_TOKEN and similar also reach their libraries)."""
    path = path or env_file_path()
    if not path.is_file():
        return {}
    values = parse_env_text(path.read_text(encoding="utf-8"))
    for k, v in values.items():
        os.environ.setdefault(k, v)
    return values


def _coerce(name: str, value: str, typ: type) -> Any:
    if typ is bool:
        low = value.strip().lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise EnvError(f"{name} must be true or false, not {value!r}")
    if typ in (int, float):
        try:
            return typ(value)
        except ValueError as exc:
            raise EnvError(f"{name} must be a number, not {value!r}") from exc
    return value


def apply_overrides(data: dict, env: dict[str, str]) -> tuple[dict, list[str]]:
    """Return config data with EVIDEX_* overrides applied, and the dotted keys overridden.
    A variable set to an empty string counts (e.g. no embedding prefixes)."""
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in data.items()}
    changed: list[str] = []
    for name, (section, key, typ) in OVERRIDES.items():
        if name in env:
            value = env[name]
            if typ is not str and value.strip() == "" and typ is not bool:
                continue  # empty number: keep config.yaml
            out.setdefault(section, {})[key] = _coerce(name, value, typ)
            changed.append(f"{section}.{key}")
    return out, changed


def is_local_url(url: str) -> bool:
    """localhost, loopback, private or link-local addresses, and *.local / *.lan /
    *.internal / *.home.arpa names count as local. Anything else is a remote host."""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    if host in ("localhost", "host.docker.internal") or host.endswith((".localhost", ".local", ".lan", ".internal",
                                                                       ".home.arpa")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def _endpoint(prefix: str, env: dict[str, str], default: Endpoint) -> Endpoint:
    provider = (env.get(f"{prefix}_PROVIDER") or "").strip().lower() or None
    base = (env.get(f"{prefix}_BASE_URL") or "").strip() or None
    key = env.get(f"{prefix}_API_KEY")
    if provider is None and base is None and key is None:
        return default
    provider = provider or default.provider
    if provider not in ("ollama", "openai"):
        raise EnvError(f"{prefix}_PROVIDER must be 'ollama' or 'openai', not {provider!r}")
    if base is None:
        if provider != default.provider:
            raise EnvError(f"{prefix}_BASE_URL is required when {prefix}_PROVIDER={provider}, "
                           "e.g. http://localhost:1234/v1 for LM Studio")
        base = default.base_url
    if not re.match(r"^https?://", base):
        raise EnvError(f"{prefix}_BASE_URL must start with http:// or https://, not {base!r}")
    base = base.rstrip("/")
    # Inherit the default's key only for the very same server: never send a key to another host.
    inherited = default.api_key if base == default.base_url.rstrip("/") else None
    api_key = SecretStr(key) if key else (inherited if key is None else None)
    return Endpoint(provider=provider, base_url=base, api_key=api_key)


def endpoints(env: dict[str, str], ollama_base_url: str) -> tuple[Endpoint, Endpoint]:
    """(chat/answer endpoint, embedding endpoint). Embeddings default to the chat endpoint."""
    llm = _endpoint("EVIDEX_LLM", env, Endpoint(provider="ollama", base_url=ollama_base_url.rstrip("/")))
    emb = _endpoint("EVIDEX_EMBED", env, llm)
    allow_remote = _coerce("EVIDEX_ALLOW_REMOTE", env.get("EVIDEX_ALLOW_REMOTE", "false"), bool)
    for name, ep in (("EVIDEX_LLM_BASE_URL", llm), ("EVIDEX_EMBED_BASE_URL", emb)):
        if not allow_remote and not is_local_url(ep.base_url):
            raise EnvError(
                f"{name} points to a remote host ({ep.host}). Evidex runs locally by default; set "
                "EVIDEX_ALLOW_REMOTE=true in .env if you really want to send your documents there.")
    return llm, emb
