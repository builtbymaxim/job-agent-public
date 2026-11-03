"""Funnel metrics derived from tracked applications — no DB, pure over the JSON state.

Computes a conversion funnel and outcome rates from `applications.json`. Only fields the
tracker stores (status, language) are used, so the funnel is derived from each app's
current status. Richer cuts (by archetype/tone/board, time-to-response) need extra fields
on `TrackedApplication` first — tracked as future work.
"""

from __future__ import annotations

from collections import Counter

from models import TrackedApplication

# Statuses that imply the application reached at least this funnel stage.
_RESPONDED = ("response", "interview", "offer")
_INTERVIEW = ("interview", "offer")


def compute_metrics(apps: list[TrackedApplication]) -> dict:
    """Funnel + outcome rates + language breakdown. Safe on an empty list."""
    total = len(apps)
    by_status = Counter(a.status.value for a in apps)

    responded = sum(by_status.get(s, 0) for s in _RESPONDED)
    interview = sum(by_status.get(s, 0) for s in _INTERVIEW)
    offer = by_status.get("offer", 0)

    def rate(n: int) -> float:
        return (n / total * 100) if total else 0.0

    return {
        "total": total,
        "by_status": dict(by_status),
        "funnel": {
            "applied": total,
            "responded": responded,
            "interview": interview,
            "offer": offer,
        },
        "rates": {
            "response": rate(responded),
            "interview": rate(interview),
            "offer": rate(offer),
        },
        "by_language": dict(Counter(a.language for a in apps)),
        "by_archetype": dict(Counter(a.archetype for a in apps if a.archetype)),
        "by_board": dict(Counter(a.board for a in apps if a.board)),
        "rejected": by_status.get("rejected", 0),
        "ghosted": by_status.get("ghosted", 0),
    }
