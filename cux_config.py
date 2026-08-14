"""Config + CLI helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_NAME = "codex_usage_epd.yaml"

_DEFAULTS = {
    "display": {
        "width": 400,
        "height": 300,
        "model_id": 0x02,
        "sleep_after_push": True,
    },
    "ble": {
        "device": "auto",
        "mtu": 247,
        "scan_timeout": 10.0,
        "interleave": 4,
        "patch_wakeup_pin": True,
    },
    "codex": {
        "auth_file": "~/.codex/auth.json",
        "timeout": 15.0,
    },
    "render": {
        "font": "",
        "warn_threshold": 20.0,
        "preview": "preview.png",
    },
    "schedule": {
        "interval_minutes": 5,
    },
}


def load_config(path: str | None) -> dict:
    import yaml

    cfg_path = Path(path) if path else Path(__file__).parent / DEFAULT_CONFIG_NAME
    merged = {}
    if cfg_path.exists():
        with cfg_path.open() as f:
            user_cfg = yaml.safe_load(f) or {}
    else:
        user_cfg = {}

    for section, defaults in _DEFAULTS.items():
        merged[section] = dict(defaults)
        if isinstance(user_cfg.get(section), dict):
            merged[section].update(user_cfg[section])

    # top-level flat keys are accepted too
    for key in user_cfg:
        if key not in _DEFAULTS:
            merged[key] = user_cfg[key]

    merged["_path"] = str(cfg_path)
    return merged


def expand_user(text: str) -> str:
    return str(Path(text).expanduser())


def resolve_codex_home() -> Path:
    override = __import__("os").environ.get("CODEX_HOME")
    return Path(override) if override else Path.home() / ".codex"
