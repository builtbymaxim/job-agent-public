"""Token usage and cost tracking for Gemini API calls."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# USD per 1M tokens (input, output). Verify at https://ai.google.dev/pricing before
# relying on cost numbers — the 3.x figures below come from secondary sources.
PRICING: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash":      (0.30, 2.50),
    "gemini-2.5-pro":        (1.25, 10.00),
    "gemini-3.5-flash":      (1.50, 9.00),
    "gemini-3.5-flash-lite": (0.10, 0.40),
    "gemini-3.6-flash":      (1.50, 9.00),
    "gemini-3.1-flash-lite": (0.10, 0.40),
}

_warned_models: set[str] = set()


@dataclass
class CallRecord:
    model: str
    prompt_tokens: int
    output_tokens: int
    thoughts_tokens: int
    cached_tokens: int

    @property
    def cost_usd(self) -> float:
        if self.model not in PRICING:
            # Silently reporting $0.00 for an unpriced model hides real spend, which
            # is how a model migration can quietly multiply the cost of a run.
            if self.model not in _warned_models:
                _warned_models.add(self.model)
                logger.warning(
                    f"No pricing entry for '{self.model}' — its cost is reported as $0.00. "
                    f"Add it to PRICING in agents/usage_tracker.py."
                )
            return 0.0
        in_price, out_price = PRICING[self.model]
        # Conservative upper bound: cached tokens are billed at lower rate by Gemini
        # but we treat them as full input for now (no cache discount applied).
        return (
            (self.prompt_tokens * in_price)
            + ((self.output_tokens + self.thoughts_tokens) * out_price)
        ) / 1_000_000


@dataclass
class UsageTracker:
    calls: list[CallRecord] = field(default_factory=list)

    def record(self, model: str, usage_metadata) -> None:
        if usage_metadata is None:
            return
        self.calls.append(CallRecord(
            model=model,
            prompt_tokens=getattr(usage_metadata, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage_metadata, "candidates_token_count", 0) or 0,
            thoughts_tokens=getattr(usage_metadata, "thoughts_token_count", 0) or 0,
            cached_tokens=getattr(usage_metadata, "cached_content_token_count", 0) or 0,
        ))

    def summary(self) -> dict:
        total_in = sum(c.prompt_tokens for c in self.calls)
        total_out = sum(c.output_tokens + c.thoughts_tokens for c in self.calls)
        total_cost = sum(c.cost_usd for c in self.calls)
        by_model: dict[str, dict] = {}
        for c in self.calls:
            m = by_model.setdefault(
                c.model,
                {"calls": 0, "input": 0, "output": 0, "cost": 0.0},
            )
            m["calls"] += 1
            m["input"] += c.prompt_tokens
            m["output"] += c.output_tokens + c.thoughts_tokens
            m["cost"] += c.cost_usd
        return {
            "total_calls": len(self.calls),
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "total_cost_usd": total_cost,
            "by_model": by_model,
        }

    def reset(self) -> None:
        self.calls.clear()


usage_tracker = UsageTracker()
