# Developer guide

The utility reads the same data source CodexBar uses (OAuth `wham/usage`) and
speaks the EPD-nRF5 BLE image-transfer protocol byte-for-byte (RLE + bitplanes
from the web client).

## Repository layout

```text
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
│   └── codex_usage_epd.yaml.example  # configuration template
├── deploy/
│   ├── com.codex-usage-epd.plist.in  # launchd agent template
│   └── install.sh             # renders and bootstraps launchd
├── tests/                     # hardware- and network-independent tests
└── assets/                    # documentation media
```

`tmp/`, `logs/`, and the generated configuration file are local, gitignored
artifacts created while running the utility.

## Rendering / colour notes

The panel is **black / white / red only — there is no grey**. PIL draws the
dashboard in RGB, then `render.image_to_planes` quantises each pixel:

- luma `>= 140` → white, luma `< 140` → black (`BW_THRESHOLD`)
- pure red (`r > 160` and `r > g`, `r > b`) → red

Consequences for the layout:

- **Light grey text is invisible.** A "grey" like `(180,180,180)` has luma `180`
  and maps to white, so it disappears against the white background. Secondary
  text (`resets …`, `Tokens used today`, the footer) therefore uses
  `GRAY = (100, 100, 100)` — dark enough to map to black and stay legible.
- **Percentages are drawn outside the progress bars.** Global-window bars are
  sized to leave room for the `%` label to their right. This keeps the label
  readable even when the bar fill approaches 100% (a black fill would otherwise
  cover black text).

The token sections read local rollout logs from
`$CODEX_HOME/sessions` (or `~/.codex/sessions`) and `archived_sessions`. They
group input + output tokens by date and active model in the local timezone.
The chart sums all models for each day and shows zero for dates without local
usage. Today's Total includes every model; the breakdown displays the top three.
With history enabled, both sections share one log scan. Otherwise only today's
usage is collected. Cached input is already part of the input count and is not
added a second time.

## Dashboard checks

The dashboard has two local preview configurations:

- `tests/check-today.yaml` checks the default layout with today's usage only.
- `tests/check-history.yaml` checks the layout with the optional `Last 7 days`
  chart enabled.

Run both rendering and bitplane checks with:

```sh
uv run codex-usage-epd --selftest --config tests/check-today.yaml
uv run codex-usage-epd --selftest --config tests/check-history.yaml
```

The commands write `tmp/preview-today.png` and `tmp/preview-history.png`.
The preview images remain local, gitignored test artifacts.
