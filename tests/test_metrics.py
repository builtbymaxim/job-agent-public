"""Tests for funnel metrics derived from tracked applications."""

from datetime import date

from metrics import compute_metrics
from models import ApplicationStatus, TrackedApplication


def _app(status: ApplicationStatus, language: str = "de", archetype: str = "", board: str = "") -> TrackedApplication:
    return TrackedApplication(
        id=f"x-{status.value}-{language}-{archetype}-{board}",
        company="C",
        position="P",
        date_sent=date.today().isoformat(),
        status=status,
        language=language,
        archetype=archetype,
        board=board,
    )


def test_empty():
    m = compute_metrics([])
    assert m["total"] == 0
    assert m["rates"]["response"] == 0.0
    assert m["funnel"]["offer"] == 0


def test_funnel_is_cumulative_by_stage():
    apps = [
        _app(ApplicationStatus.SENT),
        _app(ApplicationStatus.SENT),
        _app(ApplicationStatus.RESPONSE),
        _app(ApplicationStatus.INTERVIEW),
        _app(ApplicationStatus.OFFER),
        _app(ApplicationStatus.REJECTED),
        _app(ApplicationStatus.GHOSTED),
    ]
    m = compute_metrics(apps)
    assert m["total"] == 7
    # responded = response + interview + offer = 3
    assert m["funnel"]["responded"] == 3
    # interview = interview + offer = 2
    assert m["funnel"]["interview"] == 2
    assert m["funnel"]["offer"] == 1
    assert m["rejected"] == 1 and m["ghosted"] == 1


def test_rates_and_language_split():
    apps = [
        _app(ApplicationStatus.OFFER, "de"),
        _app(ApplicationStatus.SENT, "en"),
        _app(ApplicationStatus.SENT, "en"),
        _app(ApplicationStatus.RESPONSE, "de"),
    ]
    m = compute_metrics(apps)
    assert m["rates"]["response"] == 50.0   # 2 of 4
    assert m["rates"]["offer"] == 25.0      # 1 of 4
    assert m["by_language"] == {"de": 2, "en": 2}


def test_archetype_and_board_breakdowns():
    apps = [
        _app(ApplicationStatus.SENT, archetype="data_analyst", board="indeed"),
        _app(ApplicationStatus.SENT, archetype="data_analyst", board="linkedin"),
        _app(ApplicationStatus.SENT, archetype="bi_developer", board="indeed"),
    ]
    m = compute_metrics(apps)
    assert m["by_archetype"] == {"data_analyst": 2, "bi_developer": 1}
    assert m["by_board"] == {"indeed": 2, "linkedin": 1}


def test_breakdowns_empty_for_legacy_apps():
    # Apps tracked before archetype/board existed -> empty (not crash, not "" keys).
    m = compute_metrics([_app(ApplicationStatus.SENT)])
    assert m["by_archetype"] == {}
    assert m["by_board"] == {}
