"""Write values into config.yaml without losing its comments or layout."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    if isinstance(value, list):
        return "[" + ", ".join(yaml_scalar(v) for v in value) + "]"
    s = str(value)
    if s == "" or re.search(r"[:#\[\]{},&*!|>'\"%@`]|^\s|\s$", s) or s.lower() in ("true", "false", "null", "yes", "no"):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def set_value(text: str, section: str, key: str, value: Any) -> str:
    """Replace `key: value` inside `section:`; keep the trailing comment."""
    pattern = re.compile(rf"(^{re.escape(section)}:\n(?:[ \t]+.*\n|\n)*?[ \t]+{re.escape(key)}:[ \t]*)"
                         rf"((?:\"[^\"]*\"|'[^']*'|\[[^\]]*\]|[^#\n])*?)([ \t]*(#.*)?)$", re.M)
    if not pattern.search(text):
        raise KeyError(f"{section}.{key} is not in config.yaml")
    return pattern.sub(lambda m: m.group(1) + yaml_scalar(value) + m.group(3), text, count=1)


def set_config_value(path: Path, section: str, key: str, value: Any) -> None:
    path.write_text(set_value(path.read_text(encoding="utf-8"), section, key, value), encoding="utf-8")
