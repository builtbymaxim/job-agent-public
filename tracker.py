"""Persistent application tracker — CRUD for applications.json."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import date
from pathlib import Path

from models import ApplicationDraft, ApplicationStatus, TrackedApplication

logger = logging.getLogger(__name__)

TRACKER_PATH = Path(__file__).resolve().parent / "applications.json"


class ApplicationTracker:
    def __init__(self, path: str | Path = TRACKER_PATH):
        self.path = Path(path)
        self._apps: list[TrackedApplication] = []
        self.load()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def load(self) -> list[TrackedApplication]:
        """Load applications from JSON file."""
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self._apps = [TrackedApplication.from_dict(d) for d in raw]
        else:
            self._apps = []
        return self._apps

    def save(self) -> None:
        """Atomic write to applications.json via temp file + rename."""
        data = [app.to_dict() for app in self._apps]
        dir_path = self.path.parent
        fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.path)
        except Exception:
            os.unlink(tmp_path)
            raise

    def add_application(
        self,
        draft: ApplicationDraft,
        cv_pdf_path: str = "",
        cl_pdf_path: str = "",
        output_dir: str = "",
    ) -> TrackedApplication:
        """Create a new tracked application from a completed draft."""
        app_id = f"{draft.job.id}_{date.today().isoformat()}"

        # Build paths relative to project
        folder = ""
        if output_dir:
            folder = str(Path(output_dir) / draft.job.folder_name)

        existing = self.get_application(app_id)
        if existing is not None:
            # Re-running the same job on the same day regenerates its documents; it is
            # not a second application. Refresh what the pipeline produced and leave
            # everything the user owns alone — otherwise a regeneration would reset an
            # interview back to "sent" and orphan its notes, and metrics would
            # double-count the application.
            existing.position = draft.job.title
            existing.cv_pdf_path = cv_pdf_path
            existing.cl_pdf_path = cl_pdf_path
            existing.job_posting_path = f"{folder}/job_posting.md" if folder else ""
            existing.company_research_path = f"{folder}/company_research.md" if folder else ""
            existing.language = draft.language.value
            existing.archetype = draft.job.archetype
            existing.board = draft.job.platform
            self.save()
            logger.info(
                f"Updated tracked application (regenerated): {existing.company} — {existing.position}"
            )
            return existing

        app = TrackedApplication(
            id=app_id,
            company=draft.job.company,
            position=draft.job.title,
            date_sent=date.today().isoformat(),
            status=ApplicationStatus.SENT,
            cv_pdf_path=cv_pdf_path,
            cl_pdf_path=cl_pdf_path,
            job_posting_path=f"{folder}/job_posting.md" if folder else "",
            company_research_path=f"{folder}/company_research.md" if folder else "",
            language=draft.language.value,
            archetype=draft.job.archetype,
            board=draft.job.platform,
        )

        self._apps.append(app)
        self.save()
        logger.info(f"Tracked application: {app.company} — {app.position}")
        return app

    def update_status(
        self,
        app_id: str,
        new_status: ApplicationStatus,
        contact_person: str | None = None,
        next_step: str | None = None,
        deadline: str | None = None,
        notes: str | None = None,
    ) -> TrackedApplication | None:
        """Update an application's status and optional fields."""
        app = self.get_application(app_id)
        if not app:
            logger.error(f"Application not found: {app_id}")
            return None

        app.status = new_status
        if contact_person is not None:
            app.contact_person = contact_person
        if next_step is not None:
            app.next_step = next_step
        if deadline is not None:
            app.deadline = deadline
        if notes is not None:
            app.notes = notes

        self.save()
        return app

    def set_interview_prep_path(self, app_id: str, path: str) -> TrackedApplication | None:
        """Record the generated interview-prep file path on an application."""
        app = self.get_application(app_id)
        if not app:
            logger.error(f"Application not found: {app_id}")
            return None
        app.interview_prep_path = path
        self.save()
        return app

    def due_soon(self, within_days: int = 7) -> list[TrackedApplication]:
        """Apps with a parseable deadline within `within_days` from today,
        sorted soonest-first. Empty/unparseable deadlines are skipped."""
        today = date.today()
        out: list[tuple[date, TrackedApplication]] = []
        for app in self._apps:
            if not app.deadline:
                continue
            try:
                d = date.fromisoformat(app.deadline.strip())
            except ValueError:
                continue
            if 0 <= (d - today).days <= within_days:
                out.append((d, app))
        out.sort(key=lambda t: t[0])
        return [app for _, app in out]

    def get_application(self, app_id: str) -> TrackedApplication | None:
        """Find application by ID."""
        for app in self._apps:
            if app.id == app_id:
                return app
        return None

    def get_all(self) -> list[TrackedApplication]:
        """Return all tracked applications."""
        return list(self._apps)

    def get_by_status(self, status: ApplicationStatus) -> list[TrackedApplication]:
        """Filter applications by status."""
        return [app for app in self._apps if app.status == status]

    @property
    def count(self) -> int:
        return len(self._apps)

    def summary(self) -> dict[str, int]:
        """Return count of applications by status."""
        counts: dict[str, int] = {}
        for app in self._apps:
            key = app.status.value
            counts[key] = counts.get(key, 0) + 1
        return counts
