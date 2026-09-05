"""Fetch and parse OpenAI Codex usage from the OAuth `wham/usage` endpoint.

Replicates CodexBar's CodexOAuthUsageFetcher behaviour:
  - credentials come from ~/.codex/auth.json (tokens.access_token/account_id)
  - base URL honours the `chatgpt_base_url` key in ~/.codex/config.toml
  - endpoint: GET <base>/wham/usage   (or /api/codex/usage on custom bases)
  - headers: Authorization: Bearer, User-Agent, Accept, ChatGPT-Account-Id
"""

from __future__ import annotations

import json
import time
from pathlib import Path

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

from .model import Balance, ModelTokenUsage, Window

DEFAULT_CHATGPT_BASE = "https://chatgpt.com/backend-api/"
USAGE_PATH = "/wham/usage"
CODEX_USAGE_PATH = "/api/codex/usage"


class UsageFetchError(Exception):
    pass


def codex_home() -> Path:
    override = os_environ_get("CODEX_HOME")
    if override:
        return Path(override)
    return Path.home() / ".codex"


def os_environ_get(key: str, default: str = "") -> str:
    import os

    return os.environ.get(key, default)


def read_auth(auth_file: Path) -> tuple[str, str | None]:
    """Return (access_token, account_id) from ~/.codex/auth.json."""
    if not auth_file.exists():
        raise UsageFetchError(f"auth file not found: {auth_file}. Run `codex` to log in.")
    try:
        data = json.loads(auth_file.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise UsageFetchError(f"failed to decode {auth_file}: {e}") from e

    tokens = data.get("tokens") or {}
    access_token = tokens.get("access_token") or tokens.get("accessToken") or ""
    if not access_token:
        api_key = (data.get("OPENAI_API_KEY") or "").strip()
        if api_key:
            raise UsageFetchError(
                "auth.json holds an API key, not an OAuth login. "
                "`wham/usage` requires ChatGPT subscription auth (`codex login`)."
            )
        raise UsageFetchError(f"no access_token in {auth_file}")
    account_id = tokens.get("account_id") or tokens.get("accountId") or None
    return access_token, account_id


def resolve_base_url(home: Path) -> str:
    """Read chatgpt_base_url from config.toml (like CodexBar)."""
    cfg = home / "config.toml"
    if cfg.exists():
        try:
            for raw_line in cfg.read_text().splitlines():
                line = raw_line.split("#", 1)[0].strip()
                if not line:
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    if key.strip() == "chatgpt_base_url":
                        value = value.strip().strip('"').strip("'").rstrip("/")
                        if value:
                            return value
        except OSError:
            pass
    return DEFAULT_CHATGPT_BASE.rstrip("/")


def build_usage_url(home: Path) -> str:
    base = resolve_base_url(home)
    path = USAGE_PATH if base.endswith("/backend-api") else CODEX_USAGE_PATH
    return base + path


def fetch_usage(access_token: str, account_id: str | None, url: str, timeout: float = 15.0,
                attempts: int = 3) -> dict:
    if requests is None:
        raise UsageFetchError("requests is required. Install with: pip install requests")

    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "codex-usage-epd/0.1",
        "Accept": "application/json",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id

    last_err: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)  # type: ignore[name-defined]
        except Exception as e:  # network / TLS errors -> retry with backoff
            last_err = e
            if attempt < attempts:
                import time

                time.sleep(1.5 * attempt)
                continue
            raise UsageFetchError(f"network error: {e}") from e

        if resp.status_code in (401, 403):
            raise UsageFetchError(
                f"HTTP {resp.status_code}: token expired/invalid. Run `codex` to re-authenticate."
            )
        if resp.status_code != 200:
            raise UsageFetchError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError as e:
            raise UsageFetchError(f"invalid JSON response: {e}") from e
    raise UsageFetchError(f"network error: {last_err}")  # pragma: no cover


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _to_int(value) -> int | None:
    f = _to_float(value)
    return int(f) if f is not None else None


def _window(raw, name: str, label: str) -> Window | None:
    if not isinstance(raw, dict):
        return None
    used = _to_float(raw.get("used_percent"))
    if used is None:
        used = 0.0
    return Window(
        name=name,
        label=label,
        used_percent=used,
        resets_at=_to_int(raw.get("reset_at")) or _to_int(raw.get("resets_at")),
        limit_seconds=_to_int(raw.get("limit_window_seconds")),
        reset_after_seconds=_to_int(raw.get("reset_after_seconds")),
    )


def _name_for_window(reset_at: int | None, limit_seconds: int | None) -> str:
    # wham/usage windows have no "name" field; infer from duration.
    if limit_seconds:
        if limit_seconds == 5 * 3600:
            return "five_hour"
        if limit_seconds == 7 * 24 * 3600:
            return "weekly"
        if limit_seconds == 30 * 24 * 3600:
            return "thirty_day"
        return f"win_{limit_seconds // 60}m"
    return "window"


def _windows_from_rate_limit(rate: dict) -> list[Window]:
    out: list[Window] = []
    pw = _window(rate.get("primary_window"), "five_hour", "5H")
    sw = _window(rate.get("secondary_window"), "weekly", "WK")
    if pw:
        pw.name = _name_for_window(pw.resets_at, pw.limit_seconds)
        pw.label = _label_for(pw.name)
        out.append(pw)
    if sw:
        sw.name = _name_for_window(sw.resets_at, sw.limit_seconds)
        sw.label = _label_for(sw.name)
        out.append(sw)
    return out


def _label_for(name: str) -> str:
    return {
        "five_hour": "5H",
        "weekly": "WK",
        "thirty_day": "30D",
    }.get(name, name.upper()[:3])


def parse_usage(raw: dict) -> Balance:
    plan_type = raw.get("plan_type") or None

    credits_raw = raw.get("credits")
    has_credits = False
    credits_unlimited = False
    credits_balance = None
    if isinstance(credits_raw, dict):
        has_credits = bool(credits_raw.get("has_credits", False))
        credits_unlimited = bool(credits_raw.get("unlimited", False))
        credits_balance = _to_float(credits_raw.get("balance"))

    rate = raw.get("rate_limit") or {}
    windows = _windows_from_rate_limit(rate) if isinstance(rate, dict) else []

    return Balance(
        plan_type=plan_type,
        windows=windows,
        credits_balance=credits_balance,
        credits_unlimited=credits_unlimited,
        has_credits=has_credits,
        source="oauth",
    )


def sample_balance() -> Balance:
    """Synthetic snapshot used by --selftest (no network / no device)."""
    now = int(time.time())
    return Balance(
        plan_type="plus",
        windows=[
            Window("five_hour", "5H", used_percent=17.0, resets_at=now + 3600, limit_seconds=5 * 3600, reset_after_seconds=3600),
            Window("weekly", "WK", used_percent=36.0, resets_at=now + 3 * 86400, limit_seconds=7 * 86400, reset_after_seconds=3 * 86400),
        ],
        models=[
            ModelTokenUsage("gpt-5.6-sol", 12_840_000),
            ModelTokenUsage("gpt-6-astra", 6_275_000),
            ModelTokenUsage("gpt-5.6-luna", 925_000),
        ],
        has_credits=False,
        source="sample",
    )
