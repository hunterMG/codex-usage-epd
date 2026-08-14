"""BLE push of the two bitplanes to an EPD-nRF5 device.

Protocol (EPD-nRF5/EPD/EPD_service.c + html/js/main.js):
  - service 62750001-d828-918d-fb46-b6c11c675aec, char 62750002
  - after connecting, subscribe to notifications (CCCD write w/ response),
    then INIT 0x01 [model_id] w/ response; the device replies with the epd
    config, then "mtu=244 rle=1" and "t=..."
  - WRITE_IMG 0x30 [flags] [payload]   flags: bit0=1 red plane, bit1=1 first
    chunk (begin), bit2=1 RLE-encoded payload
  - REFRESH 0x05, SLEEP 0x06 (w/ response)
  - chunk size = (max_data_len - 2); the device reports max_data_len = MTU - 3.
"""

from __future__ import annotations

import asyncio
import re
import time

from cux_rle import raw_chunks, rle_chunks

BLE_EPD_SVC = "62750001-d828-918d-fb46-b6c11c675aec"
BLE_EPD_CHAR = "62750002-d828-918d-fb46-b6c11c675aec"

CMD_INIT = 0x01
CMD_REFRESH = 0x05
CMD_SLEEP = 0x06
CMD_WRITE_IMG = 0x30
CMD_SET_CONFIG = 0x90

DEFAULT_DEVICE_NAME = "NRF_EPD"
_MTU_RE = re.compile(rb"mtu=(\d+)")


class BlePushError(Exception):
    pass


async def _resolve_device(device: str, scan_timeout: float) -> str:
    if device.lower() != "auto":
        return device  # caller passes a concrete address or name substring
    from bleak import BleakScanner

    print(f"[ble] scanning for '{DEFAULT_DEVICE_NAME}' ...")
    devs = await BleakScanner.discover(timeout=scan_timeout)
    for d in devs:
        if d.name and DEFAULT_DEVICE_NAME in d.name:
            print(f"[ble] found {d.name} @ {d.address}")
            return d.address
    names = [f"{d.name} {d.address}" for d in devs if d.name]
    raise BlePushError(f"no '{DEFAULT_DEVICE_NAME}' device found. seen: {names}")


async def _request_mtu(client, mtu: int) -> None:
    # bleak 3.x dropped request_mtu on some backends (macOS/CB negotiates MTU
    # automatically); call it only when the backend exposes it.
    request_mtu = getattr(client, "request_mtu", None)
    if request_mtu is None:
        return
    try:
        await request_mtu(mtu)
    except Exception as exc:  # macOS/CB may not support explicit MTU exchange
        print(f"[ble] request_mtu skipped: {exc}")


class _Notifier:
    """Synchronous notify callback (bleak 3.x callbacks are sync)."""

    def __init__(self) -> None:
        self.config: bytes | None = None
        self.mtu: int | None = None
        self.messages: list[str] = []

    def __call__(self, _handle: int, data: bytearray) -> None:
        blob = bytes(data)
        if self.config is None and len(blob) in (10, 11):
            self.config = blob
        m = _MTU_RE.search(blob)
        if m:
            self.mtu = int(m.group(1))
        if blob.startswith((b"mtu=", b"t=")):
            self.messages.append(blob.decode(errors="replace"))


def _config_bytes_to_str(cfg: bytes) -> str:
    if len(cfg) < 11:
        return cfg.hex()
    names = ["mosi", "sclk", "cs", "dc", "rst", "busy", "bs", "model", "wakeup", "led", "en"]
    return ", ".join(f"{names[i]}={cfg[i]:#04x}" for i in range(11))


async def probe(device: str, model_id: int, mtu: int, scan_timeout: float) -> None:
    """Connect, subscribe, INIT, and report what the device says."""
    from bleak import BleakClient

    address = await _resolve_device(device, scan_timeout)
    notifier = _Notifier()
    async with BleakClient(address, timeout=30) as client:
        await _request_mtu(client, mtu)
        await client.start_notify(BLE_EPD_CHAR, notifier)
        print(f"[probe] connected to {address}")
        print(f"[probe] negotiated mtu: {getattr(client, 'mtu_size', '?')}")
        try:
            version_char = client.services.get_characteristic("62750003-d828-918d-fb46-b6c11c675aec")
            if version_char:
                ver = await client.read_gatt_char(version_char)
                print(f"[probe] firmware version: 0x{ver[0]:02x}")
        except Exception:
            pass
        await client.write_gatt_char(BLE_EPD_CHAR, bytes([CMD_INIT, model_id & 0xFF]), response=True)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and notifier.mtu is None:
            await asyncio.sleep(0.05)
        if notifier.config is not None:
            print(f"[probe] epd config: {_config_bytes_to_str(notifier.config)}")
        print(f"[probe] mtu reported by device: {notifier.mtu}")
        for msg in notifier.messages:
            print(f"[probe] device: {msg}")


async def push_display(
    planes: tuple[bytes, bytes],
    model_id: int,
    device: str,
    mtu: int = 247,
    scan_timeout: float = 10.0,
    interleave: int = 4,
    sleep_after_push: bool = True,
    patch_wakeup_pin: bool = True,
    pacing_ms: float = 5.0,
) -> None:
    from bleak import BleakClient

    address = await _resolve_device(device, scan_timeout)
    notifier = _Notifier()

    async with BleakClient(address, timeout=30) as client:
        await _request_mtu(client, mtu)
        await client.start_notify(BLE_EPD_CHAR, notifier)

        # INIT first (w/ response) so the firmware binds the EPD driver and
        # reports the authoritative max_data_len in the 'mtu=' notification.
        await client.write_gatt_char(BLE_EPD_CHAR, bytes([CMD_INIT, model_id & 0xFF]), response=True)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and notifier.mtu is None:
            await asyncio.sleep(0.05)

        negotiated = getattr(client, "mtu_size", 0) or 23
        max_data_len = notifier.mtu or (negotiated - 3)
        chunk_size = max_data_len - 2
        if chunk_size < 8:
            raise BlePushError(f"max data len too small for transfers: {max_data_len}")
        print(f"[ble] negotiated mtu={negotiated} device max_data_len={max_data_len} chunk={chunk_size}B")

        # if configured, force wakeup_pin=0xFF so the device keeps re-advertising
        if patch_wakeup_pin and notifier.config and len(notifier.config) >= 11:
            if notifier.config[8] != 0xFF:
                patched = bytearray(notifier.config[:11])
                patched[8] = 0xFF
                print("[ble] patching config wakeup_pin -> 0xFF")
                await client.write_gatt_char(BLE_EPD_CHAR, bytes([CMD_SET_CONFIG]) + bytes(patched), response=True)
                await asyncio.sleep(0.1)

        # re-INIT after any config patch (config write does not re-init the panel)
        await client.write_gatt_char(BLE_EPD_CHAR, bytes([CMD_INIT, model_id & 0xFF]), response=True)

        for red_plane, plane_bytes in enumerate(planes):
            compressed = rle_chunks(plane_bytes, chunk_size)
            use_rle = sum(len(c) for c in compressed) < len(plane_bytes)
            chunks = compressed if use_rle else raw_chunks(plane_bytes, chunk_size)
            print(
                f"[ble] {'red ' if red_plane else 'black'} plane "
                f"{len(plane_bytes)}B -> {len(chunks)} chunks "
                f"({'RLE' if use_rle else 'raw'})"
            )
            for i, chunk in enumerate(chunks):
                flags = 0x00
                if red_plane:
                    flags |= 0x01
                if i == 0:
                    flags |= 0x02  # begin
                if use_rle:
                    flags |= 0x04
                msg = bytes([CMD_WRITE_IMG, flags]) + chunk
                response = (i % (interleave + 1)) == 0
                await client.write_gatt_char(BLE_EPD_CHAR, msg, response=response)
                if pacing_ms:
                    await asyncio.sleep(pacing_ms / 1000.0)

        await client.write_gatt_char(BLE_EPD_CHAR, bytes([CMD_REFRESH]), response=True)
        print("[ble] refresh sent (w/ response)")
        if sleep_after_push:
            await client.write_gatt_char(BLE_EPD_CHAR, bytes([CMD_SLEEP]), response=True)
            print("[ble] display sleeping")