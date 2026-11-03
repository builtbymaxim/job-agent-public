"""Tests for the PII redaction layer (privacy invariant)."""

from agents.privacy import redact_profile
from agents.writer import ApplicationWriter

SAMPLE_PROFILE = """# Example — Master Profile

## Kontakt
name: Max Mustermann
dob: 01.01.1990
address: Musterstraße 1, 1010 Wien
email: max.mustermann@example.com
phone: +43 660 000 00 00
linkedin_url: https://www.linkedin.com/in/max-mustermann/

## Kurzprofil
Business informatics student with a focus on data and analytics. Analyzed 46 ESG
reports across 2021-2024 and wrote 95+ pytest tests.

## Berufserfahrung
### Acme GmbH
Built the first sales dashboard in Power BI.
"""

PII_FRAGMENTS = [
    "Musterstraße",
    "max.mustermann@example.com",
    "+43 660 000 00 00",
    "01.01.1990",
]


def test_kontakt_block_and_pii_removed():
    redacted = redact_profile(SAMPLE_PROFILE)
    for frag in PII_FRAGMENTS:
        assert frag not in redacted, f"PII leaked: {frag}"
    assert "## Kontakt" not in redacted


def test_body_preserved():
    redacted = redact_profile(SAMPLE_PROFILE)
    # Narrative the writer needs must survive untouched.
    assert "## Kurzprofil" in redacted
    assert "## Berufserfahrung" in redacted
    assert "Power BI" in redacted
    # Legitimate numbers in prose must not be mangled by the phone scrub.
    assert "46 ESG" in redacted
    assert "95+ pytest" in redacted
    assert "2021-2024" in redacted


def test_writer_redacts_at_boundary():
    writer = ApplicationWriter(config={}, profile_text=SAMPLE_PROFILE, client=None)
    for frag in PII_FRAGMENTS:
        assert frag not in writer.profile_text, f"writer prompt would leak: {frag}"


def test_interview_agent_redacts_at_boundary():
    from agents.interview_agent import InterviewAgent
    agent = InterviewAgent(profile_text=SAMPLE_PROFILE, client=None)
    for frag in PII_FRAGMENTS:
        assert frag not in agent.profile_text, f"interview prompt would leak: {frag}"
