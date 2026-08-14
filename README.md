# codex-usage-epd

Push your OpenAI Codex usage quota to a 4.2" 400x300 three-colour e-paper display
powered by the [EPD-nRF5](https://github.com/tsl0922/EPD-nRF5) firmware.

Reads the same data source CodexBar uses (OAuth `wham/usage`) and speaks the
EPD-nRF5 BLE image-transfer protocol byte-for-byte (RLE + bitplanes from the
web client).

## Layout

```
codex-usage-epd/
├── pyproject.toml            # uv-managed project + AGPL-3.0 metadata
├── uv.lock                   # pinned dependency lockfile (uv)
├── LICENSE                   # GNU AGPL v3
├── codex_usage_epd/          # the Python package
│   ├── cli.py                # entry point (console script: codex-usage-epd)
│   ├── api.py                # OAuth wham/usage fetch + parse
│   ├── ble.py                # BLE connect/push (EPD-nRF5 protocol)
│   ├── config.py             # YAML config loading
│   ├── model.py              # usage data model
│   ├── render.py             # PIL dashboard rendering -> bitplanes
│   ├── rle.py                # RLE + chunk encoding
│   └── data/                 # bundled default config shipped in the wheel
├── config/
│   └── codex_usage_epd.yaml  # repo checkout config
├── deploy/
│   ├── com.codex-usage-epd.plist.in  # launchd agent template (__REPO__ placeholder)
│   └── install.sh                    # renders the plist + bootstraps launchd
├── tmp/                      # --debug raw JSON dumps (gitignored)
└── logs/                     # launchd stdout/stderr (gitignored)
```

## Features

- Plan quota: 5-hour + weekly windows (used / remaining %, reset time)
- Per-model usage from `additional_rate_limits` (spark / mini / realtime ...)
- Credits balance when present on the plan
- Red alert bars when remaining % drops below `render.warn_threshold`
- `--selftest` verifies RLE + bitplane round-trips without network/hardware

## Requirements

- macOS (BLE via bleak/CoreBluetooth), Python 3.10+
- ChatGPT login for Codex (`~/.codex/auth.json` with `tokens.access_token`)
- 4.2" 400x300 B/W/R panel with SSD1683/SSD1619 driver, model id `0x02`

## Setup

```sh
uv sync                       # creates .venv, installs deps + the package (editable)
uv run codex-usage-epd --selftest   # render preview.png, no net/BLE
uv run codex-usage-epd --dry-run    # live fetch + render preview.png
```

## Usage

```sh
uv run codex-usage-epd --selftest      # sample data, verify encode/decode
uv run codex-usage-epd --dry-run       # fetch + render preview.png (no BLE)
uv run codex-usage-epd --probe         # connect + INIT, print device config/mtu
uv run codex-usage-epd --test-screen   # INIT + CLEAR full refresh (firmware check)
uv run codex-usage-epd --once          # fetch + render + push to display
uv run codex-usage-epd --loop          # push every N minutes forever
```

Modifiers (combine with the actions above):

```sh
--config <path>    # use this YAML config instead of the default
--font <path>      # override render.font
--sample           # use synthetic sample data (no network)
--debug            # also dump raw wham/usage JSON to tmp/usage_dump.json
```

`ble.device` accepts `auto` (scans for `NRF_EPD`), a MAC address, or a name
substring.

## Installing as a package

> Not published to PyPI yet. Install from the locally built wheel:

```sh
uv build                              # builds sdist + wheel into dist/
uv pip install dist/*.whl             # or: uv pip install --python <venv> dist/*.whl
```

The wheel ships a bundled default config (`codex_usage_epd/data/`). Point it at
your own with `--config /path/to/codex_usage_epd.yaml`.

## launchd (every 5 minutes)

```sh
./deploy/install.sh    # fills the __REPO__ placeholder with the checkout path
                       # and bootstraps the agent (works for any username)
```

This generates `~/Library/LaunchAgents/com.codex-usage-epd.plist` from
`deploy/com.codex-usage-epd.plist.in`, so no manual path editing is needed.
To remove the agent later:

```sh
launchctl bootout gui/$(id -u)/com.codex-usage-epd
rm ~/Library/LaunchAgents/com.codex-usage-epd.plist
```

## Configuration

See `config/codex_usage_epd.yaml`. Notable keys:

- `display.width` / `display.height` - panel pixels (`400`x`300`); `display.model_id` - EPD panel model id (`0x02` for SSD1619 400x300 BWR)
- `display.sleep_after_push` - send the SLEEP command after pushing (default `false`; early sleep aborts the panel refresh)
- `ble.device` - `auto` | MAC address | name substring
- `ble.interleave` - writes per response-ack, mirrors the web client (`50`)
- `ble.pacing_ms` - delay between writes (`0.0` = back-to-back)
- `ble.hold_after_refresh` - seconds to stay connected so the panel refresh completes (`15`)
- `codex.auth_file` - where the OAuth tokens live (default `~/.codex/auth.json`)
- `codex` reads `chatgpt_base_url` from `~/.codex/config.toml` if set
- `render.font` - `.ttf` path (auto-detected on macOS/Windows/Linux otherwise)
- `render.warn_threshold` - remaining % that turns a bar red
- `render.preview` - filename for the rendered preview PNG
- `schedule.interval_minutes` - interval used by `--loop` / launchd

## Notes

- Tokens are read from `~/.codex/auth.json` and never logged or committed.
- The display must be powered (USB). With `ble.patch_wakeup_pin: true` it forces
  the device config byte to `0xFF` so the panel keeps re-advertising after the
  timeout, letting the scheduled push always connect.

## Development

- Repository: `git@github.com:hunterMG/codex-usage-epd.git` (private)
- License: GNU AGPL v3 — see `LICENSE`
- Dependencies are managed by uv (`uv sync`, `uv.lock` committed)