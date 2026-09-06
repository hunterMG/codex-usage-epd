from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
import yaml

from codex_usage_epd import cli
from codex_usage_epd.model import Balance, DailyTokenUsage, ModelTokenUsage


@pytest.mark.parametrize(("configured", "flags", "expected"), [
    (None, [], False),
    (True, [], True),
    (False, ["--show-history"], True),
    (True, ["--no-show-history"], False),
])
def test_history_option_reaches_scan_and_render(tmp_path, monkeypatch, configured, flags, expected):
    render_cfg = {"preview": str(tmp_path / "preview.png")}
    if configured is not None:
        render_cfg["show_history"] = configured
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"render": render_cfg}))
    monkeypatch.setattr(cli, "read_auth", lambda path: ("test-token", None))
    monkeypatch.setattr(cli, "build_usage_url", lambda path: "https://example.invalid/usage")
    monkeypatch.setattr(cli, "fetch_usage", lambda *args, **kwargs: {})
    now = datetime.now().astimezone()
    balance = Balance(plan_type=None, fetched_at=now)
    monkeypatch.setattr(cli, "parse_usage", lambda raw: balance)
    models = [ModelTokenUsage(f"model-{i}", 1000 - i) for i in range(4)]
    history = [DailyTokenUsage(now.date() - timedelta(days=1), 100)] if expected else []
    scan = Mock(return_value=(models, history))
    monkeypatch.setattr(cli, "read_token_usage", scan)
    renderer = Mock(wraps=cli.render_dashboard)
    monkeypatch.setattr(cli, "render_dashboard", renderer)

    assert cli.main(["--config", str(config), "--dry-run", *flags]) == 0

    assert scan.call_args.kwargs["limit"] is None
    assert scan.call_args.kwargs["history_days"] == (7 if expected else 0)
    assert renderer.call_args.kwargs["show_history"] is expected
    assert balance.total_tokens_today == 3994
    assert (tmp_path / "preview.png").is_file()
