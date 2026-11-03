"""Tests for job_searcher scoring/parsing logic — no Gemini calls required."""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.job_searcher import JobSearcher, NotAJobPostingError  # noqa: E402
from models import JobListing, JobLanguage  # noqa: E402


CONFIG = {
    "search": {
        "platforms": [],
        "keyword_groups": [],
        "min_match_score": 0.0,
        "max_jobs": 50,
    }
}
PROFILE = "test profile"


def _make_searcher() -> JobSearcher:
    client = MagicMock()
    searcher = JobSearcher(CONFIG, PROFILE, client)
    searcher.seen_urls = {}
    return searcher


def _raw_job(**kwargs) -> dict:
    defaults = {
        "title": "Data Analyst",
        "company": "TestCo",
        "location": "Wien",
        "platform": "linkedin",
        "url": "https://example.com/job/1",
        "description": "Some job description",
        "employment_type": "fulltime",
        "posted_date": date.today().isoformat(),
    }
    defaults.update(kwargs)
    return defaults


def _fake_response(entries: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(text=json.dumps(entries))


# ------------------------------------------------------------------
# Archetype parsing
# ------------------------------------------------------------------

def test_parse_scores_populates_archetype():
    searcher = _make_searcher()
    raw = [_raw_job()]
    entries = [{"index": 0, "match_score": 0.8, "match_reasoning": "ok",
                "requirements": [], "language": "de",
                "archetype": "risk_analyst",
                "legitimacy": {"tier": "high_confidence", "signals": []}}]
    jobs = searcher._parse_scores(_fake_response(entries), raw)
    assert len(jobs) == 1
    assert jobs[0].archetype == "risk_analyst"


def test_unknown_archetype_defaults_to_general():
    searcher = _make_searcher()
    raw = [_raw_job()]
    entries = [{"index": 0, "match_score": 0.7, "match_reasoning": "ok",
                "requirements": [], "language": "de",
                "archetype": "COMPLETELY_UNKNOWN_ROLE",
                "legitimacy": {"tier": "high_confidence", "signals": []}}]
    jobs = searcher._parse_scores(_fake_response(entries), raw)
    assert jobs[0].archetype == "general"


def test_missing_archetype_defaults_to_general():
    searcher = _make_searcher()
    raw = [_raw_job()]
    entries = [{"index": 0, "match_score": 0.7, "match_reasoning": "ok",
                "requirements": [], "language": "de",
                "legitimacy": {"tier": "high_confidence", "signals": []}}]
    jobs = searcher._parse_scores(_fake_response(entries), raw)
    assert jobs[0].archetype == "general"


def test_all_valid_archetypes_are_accepted():
    searcher = _make_searcher()
    valid = ("data_analyst", "bi_developer", "risk_analyst", "data_science_ml", "business_analyst", "general")
    for archetype in valid:
        raw = [_raw_job(url=f"https://example.com/{archetype}")]
        entries = [{"index": 0, "match_score": 0.7, "match_reasoning": "ok",
                    "requirements": [], "language": "de",
                    "archetype": archetype,
                    "legitimacy": {"tier": "high_confidence", "signals": []}}]
        jobs = searcher._parse_scores(_fake_response(entries), raw)
        assert jobs[0].archetype == archetype


# ------------------------------------------------------------------
# Legitimacy parsing
# ------------------------------------------------------------------

def test_parse_scores_populates_legitimacy():
    searcher = _make_searcher()
    raw = [_raw_job()]
    entries = [{"index": 0, "match_score": 0.8, "match_reasoning": "ok",
                "requirements": [], "language": "de",
                "archetype": "data_analyst",
                "legitimacy": {"tier": "caution", "signals": ["vague JD", "no team context"]}}]
    jobs = searcher._parse_scores(_fake_response(entries), raw)
    assert jobs[0].legitimacy_tier == "caution"
    assert "vague JD" in jobs[0].legitimacy_signals
    assert "no team context" in jobs[0].legitimacy_signals


def test_unknown_legitimacy_tier_defaults_to_high_confidence():
    searcher = _make_searcher()
    raw = [_raw_job()]
    entries = [{"index": 0, "match_score": 0.7, "match_reasoning": "ok",
                "requirements": [], "language": "de",
                "archetype": "general",
                "legitimacy": {"tier": "unknown_tier", "signals": []}}]
    jobs = searcher._parse_scores(_fake_response(entries), raw)
    assert jobs[0].legitimacy_tier == "high_confidence"


def test_missing_legitimacy_defaults_gracefully():
    searcher = _make_searcher()
    raw = [_raw_job()]
    entries = [{"index": 0, "match_score": 0.7, "match_reasoning": "ok",
                "requirements": [], "language": "de",
                "archetype": "general"}]
    jobs = searcher._parse_scores(_fake_response(entries), raw)
    assert jobs[0].legitimacy_tier == "high_confidence"
    assert jobs[0].legitimacy_signals == []


def test_legitimacy_signals_truncated_to_three():
    searcher = _make_searcher()
    raw = [_raw_job()]
    entries = [{"index": 0, "match_score": 0.7, "match_reasoning": "ok",
                "requirements": [], "language": "de",
                "archetype": "general",
                "legitimacy": {"tier": "caution",
                               "signals": ["s1", "s2", "s3", "s4", "s5"]}}]
    jobs = searcher._parse_scores(_fake_response(entries), raw)
    assert len(jobs[0].legitimacy_signals) == 3


# ------------------------------------------------------------------
# Posting-age downgrade
# ------------------------------------------------------------------

def test_posting_age_downgrade_high_to_caution():
    searcher = _make_searcher()
    old_date = (date.today() - timedelta(days=35)).isoformat()
    raw = [_raw_job(posted_date=old_date)]
    entries = [{"index": 0, "match_score": 0.8, "match_reasoning": "ok",
                "requirements": [], "language": "de",
                "archetype": "data_analyst",
                "legitimacy": {"tier": "high_confidence", "signals": ["specific team"]}}]
    jobs = searcher._parse_scores(_fake_response(entries), raw)
    assert jobs[0].legitimacy_tier == "caution"
    assert "posting >30 days old" in jobs[0].legitimacy_signals


def test_posting_age_downgrade_caution_to_suspicious():
    searcher = _make_searcher()
    old_date = (date.today() - timedelta(days=40)).isoformat()
    raw = [_raw_job(posted_date=old_date)]
    entries = [{"index": 0, "match_score": 0.8, "match_reasoning": "ok",
                "requirements": [], "language": "de",
                "archetype": "data_analyst",
                "legitimacy": {"tier": "caution", "signals": ["vague JD"]}}]
    jobs = searcher._parse_scores(_fake_response(entries), raw)
    assert jobs[0].legitimacy_tier == "suspicious"
    assert jobs[0].legitimacy_signals[0] == "posting >30 days old"


def test_posting_age_suspicious_stays_suspicious():
    searcher = _make_searcher()
    old_date = (date.today() - timedelta(days=50)).isoformat()
    raw = [_raw_job(posted_date=old_date)]
    entries = [{"index": 0, "match_score": 0.8, "match_reasoning": "ok",
                "requirements": [], "language": "de",
                "archetype": "general",
                "legitimacy": {"tier": "suspicious", "signals": ["ghost pattern"]}}]
    jobs = searcher._parse_scores(_fake_response(entries), raw)
    assert jobs[0].legitimacy_tier == "suspicious"


def test_recent_posting_no_downgrade():
    searcher = _make_searcher()
    recent = date.today().isoformat()
    raw = [_raw_job(posted_date=recent)]
    entries = [{"index": 0, "match_score": 0.8, "match_reasoning": "ok",
                "requirements": [], "language": "de",
                "archetype": "data_analyst",
                "legitimacy": {"tier": "high_confidence", "signals": []}}]
    jobs = searcher._parse_scores(_fake_response(entries), raw)
    assert jobs[0].legitimacy_tier == "high_confidence"


def test_unparseable_date_no_crash():
    searcher = _make_searcher()
    raw = [_raw_job(posted_date="not-a-date")]
    entries = [{"index": 0, "match_score": 0.7, "match_reasoning": "ok",
                "requirements": [], "language": "de",
                "archetype": "general",
                "legitimacy": {"tier": "high_confidence", "signals": []}}]
    jobs = searcher._parse_scores(_fake_response(entries), raw)
    assert jobs[0].legitimacy_tier == "high_confidence"


# ------------------------------------------------------------------
# parse_posting grounding gate
# ------------------------------------------------------------------

def _searcher():
    """JobSearcher with no client — only the pure response parser is exercised."""
    cfg = {"search": {}}
    return JobSearcher.__new__(JobSearcher)


def test_parse_posting_rejects_explicit_non_posting():
    s = _searcher()
    with pytest.raises(NotAJobPostingError):
        s._parse_posting_response(
            '{"is_posting": false, "reason": "cookie consent dialog"}', "raw", "http://x"
        )


def test_parse_posting_rejects_unparseable_response():
    s = _searcher()
    with pytest.raises(NotAJobPostingError):
        s._parse_posting_response("not json at all", "raw", "http://x")


def test_parse_posting_rejects_missing_title_or_company():
    s = _searcher()
    with pytest.raises(NotAJobPostingError):
        s._parse_posting_response('{"is_posting": true, "title": "", "company": "ACME"}', "raw", "")
    with pytest.raises(NotAJobPostingError):
        s._parse_posting_response('{"is_posting": true, "title": "Analyst", "company": ""}', "raw", "")


def test_parse_posting_accepts_real_posting():
    s = _searcher()
    job = s._parse_posting_response(
        '{"is_posting": true, "title": "Data Analyst", "company": "ACME", '
        '"location": "Wien", "language": "de", "requirements": ["SQL"], '
        '"archetype": "data_analyst", "description": "Reporting role"}',
        "raw", "http://x",
    )
    assert job.title == "Data Analyst"
    assert job.company == "ACME"
    assert job.location == "Wien"
    assert job.match_score == 1.0
