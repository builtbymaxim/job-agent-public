"""Tests for profile parsing + derived scoring summary (single source of truth)."""

from datetime import date

from agents.profile import (
    build_scoring_summary,
    candidate_context,
    extract_section,
    parse_section_kv,
    profile_age_days,
)

PROFILE = """# Someone — Master Profile

## Kontakt
name: Jane Doe
email: jane@example.com
phone: +43 1 000
address: Somewhere 1, Wien

## Eckdaten
level: Graduating BSc student
target_roles: Data Analyst, BI Developer
location: Vienna only
languages: German (native), English (fluent)
salary: 45.000€ - 55.000€
availability: immediately

## Kurzprofil
Data and analytics focus. Built dashboards in Power BI.

## Technische Kenntnisse
- Python, SQL, Power BI

## Differenzierungsmerkmale
- unusual combination of business and technical depth
"""


def test_parse_section_kv():
    facts = parse_section_kv(PROFILE, "Eckdaten")
    assert facts["target_roles"] == "Data Analyst, BI Developer"
    assert facts["salary"] == "45.000€ - 55.000€"
    # Eckdaten parsing must not bleed into the Kontakt block.
    assert "name" not in facts


def test_extract_section():
    assert "Power BI" in extract_section(PROFILE, "Kurzprofil")
    assert extract_section(PROFILE, "Nonexistent") == ""


def test_scoring_summary_derived_from_profile():
    summary = build_scoring_summary(PROFILE)
    # Facts surface...
    assert "Data Analyst, BI Developer" in summary
    assert "Vienna only" in summary
    assert "German (native)" in summary
    # ...and scoring-relevant prose...
    assert "Power BI" in summary
    # ...but never contact PII.
    assert "jane@example.com" not in summary
    assert "Somewhere 1" not in summary


def test_summary_reflects_edits_no_drift():
    edited = PROFILE.replace("Data Analyst, BI Developer", "Risk Analyst")
    assert "Risk Analyst" in build_scoring_summary(edited)
    assert "Data Analyst, BI Developer" not in build_scoring_summary(edited)


def test_fallback_when_unstructured():
    plain = "## Kontakt\nemail: x@y.z\n\n## Kurzprofil\nSome prose here."
    # No Eckdaten + a known section still yields the section, not a crash.
    out = build_scoring_summary(plain)
    assert "Some prose here." in out


def test_candidate_context():
    ctx = candidate_context(PROFILE)
    assert "Graduating BSc student" in ctx
    assert "Data Analyst, BI Developer" in ctx
    # No Eckdaten -> empty, so the reviewer falls back to a generic calibration.
    assert candidate_context("## Kurzprofil\njust prose") == ""


def test_profile_age_days():
    today = date(2026, 6, 18)
    # No prior confirmation -> measured from file mtime.
    assert profile_age_days(date(2026, 6, 1), None, today) == 17
    # A more recent confirmation resets the clock.
    assert profile_age_days(date(2026, 6, 1), date(2026, 6, 15), today) == 3
    # An older confirmation than the edit is ignored (edit wins).
    assert profile_age_days(date(2026, 6, 10), date(2026, 6, 5), today) == 8


def test_load_profile_tolerates_bom_and_crlf(tmp_path):
    from agents.latex_utils import parse_kontakt
    from agents.profile import load_profile
    p = tmp_path / "profile_x.md"
    p.write_bytes("﻿## Kontakt\r\nname: A B\r\nemail: a@b.c\r\nphone: 1\r\naddress: S 1, 1010 Wien\r\n".encode("utf-8"))
    k = parse_kontakt(load_profile(p))
    assert k["name"] == "A B" and k["city"] == "Wien"
