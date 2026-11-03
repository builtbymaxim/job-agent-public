"""Company research must stay grounded: no web content means no research.

A real run invented a Berlin headquarters for an Austrian asset manager and the
cover letter was written around it, so the model-prior fallback was removed.
"""

from unittest.mock import MagicMock, patch

from agents.writer import ApplicationWriter, _research_block
from models import JobLanguage, JobListing


def _job():
    return JobListing(
        title="Data Analyst", company="Tiny Boutique GmbH", location="Wien",
        platform="direct", url="", description="Reporting role",
        requirements=["SQL"], match_score=1.0, match_reasoning="",
        language=JobLanguage.DE,
    )


def _writer():
    w = ApplicationWriter.__new__(ApplicationWriter)
    w.client = MagicMock()
    w.model = "test-model"
    return w


def test_no_web_content_returns_empty_research_without_calling_the_model():
    w = _writer()
    with patch("agents.writer.research_company_web", return_value=""), \
         patch("agents.writer.generate_with_retry") as gen:
        assert w._research_company(_job()) == ""
        gen.assert_not_called()  # no model call means nothing to hallucinate


def test_off_target_snippets_are_discarded():
    w = _writer()
    with patch("agents.writer.research_company_web", return_value="[Source: x] blah"), \
         patch("agents.writer.generate_with_retry",
               return_value=MagicMock(text="NO RELIABLE INFORMATION")):
        assert w._research_company(_job()) == ""


def test_grounded_snippets_are_summarised():
    w = _writer()
    with patch("agents.writer.research_company_web", return_value="[Source: x] real content"), \
         patch("agents.writer.generate_with_retry",
               return_value=MagicMock(text="Boutique asset manager in Vienna.")):
        assert w._research_company(_job()) == "Boutique asset manager in Vienna."


def test_research_block_marks_absence_and_forbids_embellishment():
    block = _research_block("")
    assert "none" in block.lower()
    assert "do not describe the company" in block.lower()


def test_research_block_passes_real_research_through():
    assert _research_block("ACME builds widgets.") == "ACME builds widgets."
