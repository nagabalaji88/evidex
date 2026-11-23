"""config.yaml loading and validation (spec sections 2 and 9)."""

from __future__ import annotations

import re

import pytest
import yaml

from app.config import DEFAULT_CONFIG_PATH, REPO_ROOT, ConfigError, load_config, parse_config

from .conftest import config_dict


def spec_example_config() -> dict:
    spec = (REPO_ROOT / "docs" / "spec.md").read_text(encoding="utf-8")
    section9 = spec.split("## 9. Example config", 1)[1]
    block = re.search(r"```yaml\n(.*?)```", section9, re.S).group(1)
    return yaml.safe_load(block)


# Added after the spec, at the owner's request: the Gemini API and the local-models toggle.
NOT_IN_SPEC = ("provider", "gemini")


def test_repo_config_matches_spec_section_9():
    """M1: check config.yaml against section 9 (same keys and values), apart from the
    sections added later for the Gemini API."""
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    spec = spec_example_config()
    # The owner asked for no local models by default: the reranker (a local model) is off.
    assert data["reranker"].pop("enabled") is False and spec["reranker"].pop("enabled") is True
    assert {k: v for k, v in data.items() if k not in NOT_IN_SPEC} == spec


def test_repo_config_loads_and_validates():
    cfg = load_config()
    assert cfg.local_models.answer == "qwen2.5:7b-instruct"  # the toggle is off: Gemini answers
    assert cfg.models.answer == cfg.gemini.answer_model
    assert cfg.ollama.num_ctx == 4096
    assert cfg.retrieval.final_k == 3


def test_spec_default_settings_table(config):
    """Spec section 2 'Default settings' table."""
    assert config.ollama.temperature == 0.1
    assert config.ollama.num_ctx == 4096
    assert config.ollama.num_predict == 400
    assert config.retrieval.final_k == 3


def test_storage_paths_resolve_against_config_dir(tmp_path):
    cfg = parse_config(config_dict(), base_dir=tmp_path)
    assert cfg.vector_store_path == (tmp_path / "data" / "chroma").resolve()
    assert cfg.sqlite_path == (tmp_path / "data" / "app.db").resolve()


def test_env_var_selects_config_file(tmp_path, monkeypatch):
    data = config_dict()
    data["models"]["answer"] = "llama3.1:8b"
    data["provider"]["use_local_models"] = True
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv("EVIDEX_CONFIG", str(path))
    assert load_config().models.answer == "llama3.1:8b"


@pytest.mark.parametrize(
    "section,key,value,message",
    [
        ("ollama", "num_ctx", 9000, "ollama.num_ctx"),  # spec: configurable up to 8192
        ("ollama", "temperature", -1, "ollama.temperature"),
        ("retrieval", "relevance_threshold", 1.5, "retrieval.relevance_threshold"),
        ("retrieval", "final_k", 20, "final_k"),  # more than candidate_k
        ("ingestion", "chunk_overlap_tokens", 500, "chunk_overlap_tokens"),  # >= chunk size
        ("confidence", "medium_from", 0.9, "thresholds"),  # above high_from
        ("ui", "default_theme", "blue", "default_theme"),
        ("models", "answer", "", "models.answer"),
    ],
)
def test_invalid_values_are_refused(section, key, value, message):
    data = config_dict()
    data[section][key] = value
    with pytest.raises(ConfigError, match=re.escape(message)):
        parse_config(data)


def test_unknown_key_is_refused():
    data = config_dict()
    data["retrieval"]["relevance_treshold"] = 0.4  # typo
    with pytest.raises(ConfigError, match="relevance_treshold"):
        parse_config(data)


def test_missing_section_is_refused():
    data = config_dict()
    del data["reranker"]
    with pytest.raises(ConfigError, match="reranker"):
        parse_config(data)


def test_missing_file_and_bad_yaml(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("models: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(bad)
