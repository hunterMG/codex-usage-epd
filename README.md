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
│   └── rle.py                # RLE + chunk encoding
├── config/
│   ├── codex_usage_epd.yaml.example  # config template (generated config is gitignored)
│   └── codex_usage_epd.yaml          # generated config (gitignored)
├── deploy/
│   ├── com.codex-usage-epd.plist.in  # launchd agent template (__REPO__ placeholder)
│   └── install.sh                    # renders the plist + bootstraps launchd
├── tmp/                      # --debug raw JSON dumps (gitignored)
└── logs/                     # launchd stdout/stderr (gitignored)
```

## Features

- Plan quota: 5-hour + weekly windows (used / remaining %, reset time)
- Today's top three models by local token usage, shown in millions with bars
  proportional to the most-used model
- Credits balance when present on the plan
- Red alert bars when remaining % drops below `render.warn_threshold`
- `--selftest` verifies RLE + bitplane round-trips without network/hardware

## Rendering / colour notes

The panel is **black / white / red only — there is no grey**. PIL draws the
dashboard in RGB, then `render.image_to_planes` quantises each pixel:

- luma `>= 140` → white, luma `< 140` → black (`BW_THRESHOLD`)
- pure red (`r > 160` and `r > g`, `r > b`) → red

Consequences for the layout:

- **Light grey text is invisible.** A "grey" like `(180,180,180)` has luma `180`
  and maps to white, so it disappears against the white background. Secondary
  text (`resets …`, `TODAY TOKENS (M)`, the footer) therefore uses
  `GRAY = (100, 100, 100)` — dark enough to map to black and stay legible.
- **Percentages are drawn outside the progress bars.** Global-window bars are
  sized to leave room for the `%` label to their right. This keeps the label
  readable even when the bar fill approaches 100% (a black fill would otherwise
  cover black text).

The `TODAY TOKENS (M)` section reads local rollout logs from
`$CODEX_HOME/sessions` (or `~/.codex/sessions`) and `archived_sessions`. It
groups input + output tokens by the active model in the local timezone, sorts
them by token count, and displays the top three. Cached input is already part of
the input count and is not added a second time.

## Requirements

- macOS (BLE via bleak/CoreBluetooth), Python 3.10+
- ChatGPT login for Codex (`~/.codex/auth.json` with `tokens.access_token`)
- 4.2" 400x300 B/W/R panel with SSD1683/SSD1619 driver, model id `0x02`

## Install with a coding agent

Copy and paste this prompt into Codex or another coding agent:

```text
Install https://github.com/hunterMG/codex-usage-epd on this computer. Read the
README first, check that the prerequisites are available, clone the repository
into an appropriate user directory, install its dependencies with uv, generate
the configuration file, and run the self-test. Do not print, copy, or modify my
Codex authentication token. Tell me what configuration still needs my input,
and ask for confirmation before installing or starting the launchd background
agent.
```

## Setup

```sh
uv sync                              # creates .venv, installs deps + the package (editable)
uv run codex-usage-epd --init        # generate config/codex_usage_epd.yaml from the template
# edit config/codex_usage_epd.yaml (set ble.device etc.)
uv run codex-usage-epd --selftest    # render preview.png, no net/BLE
uv run codex-usage-epd --dry-run     # live fetch + render preview.png
```

## Usage

```sh
uv run codex-usage-epd --init          # generate a config from the template
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
--force            # with --init: overwrite an existing config
--font <path>      # override render.font
--sample           # use synthetic sample data (no network)
--debug            # also dump raw wham/usage JSON to tmp/usage_dump.json
```

Config resolution when `--config` is not given: repo `config/codex_usage_epd.yaml`,
then `~/.config/codex-usage-epd/codex_usage_epd.yaml`, then the bundled template.

`ble.device` accepts `auto` (scans for `NRF_EPD`), a MAC address, or a name
substring.

## Installing as a package

> Not published to PyPI yet. Install from the locally built wheel:

```sh
uv build                              # builds sdist + wheel into dist/
uv pip install dist/*.whl             # or: uv pip install --python <venv> dist/*.whl
```

The config template lives at `config/codex_usage_epd.yaml.example`. Run
`codex-usage-epd --init` to generate a real config (`~/.config/codex-usage-epd/`
when installed, `config/` in a checkout), or point at your own with
`--config /path/to/codex_usage_epd.yaml`.

## launchd (every 60 minutes)

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

Generate a config with `codex-usage-epd --init`, then edit
`config/codex_usage_epd.yaml` (or `~/.config/codex-usage-epd/` when installed).
Notable keys:

- `display.width` / `display.height` - panel pixels (`400`x`300`); `display.model_id` - EPD panel model id (`0x02` for SSD1619 400x300 BWR)
- `display.sleep_after_push` - send the SLEEP command after pushing (default `false`; early sleep aborts the panel refresh)
- `ble.device` - `auto` | MAC address | name substring
- `ble.scan_timeout` - seconds per scan attempt; `ble.scan_retries` - retry the scan before giving up (missed advertisements)
- `ble.interleave` - writes per response-ack, mirrors the web client (`50`)
- `ble.pacing_ms` - delay between writes (`0.0` = back-to-back)
- `ble.hold_after_refresh` - seconds to stay connected so the panel refresh completes (`20`)
- `ble.slot` - which image slot to write on the epdiy.cn slot firmware (`0..N-1`); `"auto"` picks the first free slot so an existing slot is never overwritten; `-1`/`"none"` disables the `SET_SLOT` command (plain EPD-nRF5 firmware)
- `codex.auth_file` - where the OAuth tokens live (default `~/.codex/auth.json`)
- `codex` reads `chatgpt_base_url` from `~/.codex/config.toml` if set
- `render.font` - `.ttf` path (auto-detected on macOS/Windows/Linux otherwise)
- `render.warn_threshold` - remaining % that turns a bar red
- `render.preview` - filename for the rendered preview PNG
- `schedule.interval_minutes` - interval used by `--loop`; launchd uses the fixed `StartInterval` in `deploy/com.codex-usage-epd.plist.in`

## Notes

- Tokens are read from `~/.codex/auth.json` and never logged or committed.
- The display must be powered (USB). With `ble.patch_wakeup_pin: true` it forces
  the device config byte to `0xFF` so the panel keeps re-advertising after the
  timeout, letting the scheduled push always connect.

## Development

- Repository: `git@github.com:hunterMG/codex-usage-epd.git` (private)
- License: GNU AGPL v3 — see `LICENSE`
- Dependencies are managed by uv (`uv sync`, `uv.lock` committed)
