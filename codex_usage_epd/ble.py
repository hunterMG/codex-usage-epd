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

Slot firmware (epdiy.cn web client) adds:
  - SET_SLOT 0x31 [action, slot]  action=0 select write slot, action=1 display
  - device announces "slots=<count> <usedMask> [selected]" (usedMask decimal)
    after connecting; sending SET_SLOT [0, slot] before INIT routes WRITE_IMG
    into that slot.
"""

from __future__ import annotations

import asyncio
import re
import time

from .rle import raw_chunks, rle_chunks

BLE_EPD_SVC = "62750001-d828-918d-fb46-b6c11c675aec"
BLE_EPD_CHAR = "62750002-d828-918d-fb46-b6c11c675aec"

CMD_INIT = 0x01
CMD_CLEAR = 0x02
CMD_REFRESH = 0x05
CMD_SLEEP = 0x06
CMD_WRITE_IMG = 0x30
CMD_SET_SLOT = 0x31
CMD_SET_CONFIG = 0x90

DEFAULT_DEVICE_NAME = "NRF_EPD"
_MTU_RE = re.compile(rb"mtu=(\d+)")
_SLOTS_RE = re.compile(rb"slots=(\d+)\s+(\d+|0x[0-9A-Fa-f]+)(?:\s+(-?\d+))?")


class BlePushError(Exception):
    pass


async def _resolve_device(device: str, scan_timeout: float, scan_retries: int = 3) -> str:
    if device.lower() != "auto":
        return device  # caller passes a concrete address or name substring
    from bleak import BleakScanner

    print(f"[ble] scanning for '{DEFAULT_DEVICE_NAME}' ...")
    seen: dict[str, str] = {}
    for attempt in range(1, max(1, scan_retries) + 1):
        if attempt > 1:
            print(f"[ble] scan attempt {attempt}/{scan_retries} ...")
        target: asyncio.Future[tuple[str, str]] = asyncio.get_running_loop().create_future()

        def _cb(device_, _adv, target=target):
            if device_.name:
                seen[device_.address] = device_.name
            if device_.name and DEFAULT_DEVICE_NAME in device_.name and not target.done():
                target.set_result((device_.name, device_.address))

        try:
            async with BleakScanner(detection_callback=_cb):
                name, address = await asyncio.wait_for(target, timeout=scan_timeout)
        except asyncio.TimeoutError:
            continue
        print(f"[ble] found {name} @ {address}")
        return address

    names = [f"{n} {a}" for a, n in seen.items()]
    raise BlePushError(
        f"no '{DEFAULT_DEVICE_NAME}' device found after {max(1, scan_retries)} scans. seen: {names}"
    )


async def _request_mtu(client, mtu: int) -> None:
    # bleak 3.x dropped request_mtu on some backends (macOS/CB negotiates MTU
    # automatically); call it only when the backend exposes it.
    request_mtu = getattr(client, "request_mtu", None)
    if request_mtu is None:
        return
    try:
        await request_mtu(mtu)
    except Exception as exc:  # noqa: BLE001 — backend may not support MTU exchange
        print(f"[ble] request_mtu skipped: {exc}")


class _Notifier:
    """Synchronous notify callback (bleak 3.x callbacks are sync)."""

    def __init__(self) -> None:
        self.config: bytes | None = None
        self.mtu: int | None = None
        self.messages: list[str] = []
        self.slots_count: int = 0
        self.slots_used_mask: int = 0
        self.slots_selected: int | None = None

    def __call__(self, _characteristic: object, data: bytearray) -> None:
        blob = bytes(data)
        if self.config is None and len(blob) >= 11 and not blob.startswith((b"mtu=", b"t=", b"slots=")):
            self.config = blob
        m = _MTU_RE.search(blob)
        if m:
            self.mtu = int(m.group(1))
        sm = _SLOTS_RE.search(blob)
        if sm:
            self.slots_count = int(sm.group(1))
            mask = sm.group(2).decode(errors="replace")
            self.slots_used_mask = int(mask, 16) if mask.lower().startswith("0x") else int(mask, 10)
            self.slots_selected = int(sm.group(3)) if sm.group(3) else None
        if blob.startswith((b"mtu=", b"t=", b"slots=")):
            self.messages.append(blob.decode(errors="replace"))


def _config_bytes_to_str(cfg: bytes) -> str:
    if len(cfg) < 11:
        return cfg.hex()
    names = ["mosi", "sclk", "cs", "dc", "rst", "busy", "bs", "model", "wakeup", "led", "en"]
    return ", ".join(f"{names[i]}={cfg[i]:#04x}" for i in range(11))


def _resolve_slot(slot_cfg, notifier: _Notifier) -> int | None:
    """Map the configured ``slot`` to a concrete slot index to write into.

    - ``None`` / ``"none"`` / ``-1`` -> no SET_SLOT command (raw behaviour)
    - ``int >= 0``                   -> that exact slot
    - ``"auto"``                     -> first free slot per the device's
                                        usedMask; falls back to the currently
                                        selected slot, then slot 0
    Returns ``None`` (no slot command) when the device does not advertise
    slot support and the caller asked for ``"auto"``.
    """
    if slot_cfg is None:
        return None
    if isinstance(slot_cfg, str):
        s = slot_cfg.strip().lower()
        if s in ("none", "off", "disabled"):
            return None
        if s == "auto":
            count = notifier.slots_count or 0
            if count <= 0:
                return None
            mask = notifier.slots_used_mask or 0
            for i in range(count):
                if not (mask >> i) & 1:
                    return i
            cur = notifier.slots_selected
            return cur if cur is not None and 0 <= cur < count else 0
        try:
            slot_cfg = int(s)
        except ValueError:
            raise BlePushError(f"invalid ble.slot value: {slot_cfg!r}")
    slot_idx = int(slot_cfg)
    if slot_idx < 0:
        return None
    if notifier.slots_count and slot_idx >= notifier.slots_count:
        print(f"[ble] warning: slot {slot_idx} >= device slot count {notifier.slots_count}")
    return slot_idx


async def probe(
    device: str,
    model_id: int,
    mtu: int,
    scan_timeout: float,
    scan_retries: int = 3,
) -> None:
    """Connect, subscribe, INIT, and report what the device says."""
    from bleak import BleakClient

    address = await _resolve_device(device, scan_timeout, scan_retries)
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
        except Exception:  # noqa: BLE001, S110 — version characteristic is optional
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


async def test_screen(
    device: str,
    model_id: int,
    mtu: int,
    scan_timeout: float,
    scan_retries: int = 3,
) -> None:
    """Minimal firmware path check: INIT + CLEAR(with refresh). The screen
    should flash to white and the REFRESH inside CLEAR should block a few
    seconds (busy wait), proving the EPD driver is bound and commands work."""
    from bleak import BleakClient

    address = await _resolve_device(device, scan_timeout, scan_retries)
    notifier = _Notifier()
    async with BleakClient(address, timeout=30) as client:
        await _request_mtu(client, mtu)
        await client.start_notify(BLE_EPD_CHAR, notifier)
        t0 = time.monotonic()
        await client.write_gatt_char(BLE_EPD_CHAR, bytes([CMD_INIT, model_id & 0xFF]), response=True)
        print(f"[test] INIT done in {time.monotonic() - t0:.2f}s")
        t1 = time.monotonic()
        await client.write_gatt_char(BLE_EPD_CHAR, bytes([CMD_CLEAR]), response=True)
        print(f"[test] CLEAR (full refresh) done in {time.monotonic() - t1:.2f}s")
        print(f"[test] mtu reported: {notifier.mtu}")


async def push_display(
    planes: tuple[bytes, bytes],
    model_id: int,
    device: str,
    mtu: int = 247,
    scan_timeout: float = 10.0,
    scan_retries: int = 3,
    interleave: int = 50,
    sleep_after_push: bool = False,
    patch_wakeup_pin: bool = True,
    pacing_ms: float = 0.0,
    hold_after_refresh: float = 15.0,
    slot: int | str | None = None,
) -> None:
    """Mirror the proven-good web client sequence (html/js/main.js sendimg).

    Order matters: INIT [0x01] (no model byte; firmware falls back to the
    stored config.model_id) -> WRITE_IMG chunks (RLE, interleave write/response)
    -> REFRESH -> stay connected while the panel finishes its busy refresh.
    The web client never sends SLEEP and does not disconnect during refresh;
    sending SLEEP or dropping the link mid-refresh aborts the image.

    On slot firmware (epdiy.cn) a ``SET_SLOT [0, slot]`` is sent before INIT,
    exactly like the web client, so WRITE_IMG lands in the chosen slot instead
    of overwriting whatever slot the device currently has selected.
    """
    from bleak import BleakClient

    address = await _resolve_device(device, scan_timeout, scan_retries)
    notifier = _Notifier()

    async with BleakClient(address, timeout=30) as client:
        await _request_mtu(client, mtu)
        await client.start_notify(BLE_EPD_CHAR, notifier)

        # "auto" needs the device's slot announcement before picking a free slot
        if isinstance(slot, str) and slot.strip().lower() == "auto":
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and notifier.slots_count == 0:
                await asyncio.sleep(0.05)
        use_slot = _resolve_slot(slot, notifier)
        if use_slot is not None:
            occupied = bool(notifier.slots_count and (notifier.slots_used_mask >> use_slot) & 1)
            print(
                f"[ble] SET_SLOT write slot {use_slot}"
                + (" (occupied, will overwrite)" if occupied else "")
            )
            await client.write_gatt_char(
                BLE_EPD_CHAR, bytes([CMD_SET_SLOT, 0x00, use_slot & 0xFF]), response=True
            )

        # INIT without a model byte, exactly like the web client. The device
        # replies with the epd config + "mtu=<n> rle=1" + "t=<unix>".
        t0 = time.monotonic()
        await client.write_gatt_char(BLE_EPD_CHAR, bytes([CMD_INIT]), response=True)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and notifier.mtu is None:
            await asyncio.sleep(0.05)
        print(f"[ble] INIT + mtu wait: {time.monotonic() - t0:.2f}s")
        if notifier.config is not None:
            print(f"[ble] epd config: {_config_bytes_to_str(notifier.config)}")
        if notifier.slots_count:
            print(
                f"[ble] device slots: count={notifier.slots_count} "
                f"used=0x{notifier.slots_used_mask:x} selected={notifier.slots_selected}"
            )
        for msg in notifier.messages:
            print(f"[ble] device: {msg}")

        negotiated = getattr(client, "mtu_size", 0) or 23
        max_data_len = notifier.mtu or (negotiated - 3)
        chunk_size = max_data_len - 2
        if chunk_size < 8:
            raise BlePushError(f"max data len too small for transfers: {max_data_len}")
        print(f"[ble] negotiated mtu={negotiated} device max_data_len={max_data_len} chunk={chunk_size}B")

        # if configured, force wakeup_pin=0xFF so the device keeps re-advertising
        if patch_wakeup_pin and notifier.config and len(notifier.config) >= 11 and notifier.config[8] != 0xFF:
                patched = bytearray(notifier.config[:11])
                patched[8] = 0xFF
                print("[ble] patching config wakeup_pin -> 0xFF")
                await client.write_gatt_char(BLE_EPD_CHAR, bytes([CMD_SET_CONFIG]) + bytes(patched), response=True)
                await asyncio.sleep(0.1)

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

        # REFRESH without response: the firmware blocks in SSD16xx_WaitBusy
        # while the panel refreshes, so a write-with-response would just sit
        # waiting for an ATT ack the device only sends after the busy-wait
        # finishes. Fire-and-forget, then hold the connection open long enough
        # for the panel to finish; the firmware sleeps the panel on disconnect
        # and would abort a mid-flight refresh.
        await client.write_gatt_char(BLE_EPD_CHAR, bytes([CMD_REFRESH]), response=False)
        print(f"[ble] REFRESH sent (no-response), holding {hold_after_refresh}s for panel refresh...")
        await asyncio.sleep(hold_after_refresh)
        if sleep_after_push:
            await client.write_gatt_char(BLE_EPD_CHAR, bytes([CMD_SLEEP]), response=True)
            print("[ble] display sleeping")
