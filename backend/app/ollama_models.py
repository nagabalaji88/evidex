"""Print the Ollama models the settings need, one per line (used by evidex.bat).

Reads config.yaml plus .env like the app does, so the names are never hard-coded.
Models served by another provider (an OpenAI-compatible server) are skipped: Ollama
cannot pull those.

    cd backend; .venv\\Scripts\\python -m app.ollama_models
"""

from __future__ import annotations

from .config import load_config


def needed_models() -> list[str]:
    cfg = load_config()
    names: list[str] = []
    if cfg.llm.provider == "ollama":
        names.append(cfg.models.answer)
    if cfg.embed.provider == "ollama" and cfg.models.embedding not in names:
        names.append(cfg.models.embedding)
    return names


def main() -> None:
    for name in needed_models():
        print(name)


if __name__ == "__main__":
    main()
