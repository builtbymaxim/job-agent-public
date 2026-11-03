"""Re-generating a job must update its entry, not create a second one.

add_application appended unconditionally, so re-running the same posting on the
same day produced a duplicate row with status "sent" while the original kept the
user's real state — double-counting it in metrics and orphaning any notes.
"""

from datetime import date

from models import ApplicationDraft, ApplicationStatus, JobLanguage, JobListing
from tracker import ApplicationTracker


def _draft(title="Data Analyst"):
    job = JobListing(
        title=title, company="ACME", location="Wien", platform="linkedin",
        url="https://example.com/1", description="d", requirements=[], match_score=1.0,
        match_reasoning="", language=JobLanguage.DE, archetype="data_analyst",
    )
    return ApplicationDraft(job=job, cv_sections={}, cover_letter={},
                            company_research="", language=JobLanguage.DE)


def _tracker(tmp_path):
    return ApplicationTracker(path=tmp_path / "applications.json")


def test_regenerating_the_same_job_does_not_duplicate(tmp_path):
    t = _tracker(tmp_path)
    t.add_application(_draft(), cv_pdf_path="/old/cv.pdf")
    t.add_application(_draft(), cv_pdf_path="/new/cv.pdf")

    assert len(t.get_all()) == 1
    assert t.get_all()[0].cv_pdf_path == "/new/cv.pdf"  # paths refreshed


def test_regeneration_preserves_user_owned_state(tmp_path):
    t = _tracker(tmp_path)
    app = t.add_application(_draft())
    t.update_status(app.id, ApplicationStatus.INTERVIEW,
                    notes="Gedaechtnisprotokoll", contact_person="Mag. Muster",
                    next_step="2. Runde", deadline="2026-08-28")

    t.add_application(_draft(), cv_pdf_path="/regenerated/cv.pdf")

    kept = t.get_application(app.id)
    assert kept.status == ApplicationStatus.INTERVIEW   # not reset to SENT
    assert kept.notes == "Gedaechtnisprotokoll"
    assert kept.contact_person == "Mag. Muster"
    assert kept.next_step == "2. Runde"
    assert kept.deadline == "2026-08-28"
    assert kept.cv_pdf_path == "/regenerated/cv.pdf"    # generated fields still refresh


def test_different_jobs_still_create_separate_entries(tmp_path):
    t = _tracker(tmp_path)
    t.add_application(_draft("Data Analyst"))
    t.add_application(_draft("BI Developer"))
    assert len(t.get_all()) == 2
