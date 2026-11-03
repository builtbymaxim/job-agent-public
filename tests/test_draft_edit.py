"""Tests for manual draft editing (serialize -> edit -> parse back)."""

from agents.draft_edit import parse_into_draft, serialize_draft
from models import ApplicationDraft, JobLanguage, JobListing


def _draft() -> ApplicationDraft:
    job = JobListing(
        title="T", company="C", location="", platform="direct", url="",
        description="", requirements=[], match_score=1.0, match_reasoning="",
        language=JobLanguage.DE,
    )
    return ApplicationDraft(
        job=job,
        cv_sections={"PROFIL": "old profil", "KENNTNISSE": "a, b"},
        cover_letter={"subject": "Subj", "para1": "hello"},
        company_research="",
        language=JobLanguage.DE,
    )


def test_roundtrip_unchanged():
    d = _draft()
    parse_into_draft(serialize_draft(d), d)
    assert d.cv_sections["PROFIL"] == "old profil"
    assert d.cover_letter["para1"] == "hello"


def test_edit_one_field():
    d = _draft()
    edited = serialize_draft(d).replace("old profil", "new profil text")
    parse_into_draft(edited, d)
    assert d.cv_sections["PROFIL"] == "new profil text"
    assert d.cover_letter["subject"] == "Subj"  # other fields untouched


def test_unknown_markers_ignored():
    d = _draft()
    parse_into_draft("=== CV: NONEXISTENT ===\nx\n\n=== CL: para1 ===\nedited\n", d)
    assert "NONEXISTENT" not in d.cv_sections
    assert d.cover_letter["para1"] == "edited"
