"""Loads backend/config.yaml once and hands it around as a plain dict."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(__file__).parent / "config.yaml"

_cfg: dict[str, Any] | None = None


def load() -> dict[str, Any]:
    global _cfg
    if _cfg is None:
        _cfg = yaml.safe_load(CONFIG_PATH.read_text())
    return _cfg


def get(*keys: str, default: Any = None) -> Any:
    """get('ollama', 'model') -> config['ollama']['model'], or default if any hop is missing."""
    node: Any = load()
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    return node
