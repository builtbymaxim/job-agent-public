"""Tests for the HR reviewer prompt — calibration is candidate-derived, not hardcoded."""

from agents.hr_agent import HRAgent
from models import ApplicationDraft, HRReview, JobLanguage, JobListing


def _draft() -> ApplicationDraft:
    job = JobListing(
        title="Data Analyst",
        company="ACME GmbH",
        location="Wien",
        platform="direct",
        url="https://example.com/1",
        description="Build dashboards.",
        requirements=["SQL", "Power BI"],
        match_score=1.0,
        match_reasoning="",
        language=JobLanguage.EN,
    )
    return ApplicationDraft(
        job=job,
        cv_sections={"PROFIL": "x"},
        cover_letter={"para1": "y"},
        company_research="",
        language=JobLanguage.EN,
    )


def test_prompt_uses_candidate_context():
    hr = HRAgent(client=None, candidate_context="Senior data engineer, 8 years")
    prompt = hr._system_prompt(_draft())
    assert "Senior data engineer, 8 years" in prompt
    # No hardcoded persona from the old prompt.
    assert "WU Vienna" not in prompt
    assert "graduating BSc" not in prompt


def test_prompt_generic_without_context():
    hr = HRAgent(client=None)  # no candidate context
    prompt = hr._system_prompt(_draft())
    assert "seniority implied by the candidate's own experience" in prompt
    assert "WU Vienna" not in prompt


def test_prompt_includes_profile_and_fabrication_check():
    hr = HRAgent(client=None, profile_text="## Berufserfahrung\n### Acme GmbH — Analyst")
    prompt = hr._system_prompt(_draft())
    assert "Acme GmbH" in prompt              # ground truth reached the reviewer
    assert "FABRICATION CHECK" in prompt
    assert "caps the affected score at 3" in prompt


def test_prompt_omits_fabrication_check_without_profile():
    prompt = HRAgent(client=None)._system_prompt(_draft())
    assert "FABRICATION CHECK" not in prompt


def test_profile_is_redacted_before_reaching_the_reviewer():
    """Privacy invariant: contact PII must never enter a prompt, including the review one."""
    profile = (
        "## Kontakt\nname: Jane Doe\naddress: Some Street 1, 1010 Wien\n"
        "email: jane@example.com\nphone: +43 660 0000000\n\n"
        "## Berufserfahrung\n### Acme GmbH — Analyst\n"
    )
    prompt = HRAgent(client=None, profile_text=profile)._system_prompt(_draft())
    assert "Some Street 1" not in prompt
    assert "jane@example.com" not in prompt
    assert "+43 660 0000000" not in prompt
    assert "Acme GmbH" in prompt  # non-contact content survives


def test_prompt_adds_role_specific_criteria():
    draft = _draft()
    draft.job.archetype = "data_science_ml"
    prompt = HRAgent(client=None)._system_prompt(draft)
    assert "ROLE-SPECIFIC CRITERIA" in prompt
    assert "EVALUATION method" in prompt


def test_prompt_has_no_role_criteria_for_general_archetype():
    draft = _draft()
    draft.job.archetype = "general"
    assert "ROLE-SPECIFIC CRITERIA" not in HRAgent(client=None)._system_prompt(draft)


def test_model_tiering_defaults_and_override():
    from agents.hr_agent import MODEL
    from agents.writer import ApplicationWriter

    assert HRAgent(client=None).model == MODEL  # default unchanged
    assert HRAgent(client=None, model="gemini-strong").model == "gemini-strong"
    # writer respects the boundary (redacts) and the model override
    w = ApplicationWriter(config={}, profile_text="## Kurzprofil\nx", client=None, model="gemini-strong")
    assert w.model == "gemini-strong"


def test_pass_threshold_configurable():
    r = HRReview(cv_score=6, cl_score=8, cv_feedback="", cl_feedback="")
    assert HRAgent(client=None)._passed(r) is False           # default 7 -> CV 6 fails
    assert HRAgent(client=None, pass_score=6)._passed(r) is True   # lowered bar passes
    assert HRAgent(client=None, pass_score=9)._passed(r) is False  # raised bar fails


def test_truncated_review_falls_back_and_is_diagnosed(caplog):
    """A cut-off response is an output-budget problem, not a 4/10 verdict.

    Gemini 3.x spends part of max_output_tokens on thinking before emitting text,
    so a budget sized for 2.5 silently truncated the JSON and surfaced as a bad score.
    """
    import logging
    from unittest.mock import MagicMock

    response = MagicMock()
    response.text = '{"cv_score": 8, "cl_score": 8, "cv_feed'  # cut mid-JSON
    response.candidates = [MagicMock(finish_reason="FinishReason.MAX_TOKENS")]

    with caplog.at_level(logging.ERROR):
        review = HRAgent(client=None)._parse_review(response)

    assert review.cv_score == 4  # conservative fallback still triggers a rewrite
    assert "output cap" in caplog.text
    assert "NOT a verdict" in caplog.text


def test_fabrication_check_covers_altered_attributes():
    """The miss that motivated this: a real degree described with invented attributes.

    A generated letter called an on-site evening master's programme "hybrid" and said
    lectures could be attended "online from the hotel", to satisfy a consulting role's
    travel requirement. Nothing was invented outright, so an entity-focused check waved
    it through at 9/10 — the profile says Abendform, Mo-Do, on site.
    """
    hr = HRAgent(client=None, profile_text="## Ausbildung\nMSc, berufsbegleitend (Abendform, Mo-Do)")
    prompt = hr._system_prompt(_draft())
    assert "ALTERED ATTRIBUTES" in prompt
    assert "hybrid" in prompt          # the exact failure is named as an example
    assert "attribute by attribute" in prompt
    assert "pressure" in prompt        # look where the posting creates a gap
