"""BLE push of the two bitplanes to an EPD-nRF5 device.

Protocol (EPD-nRF5/EPD/EPD_service.c + html/js/main.js):
  - service 62750001-d828-918d-fb46-b6c11c675aec, char 62750002
  - INIT  0x01 [model_id]
  - WRITE_IMG 0x30 [flags] [payload]   flags: bit0=1 red plane, bit1=1 first
    chunk (begin), bit2=1 RLE-encoded payload
  - REFRESH 0x05, SLEEP 0x06
  - chunk size = (ATT_MTU - 5); the device's `max_data_len` is MTU - 3.
"""

from __future__ import annotations

import asyncio
import os

from cux_rle import raw_chunks, rle_chunks

BLE_EPD_SVC = "62750001-d828-918d-fb46-b6c11c675aec"
BLE_EPD_CHAR = "62750002-d828-918d-fb46-b6c11c675aec"

CMD_INIT = 0x01
CMD_REFRESH = 0x05
CMD_SLEEP = 0x06
CMD_WRITE_IMG = 0x30
CMD_SET_CONFIG = 0x90

DEFAULT_DEVICE_NAME = "NRF_EPD"


class BlePushError(Exception):
    pass


async def _write(client, char_uuid: str, data: bytes, response: bool) -> None:
    await client.write_gatt_char(char_uuid, data, response=response)


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


async def push_display(
    planes: tuple[bytes, bytes],
    model_id: int,
    device: str,
    mtu: int = 247,
    scan_timeout: float = 10.0,
    interleave: int = 4,
    sleep_after_push: bool = True,
    patch_wakeup_pin: bool = True,
) -> None:
    from bleak import BleakClient

    address = await _resolve_device(device, scan_timeout)

    async with BleakClient(address, timeout=30) as client:
        try:
            await client.request_mtu(mtu)
        except Exception:
            pass  # older peripherals may not respond to MTU exchange

        conn_mtu = getattr(client, "mtu_size", 0) or 23
        chunk_size = conn_mtu - 5
        if chunk_size < 8:
            raise BlePushError(f"ATT MTU too small for transfers: {conn_mtu}")

        # INIT the display with the panel model id
        await _write(client, BLE_EPD_CHAR, bytes([CMD_INIT, model_id & 0xFF]), response=False)

        for red_plane, plane_bytes in enumerate(planes):
            compressed = rle_chunks(plane_bytes, chunk_size)
            use_rle = sum(len(c) for c in compressed) < len(plane_bytes)
            chunks = compressed if use_rle else raw_chunks(plane_bytes, chunk_size)
            print(
                f"[ble] {'red ' if red_plane else 'black'} plane "
                f"{len(plane_bytes)}B -> {len(chunks)} chunks "
                f"({'RLE' if use_rle else 'raw'}) chunk={chunk_size}B"
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
                await _write(client, BLE_EPD_CHAR, msg, response=response)
                if response:
                    await asyncio.sleep(0.02)

        await _write(client, BLE_EPD_CHAR, bytes([CMD_REFRESH]), response=False)
        print("[ble] refresh sent")
        if sleep_after_push:
            await _write(client, BLE_EPD_CHAR, bytes([CMD_SLEEP]), response=False)
            print("[ble] display sleeping")
