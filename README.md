# codex-usage-epd

Push your OpenAI Codex usage quota to a 4.2" 400x300 three-colour e-paper display
powered by the [EPD-nRF5](https://github.com/tsl0922/EPD-nRF5) firmware.

Reads the same data source CodexBar uses (OAuth `wham/usage`) and speaks the
EPD-nRF5 BLE image-transfer protocol byte-for-byte (RLE + bitplanes from the
web client).

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
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python codex_usage_epd.py --selftest   # render preview.png, no net/BLE
.venv/bin/python codex_usage_epd.py --dry-run    # live fetch + render preview.png
```

## Usage

```sh
codex_usage_epd.py --selftest     # sample data, verify encode/decode
codex_usage_epd.py --dry-run      # fetch + render preview.png (no BLE)
codex_usage_epd.py --probe        # connect + INIT, print device config/mtu
codex_usage_epd.py --once         # fetch + render + push to display
codex_usage_epd.py --loop         # push every N minutes forever
codex_usage_epd.py --debug        # also dump raw wham/usage JSON
```

`ble.device` accepts `auto` (scans for `NRF_EPD`), a MAC address, or a name
substring.

## launchd (every 5 minutes)

```sh
mkdir -p logs
cp com.codex-usage-epd.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.codex-usage-epd.plist
```

Edit the `ProgramArguments` / `WorkingDirectory` paths in the plist first.

## Configuration

See `codex_usage_epd.yaml`. Notable keys:

- `codex.auth_file` - where the OAuth tokens live (default `~/.codex/auth.json`)
- `codex` reads `chatgpt_base_url` from `~/.codex/config.toml` if set
- `render.font` - `.ttf` path (auto-detected on macOS/Windows/Linux otherwise)
- `render.warn_threshold` - remaining % that turns a bar red
- `display.model_id` - EPD panel model id (`0x02` for SSD1619 400x300 BWR)

## Notes

- Tokens are read from `~/.codex/auth.json` and never logged or committed.
- The display must be powered (USB). With `wakeup_pin=0xFF` it keeps
  re-advertising after the timeout so the scheduled push can always connect.
- This is a private prototype; no remote configured.