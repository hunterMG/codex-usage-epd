#!/usr/bin/env python3
"""codex-usage-epd: fetch OpenAI Codex usage and push it to an e-paper display.

Primary data source: OAuth `wham/usage` endpoint (see CodexBar).
Display protocol is compatible with the EPD-nRF5 web client (RLE + bitplanes).

Commands:
  --selftest     render sample data + verify RLE round-trip (no network/BLE)
  --dry-run      fetch + render preview.png, print summary (no BLE)
  --once         fetch + render + push to display once
  --loop         push every N minutes forever
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from .api import build_usage_url, fetch_usage, parse_usage, read_auth, sample_balance, UsageFetchError
from .ble import BlePushError, probe, push_display, test_screen
from .config import expand_user, load_config
from .render import image_to_planes, planes_to_rgb, render_dashboard, resolve_font_path


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Codex usage -> e-paper display")
    p.add_argument("--config", default=None, help="path to YAML config (default: repo config/ or bundled)")
    p.add_argument("--debug", action="store_true", help="dump raw wham/usage JSON to tmp/usage_dump.json")
    p.add_argument("--font", default=None, help="override render.font")
    p.add_argument("--sample", action="store_true", help="use synthetic sample data (no network)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--selftest", action="store_true", help="render sample data, no network/device")
    g.add_argument("--dry-run", action="store_true", help="fetch + render preview.png, no BLE")
    g.add_argument("--probe", action="store_true", help="connect + INIT, print device config/mtu, no image")
    g.add_argument("--test-screen", action="store_true", help="INIT + CLEAR full refresh, check firmware path")
    g.add_argument("--once", action="store_true", help="fetch + render + push once")
    g.add_argument("--loop", action="store_true", help="push every N minutes forever")
    return p.parse_args(argv)


def load_balance(args: argparse.Namespace, cfg: dict) -> tuple:
    """Return (balance, raw_dict_or_None)."""
    codex = cfg["codex"]
    auth_file = expand_user(codex["auth_file"])
    access_token, account_id = read_auth(Path(auth_file))
    url = build_usage_url(Path(auth_file).parent)
    raw = fetch_usage(access_token, account_id, url, timeout=codex.get("timeout", 15.0))
    if args.debug:
        dump_dir = Path(__file__).resolve().parent.parent / "tmp"
        dump_dir.mkdir(exist_ok=True)
        dump_path = dump_dir / "usage_dump.json"
        dump_path.write_text(json.dumps(raw, indent=2))
        print(f"[debug] raw wham/usage JSON -> {dump_path}")
    return parse_usage(raw), raw


def render(balance, cfg: dict, font_path: str) -> tuple[bytes, bytes]:
    render_cfg = cfg["render"]
    img = render_dashboard(
        balance,
        font_path=font_path,
        warn_threshold=render_cfg.get("warn_threshold", 20.0),
        width=cfg["display"]["width"],
        height=cfg["display"]["height"],
    )
    preview = render_cfg.get("preview", "preview.png")
    img.save(preview)
    print(f"[render] saved {preview} ({img.size[0]}x{img.size[1]})")
    return image_to_planes(img)


def run_selftest(cfg: dict, font_path: str) -> int:
    balance = sample_balance()
    render_cfg = cfg["render"]
    img = render_dashboard(
        balance,
        font_path=font_path,
        warn_threshold=render_cfg.get("warn_threshold", 20.0),
        width=cfg["display"]["width"],
        height=cfg["display"]["height"],
    )
    preview = render_cfg.get("preview", "preview.png")
    img.save(preview)
    bw, red = image_to_planes(img)
    print(f"[selftest] rendered {img.size[0]}x{img.size[1]} -> {preview}")
    print(f"[selftest] planes: bw={len(bw)}B red={len(red)}B")

    # RLE round-trip: compress -> decompress == original, chunks self-contained
    from .rle import _rle_decompressed, rle_chunks

    chunk_size = 242  # MTU 247 - 5
    for name, plane in (("bw", bw), ("red", red)):
        chunks = rle_chunks(plane, chunk_size)
        for i, ch in enumerate(chunks):
            if len(ch) > chunk_size:
                print(f"[selftest] FAIL chunk {name}[{i}] {len(ch)} > {chunk_size}")
                return 1
        joined = b"".join(chunks)
        decoded = _rle_decompressed(joined, len(plane))
        if decoded != plane:
            print(f"[selftest] FAIL {name} RLE round-trip ({len(decoded)} != {len(plane)})")
            return 1
        print(f"[selftest] ok {name}: {len(plane)}B -> {len(joined)}B RLE in {len(chunks)} chunks")

    # plane round-trip: every pure black/white/red pixel must decode back
    back = planes_to_rgb(bw, red, img.size[0], img.size[1])
    pa, pb = img.load(), back.load()
    bad = 0
    checked = 0
    for yy in range(img.size[1]):
        for xx in range(img.size[0]):
            a = pa[xx, yy]
            if a in ((0, 0, 0), (255, 255, 255), (255, 0, 0)):
                checked += 1
                if pb[xx, yy] != a:
                    bad += 1
    print(f"[selftest] pure-colour round-trip: {checked} px checked, {bad} wrong")
    if bad == 0:
        print("[selftest] PASS")
        return 0
    print("[selftest] FAIL: bitplane encode/decode mismatch")
    return 1


async def run_push(planes, cfg: dict) -> None:
    ble = cfg["ble"]
    display = cfg["display"]
    await push_display(
        planes,
        model_id=int(display["model_id"]),
        device=ble["device"],
        mtu=int(ble.get("mtu", 247)),
        scan_timeout=float(ble.get("scan_timeout", 10.0)),
        interleave=int(ble.get("interleave", 50)),
        sleep_after_push=bool(display.get("sleep_after_push", False)),
        patch_wakeup_pin=bool(ble.get("patch_wakeup_pin", True)),
        pacing_ms=float(ble.get("pacing_ms", 0.0)),
        hold_after_refresh=float(ble.get("hold_after_refresh", 15.0)),
    )


async def run_probe(cfg: dict) -> None:
    ble = cfg["ble"]
    display = cfg["display"]
    await probe(
        device=ble["device"],
        model_id=int(display["model_id"]),
        mtu=int(ble.get("mtu", 247)),
        scan_timeout=float(ble.get("scan_timeout", 10.0)),
    )


async def run_test_screen(cfg: dict) -> None:
    ble = cfg["ble"]
    display = cfg["display"]
    await test_screen(
        device=ble["device"],
        model_id=int(display["model_id"]),
        mtu=int(ble.get("mtu", 247)),
        scan_timeout=float(ble.get("scan_timeout", 10.0)),
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    cfg = load_config(args.config)
    print(f"[cfg] using {cfg['_path']}")

    if args.selftest:
        font_path = resolve_font_path(args.font or cfg["render"].get("font", ""))
        return run_selftest(cfg, font_path)

    if args.probe:
        try:
            asyncio.run(run_probe(cfg))
            return 0
        except BlePushError as e:
            print(f"[error] {e}", file=sys.stderr)
            return 1

    if args.test_screen:
        try:
            asyncio.run(run_test_screen(cfg))
            return 0
        except BlePushError as e:
            print(f"[error] {e}", file=sys.stderr)
            return 1

    def do_once() -> int:
        if args.sample:
            balance = sample_balance()
            print("[fetch] using synthetic sample snapshot")
        else:
            balance, _raw = load_balance(args, cfg)
            print("[fetch] usage snapshot:")
        print(balance.summary())
        font_path = resolve_font_path(args.font or cfg["render"].get("font", ""))
        planes = render(balance, cfg, font_path)
        if args.dry_run:
            print("[dry-run] skipping BLE push")
            return 0
        asyncio.run(run_push(planes, cfg))
        return 0

    if args.dry_run or args.once:
        try:
            return do_once()
        except (UsageFetchError, BlePushError) as e:
            print(f"[error] {e}", file=sys.stderr)
            return 1

    if args.loop:
        interval = int(cfg["schedule"].get("interval_minutes", 5)) * 60
        print(f"[loop] pushing every {interval // 60} min (Ctrl-C to stop)")
        while True:
            try:
                do_once()
            except (UsageFetchError, BlePushError) as e:
                print(f"[error] {e}", file=sys.stderr)
            time.sleep(interval)
        return 0

    print("no action given (use --selftest / --probe / --dry-run / --once / --loop)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())