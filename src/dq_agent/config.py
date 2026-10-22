"""Configuration loading: packaged defaults deep-merged with an optional user file."""
from __future__ import annotations

import copy
import os
from importlib import resources
from pathlib import Path
from typing import Any

import yaml


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def default_config() -> dict[str, Any]:
    text = resources.files("dq_agent").joinpath("default_config.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text)


def load_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    """Load config. Order: explicit path, $DQ_CONFIG, ./config.yaml, else defaults."""
    cfg = default_config()
    candidate = path or os.environ.get("DQ_CONFIG")
    if not candidate and Path("config.yaml").exists():
        candidate = "config.yaml"
    if candidate:
        p = Path(candidate)
        if not p.exists():
            raise FileNotFoundError(f"Config file not found: {p}")
        user = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        cfg = _deep_merge(cfg, user)
    return cfg
