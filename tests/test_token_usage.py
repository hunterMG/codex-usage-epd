import json
from datetime import datetime, timedelta, timezone

from codex_usage_epd.token_usage import read_today_model_usage


def _write_log(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records))


def _context(timestamp, model):
    return {"type": "turn_context", "timestamp": timestamp, "payload": {"model": model}}


def _usage(timestamp, last=None, total=None):
    info = {}
    if last is not None:
        info["last_token_usage"] = last
    if total is not None:
        info["total_token_usage"] = total
    return {
        "type": "event_msg",
        "timestamp": timestamp,
        "payload": {"type": "token_count", "info": info},
    }


def test_reads_today_groups_models_and_returns_top_three(tmp_path):
    log = tmp_path / "sessions/2026/09/05/rollout.jsonl"
    records = [_context("2026-09-04T16:00:01Z", "gpt-small")]
    cumulative_input = 0
    cumulative_output = 0
    for model, tokens in (
        ("gpt-small", 100_000),
        ("gpt-large", 4_000_000),
        ("gpt-medium", 2_000_000),
        ("gpt-fourth", 50_000),
    ):
        cumulative_input += tokens - 10
        cumulative_output += 10
        records.extend(
            [
                _context("2026-09-05T01:00:00Z", model),
                _usage(
                    "2026-09-05T01:00:01Z",
                    last={"input_tokens": tokens - 10, "cached_input_tokens": tokens - 20, "output_tokens": 10},
                    total={"input_tokens": cumulative_input, "output_tokens": cumulative_output},
                ),
            ]
        )
    _write_log(log, records)

    now = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
    result = read_today_model_usage(tmp_path, now=now)

    assert [(item.id, item.tokens) for item in result] == [
        ("gpt-large", 4_000_000),
        ("gpt-medium", 2_000_000),
        ("gpt-small", 100_000),
    ]


def test_filters_by_local_day_and_uses_total_deltas_when_last_is_missing(tmp_path):
    log = tmp_path / "sessions/2026/09/05/rollout.jsonl"
    _write_log(
        log,
        [
            _context("2026-09-04T15:59:00Z", "gpt-5.6-sol"),
            _usage("2026-09-04T15:59:30Z", total={"input_tokens": 100, "output_tokens": 10}),
            _usage("2026-09-04T16:00:30Z", total={"input_tokens": 250, "output_tokens": 25}),
            _usage("2026-09-05T15:59:30Z", total={"input_tokens": 400, "output_tokens": 40}),
            _usage("2026-09-05T16:00:30Z", total={"input_tokens": 800, "output_tokens": 80}),
        ],
    )

    # Supply +08:00 explicitly: the local day spans 16:00Z through 15:59Z.
    singapore = timezone(timedelta(hours=8))
    result = read_today_model_usage(tmp_path, now=datetime(2026, 9, 5, 12, tzinfo=singapore))

    assert [(item.id, item.tokens) for item in result] == [("gpt-5.6-sol", 330)]


def test_deduplicates_copied_parent_events_across_rollouts(tmp_path):
    event = _usage(
        "2026-09-05T01:00:01Z",
        last={"input_tokens": 900, "output_tokens": 100},
        total={"input_tokens": 900, "output_tokens": 100},
    )
    repeated = _usage(
        "2026-09-05T01:00:02Z",
        last={"input_tokens": 900, "output_tokens": 100},
        total={"input_tokens": 900, "output_tokens": 100},
    )
    records = [_context("2026-09-05T01:00:00Z", "gpt-5.6-sol"), event, repeated]
    _write_log(tmp_path / "sessions/2026/09/05/parent.jsonl", records)
    _write_log(tmp_path / "sessions/2026/09/05/fork.jsonl", records)

    result = read_today_model_usage(tmp_path, now=datetime(2026, 9, 5, 12, tzinfo=timezone.utc))

    assert [(item.id, item.tokens) for item in result] == [("gpt-5.6-sol", 1_000)]


def test_contains_counter_reset_in_a_rollout_spanning_two_days(tmp_path):
    log = tmp_path / "sessions/2026/09/05/rollout.jsonl"
    previous_day = {
        "input_tokens": 142_346,
        "cached_input_tokens": 118_528,
        "output_tokens": 1_180,
    }
    records = [
        _context("2026-09-04T15:00:00Z", "gpt-5.6-sol"),
        _usage("2026-09-04T15:01:00Z", last=previous_day, total=previous_day),
        _context("2026-09-05T04:29:35Z", "gpt-5.6-sol"),
        _usage(
            "2026-09-05T04:29:46Z",
            last={"input_tokens": 41_833, "cached_input_tokens": 17_920, "output_tokens": 361},
            total={"input_tokens": 41_833, "cached_input_tokens": 17_920, "output_tokens": 361},
        ),
        _usage(
            "2026-09-05T04:29:51Z",
            last={"input_tokens": 52_333, "cached_input_tokens": 41_728, "output_tokens": 151},
            total={"input_tokens": 94_166, "cached_input_tokens": 59_648, "output_tokens": 512},
        ),
        _usage(
            "2026-09-05T04:29:56Z",
            last={"input_tokens": 60_339, "cached_input_tokens": 52_224, "output_tokens": 151},
            total={"input_tokens": 154_505, "cached_input_tokens": 111_872, "output_tokens": 663},
        ),
        _usage(
            "2026-09-05T04:30:03Z",
            last={"input_tokens": 71_743, "cached_input_tokens": 60_288, "output_tokens": 330},
            total={"input_tokens": 226_248, "cached_input_tokens": 172_160, "output_tokens": 993},
        ),
        _usage(
            "2026-09-05T04:30:08Z",
            last={"input_tokens": 78_745, "cached_input_tokens": 71_680, "output_tokens": 192},
            total={"input_tokens": 304_993, "cached_input_tokens": 243_840, "output_tokens": 1_185},
        ),
    ]
    _write_log(log, records)

    result = read_today_model_usage(tmp_path, now=datetime(2026, 9, 5, 12, tzinfo=timezone.utc))

    # The raw cumulative counter is 306,178, but 227,428 belongs to the
    # reset/interleaved prefix and must not be counted again.
    assert [(item.id, item.tokens) for item in result] == [("gpt-5.6-sol", 78_750)]
