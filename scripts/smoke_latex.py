#!/usr/bin/env python3
"""Render-only smoke test: build synthetic DE + EN drafts and compile both via
LatexExporter. No Gemini calls. Verifies routing, both template sets, xelatex."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml

from agents.latex_exporter import LatexExporter, LatexError
from models import ApplicationDraft, JobLanguage, JobListing

CV = {
    "PROFIL": "Data-minded analyst bridging analytics, BI and process automation.",
    "KENNTNISSE": "BI & Reporting: Power BI, Excel. GenAI & Analytics: Python, NLP, SQL.",
    "PROJEKTE": "Example Project\n- Built a data pipeline feeding an ML scoring model.\n- Backtested the model against a simple baseline.",
    "AUSBILDUNG": "Example University — B.Sc. Field of Study\n- Focus: Data Science, AI.",
    "SPRACHEN": "- German (native)\n- English (fluent)",
}

EXPERIENCES = [
    {
        "company": "Example Company GmbH", "role": "Intern, Sales & Reporting",
        "location": "Wien", "dates": "2024–2025",
        "bullets": "- Built an internal reporting tool steering sales & product KPIs.\n- Designed Power BI dashboards for management.",
    },
    {
        "company": "Sample Projects GmbH", "role": "Project Management Assistant",
        "location": "Wien", "dates": "2022–2023",
        "bullets": "- Supported project controlling (cost, schedule, scope).\n- Prepared decision briefs for leadership.",
    },
]

CL = {
    "recipient": "Musterstraße 1\n1010 Wien",
    "date": "09.06.2026",
    "subject": "Bewerbung als Data Analyst",
    "para1": "Your push into data-driven risk steering is exactly where I want to build.",
    "para2": "Power BI dashboards and Python KPI models map directly onto my internship work.",
    "para3": "My project work and thesis prove range across analytical and technical delivery.",
    "para4": "I would welcome a conversation about contributing from day one.",
}


def make_draft(lang: JobLanguage) -> ApplicationDraft:
    job = JobListing(
        title="Risk Data Analyst",
        company="Example Bank AG",
        location="Vienna, Austria",
        url="https://example.com/test",
        platform="test",
        description="Synthetic risk data analyst role for render smoke test.",
        requirements=["Power BI", "Python", "SQL"],
        match_score=0.85,
        match_reasoning="synthetic",
        language=lang,
    )
    return ApplicationDraft(
        job=job,
        cv_sections=dict(CV),
        cover_letter=dict(CL),
        company_research="(synthetic)",
        language=lang,
        experiences=[dict(e) for e in EXPERIENCES],
    )


def main() -> int:
    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    profile_path = ROOT / config["profile"]
    if not profile_path.exists():
        profile_path = ROOT / "profile.example.md"  # fresh clone: render with placeholders
    profile = profile_path.read_text(encoding="utf-8")
    exporter = LatexExporter(config, profile, ROOT)

    # Kept (gitignored output dir) so you can open the PDFs and check your letterhead.
    out = ROOT / config.get("output_dir", "applications") / "_smoke"
    for lang in (JobLanguage.DE, JobLanguage.EN):
        print(f"\n=== {lang.value.upper()} ===")
        try:
            m = exporter.export(make_draft(lang), str(out / lang.value))
        except LatexError as e:
            print(f"  FAILED: {e}")
            return 1
        cv, cl = Path(m["cv"]["pdf_path"]), Path(m["cl"]["pdf_path"])
        if not (cv.exists() and cl.exists()):
            return 1
        for label, doc in (("CV", m["cv"]), ("CL", m["cl"])):
            note = "" if doc["pages"] <= 1 else f"  <- {doc['pages']} pages"
            print(f"  {label}: {doc['pdf_path']}{note}")
    print("\nALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
