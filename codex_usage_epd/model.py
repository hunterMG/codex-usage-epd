"""Data model for Codex usage quota snapshots.

Quota field names mirror the `wham/usage` payload decoded by CodexBar's
CodexOAuthUsageFetcher. Per-model token totals come from today's local Codex
session logs, like CodexBar's local token-history view.
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
class ModelTokenUsage:
    id: str  # e.g. "gpt-5.6-sol"
    tokens: int


@dataclass
class Balance:
    plan_type: str | None
    windows: list[Window] = field(default_factory=list)  # global windows
    models: list[ModelTokenUsage] = field(default_factory=list)
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
            lines.append(f"  today {m.id}: {m.tokens} tokens")
        if self.has_credits:
            lines.append(f"  credits: balance={self.credits_balance} unlimited={self.credits_unlimited}")
        return "\n".join(lines)
