"""The review loop must reuse research and revise the previous draft.

Regenerating from scratch each round re-ran the web research, cost extra calls,
re-rolled grounding, and threw away sections that had already scored well.
"""

from unittest.mock import MagicMock

from agents.hr_agent import HRAgent
from models import ApplicationDraft, HRReview, JobLanguage, JobListing


def _job():
    return JobListing(
        title="Data Analyst", company="ACME", location="Wien", platform="direct",
        url="", description="d", requirements=["SQL"], match_score=1.0,
        match_reasoning="", language=JobLanguage.DE,
    )


def _draft(marker="v1"):
    return ApplicationDraft(
        job=_job(), cv_sections={"PROFIL": marker}, cover_letter={"para1": marker},
        company_research="ACME is a real company.", language=JobLanguage.DE,
    )


def _hr(scores):
    """HRAgent whose review() yields the given (cv, cl) scores in order."""
    hr = HRAgent(client=None, max_loops=2)
    reviews = [HRReview(cv_score=c, cl_score=l, cv_feedback="fix x", cl_feedback="fix y")
               for c, l in scores]
    hr.review = MagicMock(side_effect=reviews)
    return hr


def test_rewrite_reuses_research_and_passes_previous_draft():
    hr = _hr([(4, 4), (8, 8)])
    writer = MagicMock()
    writer.generate.return_value = _draft("v2")

    hr.review_loop(writer, _job(), _draft("v1"))

    kwargs = writer.generate.call_args.kwargs
    assert kwargs["research"] == "ACME is a real company."  # not re-fetched
    assert kwargs["previous"].cv_sections["PROFIL"] == "v1"  # revise, don't restart
    assert kwargs["feedback"].cv_score == 4


def test_passing_draft_skips_the_writer_entirely():
    hr = _hr([(9, 9)])
    writer = MagicMock()

    draft, review = hr.review_loop(writer, _job(), _draft())

    writer.generate.assert_not_called()
    assert review.cv_score == 9
    assert draft.revision == 1


def test_loop_stops_at_max_loops_and_returns_last_review():
    hr = _hr([(3, 3), (4, 4)])
    writer = MagicMock()
    writer.generate.return_value = _draft("v2")

    _draft_out, review = hr.review_loop(writer, _job(), _draft("v1"))

    assert writer.generate.call_count == 1  # max_loops=2 -> one rewrite
    assert (review.cv_score, review.cl_score) == (4, 4)
    assert hr._passed(review) is False


def test_worse_revision_does_not_replace_a_better_draft():
    """A truncated rewrite once replaced an 8/10 cover letter with an empty one."""
    hr = _hr([(6, 8), (5, 0)])          # CV fails, CL passes -> rewrite -> rewrite is worse
    writer = MagicMock()
    writer.generate.return_value = _draft("v2-broken")

    draft, review = hr.review_loop(writer, _job(), _draft("v1-good"))

    assert draft.cv_sections["PROFIL"] == "v1-good"   # kept the better draft
    assert (review.cv_score, review.cl_score) == (6, 8)


def test_better_revision_is_adopted():
    hr = _hr([(4, 4), (9, 8)])
    writer = MagicMock()
    writer.generate.return_value = _draft("v2-better")

    draft, review = hr.review_loop(writer, _job(), _draft("v1"))

    assert draft.cv_sections["PROFIL"] == "v2-better"
    assert (review.cv_score, review.cl_score) == (9, 8)
