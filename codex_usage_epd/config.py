"""Config + CLI helpers."""

from __future__ import annotations

from pathlib import Path

DEFAULT_CONFIG_NAME = "codex_usage_epd.yaml"
TEMPLATE_NAME = "codex_usage_epd.yaml.example"

USER_CONFIG_DIR = "~/.config/codex-usage-epd"

_DEFAULTS = {
    "display": {
        "width": 400,
        "height": 300,
        "model_id": 0x02,
        "sleep_after_push": False,
    },
    "ble": {
        "device": "auto",
        "mtu": 247,
        "scan_timeout": 10.0,
        "scan_retries": 3,
        "interleave": 50,
        "patch_wakeup_pin": True,
        "pacing_ms": 0.0,
        "hold_after_refresh": 15.0,
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


def _bundled_template_text() -> str:
    import importlib.resources

    res = importlib.resources.files("codex_usage_epd").joinpath("data", TEMPLATE_NAME)
    return res.read_text(encoding="utf-8")


def init_config(path: str | None = None, force: bool = False) -> str:
    """Generate a real config file from the bundled template.

    Defaults to <repo root>/config/codex_usage_epd.yaml when the repo checkout
    is present, otherwise to ~/.config/codex-usage-epd/codex_usage_epd.yaml.
    Refuses to overwrite an existing file unless force=True.
    Returns the path written.
    """
    dest = _default_config_path(path)
    if dest.exists() and not force:
        raise FileExistsError(f"config already exists: {dest} (use --force to overwrite)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_bundled_template_text())
    return str(dest)


def _default_config_path(path: str | None) -> Path:
    if path:
        return Path(path)
    repo = Path(__file__).resolve().parent.parent / "config" / DEFAULT_CONFIG_NAME
    if repo.parent.exists():
        return repo
    return Path(USER_CONFIG_DIR).expanduser() / DEFAULT_CONFIG_NAME


def load_config(path: str | None) -> dict:
    import yaml

    if path:
        cfg_path = Path(path)
        if not cfg_path.exists():
            raise FileNotFoundError(f"config not found: {cfg_path}")
    else:
        # 1) repo checkout layout: <repo root>/config/codex_usage_epd.yaml
        repo_cfg = Path(__file__).resolve().parent.parent / "config" / DEFAULT_CONFIG_NAME
        # 2) installed package: ~/.config/codex-usage-epd/codex_usage_epd.yaml
        user_cfg = Path(USER_CONFIG_DIR).expanduser() / DEFAULT_CONFIG_NAME
        if repo_cfg.exists():
            cfg_path = repo_cfg
        elif user_cfg.exists():
            cfg_path = user_cfg
        else:
            # 3) bundled template as the fallback default
            return _merge(yaml.safe_load(_bundled_template_text()) or {}, f"<bundled>:{TEMPLATE_NAME}")
    with cfg_path.open() as f:
        user_cfg = yaml.safe_load(f) or {}
    return _merge(user_cfg, str(cfg_path))


def _merge(user_cfg: dict, src: str) -> dict:
    merged = {}
    for section, defaults in _DEFAULTS.items():
        merged[section] = dict(defaults)
        if isinstance(user_cfg.get(section), dict):
            merged[section].update(user_cfg[section])

    # top-level flat keys are accepted too
    for key in user_cfg:
        if key not in _DEFAULTS:
            merged[key] = user_cfg[key]

    merged["_path"] = src
    return merged


def expand_user(text: str) -> str:
    return str(Path(text).expanduser())


def resolve_codex_home() -> Path:
    override = __import__("os").environ.get("CODEX_HOME")
    return Path(override) if override else Path.home() / ".codex"
