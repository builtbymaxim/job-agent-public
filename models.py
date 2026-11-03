"""Shared data models for the job application pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Optional

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})


class JobLanguage(str, Enum):
    DE = "de"
    EN = "en"


class CompanyTone(str, Enum):
    STARTUP = "startup"
    CORPORATE = "corporate"
    CONSULTING = "consulting"
    PUBLIC = "public"


class ApplicationStatus(str, Enum):
    SENT = "sent"
    RESPONSE = "response"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"
    GHOSTED = "ghosted"


@dataclass
class JobListing:
    title: str
    company: str
    location: str
    platform: str
    url: str
    description: str
    requirements: list[str]
    match_score: float
    match_reasoning: str
    language: JobLanguage = JobLanguage.DE
    employment_type: str = ""
    posted_date: str = ""
    archetype: str = ""
    legitimacy_tier: str = ""
    legitimacy_signals: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        slug_company = self.company.lower().replace(" ", "-").replace("/", "-")
        slug_title = self.title.lower().replace(" ", "-").replace("/", "-")
        # Remove non-ascii chars for clean folder names
        slug_company = "".join(c for c in slug_company if c.isascii() and (c.isalnum() or c == "-"))
        slug_title = "".join(c for c in slug_title if c.isascii() and (c.isalnum() or c == "-"))
        return f"{slug_company}_{slug_title}"

    @property
    def folder_name(self) -> str:
        return self.id

    @property
    def company_slug(self) -> str:
        """CamelCase ASCII slug for filenames, e.g. 'Philip Morris Int.' -> 'PhilipMorrisInt'."""
        # Transliterate German umlauts first so "Getränke" -> "Getraenke", not "Getrnke".
        name = self.company.translate(_UMLAUTS)
        cleaned = "".join(c if c.isascii() and (c.isalnum() or c == " ") else " " for c in name)
        return "".join(w.capitalize() for w in cleaned.split())

    def to_dict(self) -> dict:
        d = asdict(self)
        d["language"] = self.language.value
        d["id"] = self.id
        return d

    @classmethod
    def from_dict(cls, d: dict) -> JobListing:
        d = d.copy()
        d.pop("id", None)
        d["language"] = JobLanguage(d.get("language", "de"))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class ApplicationDraft:
    job: JobListing
    cv_sections: dict[str, str]      # PROFIL, KENNTNISSE, PROJEKTE, AUSBILDUNG, SPRACHEN
    cover_letter: dict[str, str]     # recipient, date, subject, para1, para2, para3, para4
    company_research: str
    language: JobLanguage
    revision: int = 0
    # Selected, tailored experiences: {company, role, location, dates, bullets(str)}
    experiences: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "job": self.job.to_dict(),
            "cv_sections": self.cv_sections,
            "cover_letter": self.cover_letter,
            "company_research": self.company_research,
            "language": self.language.value,
            "revision": self.revision,
            "experiences": self.experiences,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ApplicationDraft:
        return cls(
            job=JobListing.from_dict(d["job"]),
            cv_sections=d["cv_sections"],
            cover_letter=d["cover_letter"],
            company_research=d["company_research"],
            language=JobLanguage(d["language"]),
            revision=d.get("revision", 0),
            experiences=d.get("experiences", []),
        )


@dataclass
class HRReview:
    cv_score: int
    cl_score: int
    cv_feedback: str
    cl_feedback: str

    @property
    def passed(self) -> bool:
        return self.cv_score >= 7 and self.cl_score >= 7

    def to_dict(self) -> dict:
        return {
            "cv_score": self.cv_score,
            "cl_score": self.cl_score,
            "cv_feedback": self.cv_feedback,
            "cl_feedback": self.cl_feedback,
            "passed": self.passed,
        }

    @classmethod
    def from_dict(cls, d: dict) -> HRReview:
        return cls(
            cv_score=d["cv_score"],
            cl_score=d["cl_score"],
            cv_feedback=d["cv_feedback"],
            cl_feedback=d["cl_feedback"],
        )


@dataclass
class TrackedApplication:
    id: str
    company: str
    position: str
    date_sent: str
    status: ApplicationStatus = ApplicationStatus.SENT
    contact_person: str = ""
    next_step: str = ""
    deadline: str = ""
    notes: str = ""
    cv_pdf_path: str = ""
    cl_pdf_path: str = ""
    job_posting_path: str = ""
    company_research_path: str = ""
    interview_prep_path: str = ""
    language: str = "de"
    archetype: str = ""
    board: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> TrackedApplication:
        d = d.copy()
        d["status"] = ApplicationStatus(d.get("status", "sent"))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @property
    def days_since_sent(self) -> int:
        sent = date.fromisoformat(self.date_sent)
        return (date.today() - sent).days
