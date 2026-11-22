"""Load and validate config.yaml (spec section 9).

Every model name, threshold and limit in the app comes from here. Nothing else in the
code base should contain those values.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .env import EnvError, Endpoint, apply_overrides, endpoints, load_env_file

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"
CONFIG_ENV_VAR = "EVIDEX_CONFIG"

Fraction = Annotated[float, Field(ge=0.0, le=1.0)]
PositiveInt = Annotated[int, Field(gt=0)]


class _Section(BaseModel):
    # Unknown keys are refused, so a typo in config.yaml is an error, not a silent default.
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelsConfig(_Section):
    answer: str = Field(min_length=1)
    embedding: str = Field(min_length=1)
    embedding_query_prefix: str
    embedding_doc_prefix: str


class OllamaConfig(_Section):
    base_url: str = Field(min_length=1)
    keep_alive: str | int
    temperature: Annotated[float, Field(ge=0.0, le=2.0)]
    num_ctx: Annotated[int, Field(ge=512, le=8192)]  # spec section 2: configurable up to 8192
    num_predict: PositiveInt
    timeout_seconds: Annotated[float, Field(gt=0)]

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")


class RerankerConfig(_Section):
    enabled: bool
    model: str = Field(min_length=1)
    max_pair_tokens: PositiveInt
    cpu_threads: PositiveInt


class IngestionConfig(_Section):
    chunk_size_tokens: PositiveInt
    chunk_overlap_tokens: Annotated[int, Field(ge=0)]
    embed_batch_size: PositiveInt
    max_file_mb: Annotated[float, Field(gt=0)]
    ocr_enabled: bool

    @model_validator(mode="after")
    def _overlap_smaller_than_chunk(self) -> "IngestionConfig":
        if self.chunk_overlap_tokens >= self.chunk_size_tokens:
            raise ValueError("ingestion.chunk_overlap_tokens must be smaller than chunk_size_tokens")
        return self


class RetrievalConfig(_Section):
    candidate_k: PositiveInt
    final_k: PositiveInt
    relevance_threshold: Fraction
    vector_threshold: Fraction
    history_turns: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def _final_le_candidates(self) -> "RetrievalConfig":
        if self.final_k > self.candidate_k:
            raise ValueError("retrieval.final_k must not be larger than retrieval.candidate_k")
        return self


class ConfidenceConfig(_Section):
    high_from: Fraction
    medium_from: Fraction


class SqlConfig(_Section):
    timeout_seconds: Annotated[float, Field(gt=0)]
    max_rows: PositiveInt


class GroundingConfig(_Section):
    claim_check_enabled: bool
    max_retries: Annotated[int, Field(ge=0)]


class UiConfig(_Section):
    default_theme: str
    departments: list[str] = Field(min_length=1)

    @field_validator("default_theme")
    @classmethod
    def _theme(cls, v: str) -> str:
        if v not in ("dark", "light"):
            raise ValueError("ui.default_theme must be 'dark' or 'light'")
        return v


class StorageConfig(_Section):
    vector_store: str = Field(min_length=1)
    sqlite: str = Field(min_length=1)


class Config(_Section):
    models: ModelsConfig
    ollama: OllamaConfig
    reranker: RerankerConfig
    ingestion: IngestionConfig
    retrieval: RetrievalConfig
    confidence: ConfidenceConfig
    sql: SqlConfig
    grounding: GroundingConfig
    ui: UiConfig
    storage: StorageConfig

    # Directory the config file lives in; relative storage paths resolve against it.
    base_dir: Path = Field(default=REPO_ROOT, exclude=True)
    # From .env / environment (app/env.py); never written to config.yaml or returned by the API.
    llm_endpoint: Endpoint | None = Field(default=None, exclude=True)
    embed_endpoint: Endpoint | None = Field(default=None, exclude=True)
    env_overrides: tuple[str, ...] = Field(default=(), exclude=True)

    @model_validator(mode="after")
    def _confidence_order(self) -> "Config":
        c, r = self.confidence, self.retrieval
        if not (r.relevance_threshold <= c.medium_from <= c.high_from):
            raise ValueError(
                "thresholds must satisfy retrieval.relevance_threshold <= "
                "confidence.medium_from <= confidence.high_from"
            )
        return self

    @property
    def llm(self) -> Endpoint:
        """Where answers, SQL, rewrites and labels are generated."""
        return self.llm_endpoint or Endpoint(provider="ollama", base_url=self.ollama.base_url)

    @property
    def embed(self) -> Endpoint:
        """Where embeddings are made (defaults to the answer endpoint)."""
        return self.embed_endpoint or self.llm

    def resolve_path(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else (self.base_dir / path).resolve()

    @property
    def vector_store_path(self) -> Path:
        return self.resolve_path(self.storage.vector_store)

    @property
    def sqlite_path(self) -> Path:
        return self.resolve_path(self.storage.sqlite)


class ConfigError(ValueError):
    """config.yaml is missing, not valid YAML, or fails validation."""


def config_path() -> Path:
    return Path(os.environ.get(CONFIG_ENV_VAR, DEFAULT_CONFIG_PATH)).resolve()


def parse_config(data: dict, base_dir: Path = REPO_ROOT) -> Config:
    from pydantic import ValidationError

    if not isinstance(data, dict):
        raise ConfigError("config.yaml must contain a mapping at the top level")
    try:
        return Config(**data, base_dir=base_dir)
    except ValidationError as exc:
        raise ConfigError(_format_errors(exc)) from exc


def load_config(path: Path | str | None = None, *, use_env: bool = True) -> Config:
    """Load config.yaml, then apply .env / environment overrides and endpoints."""
    if use_env:
        load_env_file()
    path = Path(path) if path is not None else config_path()
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"config.yaml is not valid YAML: {exc}") from exc
    if not use_env or not isinstance(data, dict):
        return parse_config(data, base_dir=path.resolve().parent)
    return with_env(data, dict(os.environ), base_dir=path.resolve().parent)


def with_env(data: dict, env: dict[str, str], base_dir: Path = REPO_ROOT) -> Config:
    """config.yaml data + environment -> validated Config with endpoints."""
    try:
        data, overridden = apply_overrides(data, env)
        cfg = parse_config(data, base_dir=base_dir)
        llm, emb = endpoints(env, cfg.ollama.base_url)
    except EnvError as exc:
        raise ConfigError(f"Invalid .env setting: {exc}") from exc
    except ConfigError as exc:
        raise ConfigError(f"{exc} (check .env overrides)" if any(k in env for k in _env_names()) else str(exc)) from exc
    return cfg.model_copy(update={"llm_endpoint": llm, "embed_endpoint": emb, "env_overrides": tuple(overridden)})


def _env_names():
    from .env import OVERRIDES

    return OVERRIDES.keys()


@lru_cache(maxsize=1)
def get_config() -> Config:
    """Process-wide config, loaded once. Call get_config.cache_clear() after saving settings."""
    return load_config()


def _format_errors(exc) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "config"
        parts.append(f"{loc}: {err['msg']}")
    return "Invalid config: " + "; ".join(parts)
