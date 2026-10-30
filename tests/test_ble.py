import asyncio

import pytest

from codex_usage_epd import ble
from codex_usage_epd.ble import CMD_SET_TIME, _set_time_command


def test_set_time_command_encodes_timestamp_timezone_and_mode():
    assert _set_time_command(0x01020304, 0, 8) == bytes([CMD_SET_TIME, 1, 2, 3, 4, 8, 0])


def test_set_time_command_encodes_negative_timezone_as_signed_byte():
    assert _set_time_command(0, 1, -5)[5] == 0xFB


def test_set_time_command_wraps_timestamp_to_32_bits():
    assert _set_time_command(0x101020304, 2, 0) == bytes([CMD_SET_TIME, 0x01, 0x02, 0x03, 0x04, 0, 2])


class _FakeClient:
    def __init__(self, _address, timeout=30):
        self.timeout = timeout
        self.writes: list[bytes] = []
        self.slots = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def start_notify(self, _char, callback):
        if self.slots:
            callback(None, bytearray(self.slots.encode()))
        callback(None, bytearray(b"mtu=247 rle=1"))

    async def write_gatt_char(self, _char, data, response=True):
        self.writes.append(bytes(data))


@pytest.mark.parametrize(("slots", "expect_set_time"), [("slots=3 0 0", True), (None, False)])
def test_push_display_stops_slideshow_only_on_slot_firmware(monkeypatch, slots, expect_set_time):
    clients: list[_FakeClient] = []

    def make_client(address, timeout=30):
        client = _FakeClient(address, timeout)
        client.slots = slots
        clients.append(client)
        return client

    async def fake_resolve(*_args, **_kwargs):
        return "fake-device"

    monkeypatch.setattr(ble, "_resolve_device", fake_resolve)
    monkeypatch.setattr("bleak.BleakClient", make_client)

    asyncio.run(
        ble.push_display(
            (b"\x00\x00\x00\x00", b"\x00\x00\x00\x00"),
            model_id=2,
            device="fake-device",
            hold_after_refresh=0,
            slot=0,
        )
    )

    writes = clients[0].writes
    assert any(w[:2] == bytes([ble.CMD_SET_SLOT, 0x01]) for w in writes)
    assert any(w[0] == ble.CMD_SET_TIME for w in writes) is expect_set_time
