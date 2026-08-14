"""Data model for Codex usage quota snapshots.

Field names mirror the `wham/usage` payload decoded by CodexBar's
CodexOAuthUsageFetcher (rate_limit.primary_window/secondary_window,
credits, additional_rate_limits, plan_type).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Window:
    name: str  # window identity e.g. "five_hour", "weekly"
    label: str  # short display label e.g. "5H", "WK"
    used_percent: float  # 0..100 used
    resets_at: int | None  # unix epoch seconds
    limit_seconds: int | None

    @property
    def remaining_percent(self) -> float:
        return max(0.0, min(100.0, 100.0 - self.used_percent))


@dataclass
class ModelUsage:
    id: str  # e.g. "gpt-5.3-codex-spark"
    windows: list[Window] = field(default_factory=list)


@dataclass
class Balance:
    plan_type: str | None
    windows: list[Window] = field(default_factory=list)  # global windows
    models: list[ModelUsage] = field(default_factory=list)
    credits_balance: float | None = None
    credits_unlimited: bool = False
    has_credits: bool = False
    fetched_at: datetime = field(default_factory=datetime.now)
    source: str = "oauth"  # "oauth" | "sample"

    def window(self, name: str) -> Window | None:
        for w in self.windows:
            if w.name == name:
                return w
        return None

    def summary(self) -> str:
        lines = [f"plan: {self.plan_type}"]
        for w in self.windows:
            lines.append(f"  {w.name}: used {w.used_percent:.1f}% remaining {w.remaining_percent:.1f}%")
        for m in self.models:
            desc = ", ".join(f"{w.name}={w.used_percent:.1f}%" for w in m.windows)
            lines.append(f"  model {m.id}: {desc}")
        if self.has_credits:
            lines.append(f"  credits: balance={self.credits_balance} unlimited={self.credits_unlimited}")
        return "\n".join(lines)