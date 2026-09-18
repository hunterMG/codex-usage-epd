# Repository Guidelines

## Project Structure & Module Organization

This is a Python 3.10+ macOS utility managed with `uv`. Application code is in
`codex_usage_epd/`: `cli.py` and `__main__.py` provide the command-line entry
point, `api.py` and `token_usage.py` collect usage data, `model.py` defines data
structures, `render.py` produces the 400×300 B/W/R dashboard, and `ble.py` and
`rle.py` implement display transport and encoding. Tests are in `tests/`.
`config/` contains the example YAML configuration, `deploy/` contains the
launchd installer and plist template, and `assets/` holds documentation media.
Generated configuration, logs, previews, and debug dumps are local artifacts
and should not be committed.

## Build, Test, and Development Commands

Use the locked environment for reproducible work:

```sh
uv sync --locked                 # install runtime and development dependencies
uv run pytest                    # run the test suite
uv run ruff check .              # lint Python sources
uv run pyright                  # run static type checking
uv run pre-commit run --all-files # run configured checks together
uv run codex-usage-epd --selftest # exercise rendering/encoding without hardware
uv build                         # build sdist and wheel in dist/
```

For a local integration check, `uv run codex-usage-epd --dry-run` fetches and
renders without sending BLE data. Do not expose authentication tokens in logs,
fixtures, or pull requests.

## Coding Style & Naming Conventions

Follow Ruff’s defaults and the existing four-space Python indentation. Use
`snake_case` for functions, variables, and modules; `PascalCase` for classes;
and uppercase names for constants. Keep hardware/protocol details isolated in
`ble.py` and `rle.py`, and prefer typed, small functions that are easy to test.

## Testing Guidelines

Pytest is the test framework. Name files `test_*.py` and test functions
`test_*`. Add regression coverage for changes to parsing, token aggregation,
rendering, or encoding; keep tests hardware- and network-independent by using
fixtures or synthetic sample data. Run `uv run pytest` before submitting.

## Commit & Pull Request Guidelines

Use Conventional Commits, as in the existing history: `feat:`, `fix:`,
`docs:`, `refactor:`, `test:`, or `chore:` with a concise imperative subject
(for example, `fix: handle missing usage window`). Pull requests should explain
the user-visible behavior and implementation, link related issues, list tests
and checks run, and include a preview image when changing dashboard layout.
Call out any configuration, BLE, macOS, or launchd steps reviewers must perform.
