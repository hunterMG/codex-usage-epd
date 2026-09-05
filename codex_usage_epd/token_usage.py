"""Read today's per-model token usage from local Codex rollout logs.

Codex records cumulative totals and a per-event ``last_token_usage`` in JSONL
files below ``$CODEX_HOME/sessions``. This follows the same core accounting
used by CodexBar: attribute each event to the active turn-context model and
count input + output tokens (cached input is a subset of input).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .model import ModelTokenUsage


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class _Totals:
    input: int
    cached: int
    output: int
    reasoning: int | None = None


def _usage_totals(raw: Any) -> _Totals | None:
    if not isinstance(raw, dict):
        return None
    has_components = "input_tokens" in raw or "output_tokens" in raw
    if has_components:
        output = _nonnegative_int(raw.get("output_tokens"))
        reasoning = (
            min(_nonnegative_int(raw.get("reasoning_output_tokens")), output)
            if "reasoning_output_tokens" in raw
            else None
        )
        return _Totals(
            input=_nonnegative_int(raw.get("input_tokens")),
            cached=max(
                _nonnegative_int(raw.get("cached_input_tokens")),
                _nonnegative_int(raw.get("cache_read_input_tokens")),
            ),
            output=output,
            reasoning=reasoning,
        )
    if "total_tokens" in raw:
        return _Totals(input=_nonnegative_int(raw.get("total_tokens")), cached=0, output=0)
    return None


def _optional_add(left: int | None, right: int | None) -> int | None:
    return left + right if left is not None and right is not None else None


def _optional_delta(baseline: int | None, current: int | None, has_baseline: bool) -> int | None:
    if current is None:
        return None
    if not has_baseline:
        return current
    return max(0, current - baseline) if baseline is not None else None


def _add(left: _Totals, right: _Totals) -> _Totals:
    return _Totals(
        input=left.input + right.input,
        cached=left.cached + right.cached,
        output=left.output + right.output,
        reasoning=_optional_add(left.reasoning, right.reasoning),
    )


def _maximum(left: _Totals | None, right: _Totals) -> _Totals:
    if left is None:
        return right
    if left.reasoning is None:
        reasoning = right.reasoning
    elif right.reasoning is None:
        reasoning = left.reasoning
    else:
        reasoning = max(left.reasoning, right.reasoning)
    return _Totals(
        input=max(left.input, right.input),
        cached=max(left.cached, right.cached),
        output=max(left.output, right.output),
        reasoning=reasoning,
    )


def _minimum(left: _Totals, right: _Totals) -> _Totals:
    reasoning = (
        min(left.reasoning, right.reasoning)
        if left.reasoning is not None and right.reasoning is not None
        else None
    )
    return _Totals(
        input=min(left.input, right.input),
        cached=min(left.cached, right.cached),
        output=min(left.output, right.output),
        reasoning=reasoning,
    )


def _equal(left: _Totals | None, right: _Totals | None) -> bool:
    if left is None or right is None:
        return left is right
    # CodexBar deliberately ignores the optional reasoning subset here.
    return left.input == right.input and left.cached == right.cached and left.output == right.output


def _at_least(left: _Totals, right: _Totals) -> bool:
    return left.input >= right.input and left.cached >= right.cached and left.output >= right.output


def _at_most(left: _Totals, right: _Totals) -> bool:
    return left.input <= right.input and left.cached <= right.cached and left.output <= right.output


def _total_delta(baseline: _Totals | None, current: _Totals) -> _Totals:
    base = baseline or _Totals(0, 0, 0)
    return _Totals(
        input=max(0, current.input - base.input),
        cached=max(0, current.cached - base.cached),
        output=max(0, current.output - base.output),
        reasoning=_optional_delta(baseline.reasoning if baseline else None, current.reasoning, baseline is not None),
    )


def _divergent_delta(raw: _Totals | None, counted: _Totals | None, current: _Totals) -> _Totals:
    raw = raw or _Totals(0, 0, 0)
    counted = counted or _Totals(0, 0, 0)

    def component(raw_value: int, counted_value: int, current_value: int) -> int:
        baseline = raw_value if current_value >= raw_value else counted_value
        return max(0, current_value - baseline)

    reasoning = None
    if raw.reasoning is not None and counted.reasoning is not None and current.reasoning is not None:
        baseline = raw.reasoning if current.reasoning >= raw.reasoning else counted.reasoning
        reasoning = max(0, current.reasoning - baseline)
    return _Totals(
        input=component(raw.input, counted.input, current.input),
        cached=component(raw.cached, counted.cached, current.cached),
        output=component(raw.output, counted.output, current.output),
        reasoning=reasoning,
    )


def _contained_delta(watermark: _Totals | None, counted: _Totals | None, current: _Totals) -> _Totals:
    water = watermark or _Totals(0, 0, 0)
    counted = counted or _Totals(0, 0, 0)

    def component(water_value: int, counted_value: int, current_value: int) -> int:
        baseline = max(water_value, counted_value) if current_value >= water_value else counted_value
        return max(0, current_value - baseline)

    reasoning = None
    if water.reasoning is not None and counted.reasoning is not None and current.reasoning is not None:
        reasoning = component(water.reasoning, counted.reasoning, current.reasoning)
    return _Totals(
        input=component(water.input, counted.input, current.input),
        cached=component(water.cached, counted.cached, current.cached),
        output=component(water.output, counted.output, current.output),
        reasoning=reasoning,
    )


def _looks_like_stale_regression(current: _Totals, previous: _Totals, last: _Totals) -> bool:
    reasoning_regressed = (
        current.reasoning is not None
        and previous.reasoning is not None
        and current.reasoning < previous.reasoning
    )
    if not (
        current.input < previous.input
        or current.cached < previous.cached
        or current.output < previous.output
        or reasoning_regressed
    ):
        return False
    previous_sum = previous.input + previous.cached + previous.output + (previous.reasoning or 0)
    current_sum = current.input + current.cached + current.output + (current.reasoning or 0)
    last_sum = last.input + last.cached + last.output + (last.reasoning or 0)
    if previous_sum <= 0 or current_sum <= 0 or last_sum <= 0:
        return False
    return current_sum * 100 >= previous_sum * 98 or current_sum + last_sum * 2 >= previous_sum


@dataclass
class _Counter:
    """CodexBar-compatible containment for cumulative token snapshots."""

    counted: _Totals | None = None
    raw_baseline: _Totals | None = None
    watermark: _Totals | None = None
    seen: list[_Totals] = field(default_factory=list)
    divergent: bool = False
    interleaved: bool = False

    def apply(self, last: _Totals | None, total: _Totals | None) -> _Totals | None:
        if total is not None:
            if any(_equal(item, total) for item in self.seen):
                return None
            stale_baseline = self.watermark or self.raw_baseline
            if stale_baseline is not None and _looks_like_stale_regression(
                total, stale_baseline, last or _Totals(0, 0, 0)
            ):
                return None
            if self.watermark is not None and (
                total.input < self.watermark.input
                or total.cached < self.watermark.cached
                or total.output < self.watermark.output
            ):
                self.interleaved = True

        watermark_baseline = self.watermark or self.raw_baseline
        if last is not None:
            delta = last
            if total is not None:
                if self.interleaved:
                    delta = _minimum(_contained_delta(watermark_baseline, self.counted, total), last)
                else:
                    candidate = _total_delta(watermark_baseline, total)
                    if (
                        not self.divergent
                        and watermark_baseline is not None
                        and _at_least(total, watermark_baseline)
                        and _at_most(candidate, last)
                    ):
                        delta = candidate
                self._commit_delta(delta, total)
            else:
                base = self.counted or _Totals(0, 0, 0, 0 if last.reasoning is not None else None)
                self.counted = _add(base, delta)
                self.raw_baseline = self.counted
                self.watermark = _maximum(self.watermark, self.counted)
        elif total is not None:
            if self.interleaved:
                delta = _contained_delta(watermark_baseline, self.counted, total)
            elif self.divergent:
                delta = _divergent_delta(watermark_baseline, self.counted, total)
            else:
                delta = _total_delta(watermark_baseline, total)
            self._commit_delta(delta, total)
        else:
            return None

        if total is not None:
            self.watermark = _maximum(self.watermark, total)
            if not any(_equal(item, total) for item in self.seen):
                self.seen.append(total)
                self.seen = self.seen[-64:]
        return delta

    def _commit_delta(self, delta: _Totals, raw_baseline: _Totals) -> None:
        base = self.counted or _Totals(0, 0, 0, 0 if delta.reasoning is not None else None)
        self.counted = _add(base, delta)
        self.raw_baseline = raw_baseline
        if not _equal(self.raw_baseline, self.counted):
            self.divergent = True


def _model_from(*sources: Any) -> str | None:
    for value in sources:
        if isinstance(value, str):
            value = value.strip()
            if value:
                return value
    return None


def _candidate_files(codex_home: Path, start: datetime) -> Iterable[Path]:
    """Yield logs that could contain an event from the requested local day."""
    roots = (codex_home / "sessions", codex_home / "archived_sessions")
    start_epoch = start.timestamp()
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.jsonl"):
            try:
                if path.stat().st_mtime >= start_epoch:
                    yield path
            except OSError:
                continue


def _scan_file(
    path: Path,
    start: datetime,
    end: datetime,
    totals: dict[str, int],
    seen_events: set[tuple[Any, ...]],
) -> None:
    current_model: str | None = None
    counter = _Counter()

    try:
        lines = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return

    with lines:
        for line in lines:
            # Avoid decoding prompts and tool output; neither can contribute to
            # this report, and turn_context lines can be unusually large.
            if '"turn_context"' not in line and '"token_count"' not in line:
                continue
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(record, dict):
                continue

            record_type = record.get("type")
            payload = record.get("payload")
            payload = payload if isinstance(payload, dict) else {}

            if record_type == "turn_context":
                info = payload.get("info")
                info = info if isinstance(info, dict) else {}
                # An explicitly blank model clears stale model evidence.
                if any(key in payload for key in ("model", "model_name")) or any(
                    key in info for key in ("model", "model_name")
                ):
                    current_model = _model_from(
                        payload.get("model"), payload.get("model_name"), info.get("model"), info.get("model_name")
                    )
                continue

            if record_type != "event_msg" or payload.get("type") != "token_count":
                continue

            info = payload.get("info")
            if not isinstance(info, dict):
                continue
            total = _usage_totals(info.get("total_token_usage"))
            last = _usage_totals(info.get("last_token_usage"))
            delta = counter.apply(last, total)
            if delta is None:
                continue

            occurred_at = _timestamp(record.get("timestamp"))
            if occurred_at is None or not (start <= occurred_at.astimezone(start.tzinfo) < end):
                continue

            model = _model_from(
                current_model,
                info.get("model"),
                info.get("model_name"),
                payload.get("model"),
                record.get("model"),
            ) or "unknown"

            # Fork/subagent rollouts can contain byte-for-byte copies of parent
            # token events. Suppress those copies across files.
            fingerprint = (record.get("timestamp"), model, last, total)
            if fingerprint in seen_events:
                continue
            seen_events.add(fingerprint)
            totals[model] += delta.input + delta.output


def read_today_model_usage(
    codex_home: Path,
    now: datetime | None = None,
    limit: int = 3,
) -> list[ModelTokenUsage]:
    """Return today's most-used Codex models, sorted by input + output tokens."""
    local_now = now or datetime.now().astimezone()
    if local_now.tzinfo is None:
        local_now = local_now.astimezone()
    start = datetime.combine(local_now.date(), time.min, tzinfo=local_now.tzinfo)
    end = start + timedelta(days=1)

    totals: dict[str, int] = defaultdict(int)
    seen_events: set[tuple[Any, ...]] = set()
    for path in _candidate_files(codex_home, start):
        _scan_file(path, start, end, totals, seen_events)

    ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    return [ModelTokenUsage(id=model, tokens=tokens) for model, tokens in ranked[: max(0, limit)]]
