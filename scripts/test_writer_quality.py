#!/usr/bin/env python3
"""Focused validation: run writer on one synthetic job to inspect output quality.

Skips job discovery to conserve Gemini free-tier quota (3-4 calls instead of ~20).
Verifies:
- Web research returns grounded content
- Style filter catches/re-prompts banned phrases
- Profile experiences surface in CV/CL by name
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

import yaml
from google import genai

from agents import ApplicationWriter
from agents.latex_exporter import LatexExporter, LatexError
from agents.profile import load_profile
from agents.style_filter import find_violations_in_dict
from models import JobLanguage, JobListing

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def main() -> int:
    with open(ROOT / "config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    profile_text = load_profile(ROOT / config["profile"])
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    job = JobListing(
        title="Retail Risk Data Analyst (f/m/x)",
        company="Raiffeisen Bank International AG",
        location="Vienna, Austria",
        url="https://example.com/test",
        platform="test",
        description=(
            "We are looking for a Retail Risk Data Analyst to join our Risk team. "
            "You will work with credit risk data, build dashboards in Power BI, "
            "model risk KPIs in Python and SQL, and support governance reporting. "
            "Experience with PostgreSQL, statistical modeling, and BI tools is required."
        ),
        requirements=[
            "Bachelor in Business Informatics, Statistics, or related field",
            "Python (pandas, scikit-learn)",
            "SQL / PostgreSQL",
            "Power BI",
            "Statistical analysis (regression, correlation)",
            "Risk reporting experience a plus",
        ],
        employment_type="Full-time",
        match_score=0.85,
        match_reasoning="Strong match: data analytics + Python + Power BI + risk domain",
        language=JobLanguage.EN,
    )

    writer = ApplicationWriter(config, profile_text, client)
    print("\n" + "=" * 60)
    print("Generating draft for test job...")
    print("=" * 60 + "\n")
    draft = writer.generate(job)

    print("\n" + "=" * 60)
    print("RESEARCH (first 500 chars):")
    print("=" * 60)
    print(draft.company_research[:500])

    print("\n" + "=" * 60)
    print("CV SECTIONS:")
    print("=" * 60)
    for k, v in draft.cv_sections.items():
        print(f"\n[{k}]\n{v}")

    print("\n" + "=" * 60)
    print("COVER LETTER:")
    print("=" * 60)
    cl = draft.cover_letter
    print(f"To: {cl.get('recipient', '')}")
    print(f"Date: {cl.get('date', '')}")
    print(f"Subject: {cl.get('subject', '')}")
    for k in ["para1", "para2", "para3", "para4"]:
        print(f"\n[{k}]\n{cl.get(k, '')}")

    print("\n" + "=" * 60)
    print("VALIDATION:")
    print("=" * 60)
    cv_violations = find_violations_in_dict(draft.cv_sections)
    cl_violations = find_violations_in_dict(draft.cover_letter)
    print(f"CV banned-phrase violations: {cv_violations or 'none'}")
    print(f"CL banned-phrase violations: {cl_violations or 'none'}")

    # Profile-enrichment surface check
    cv_text = " ".join(str(v) for v in draft.cv_sections.values()).lower()
    cl_text = " ".join(str(v) for v in draft.cover_letter.values()).lower()
    full = cv_text + " " + cl_text
    # Every profile employer the writer selected should surface by name (profile-driven,
    # so this works for any candidate's profile).
    keywords = {
        f"Experience surfaced: {e['company']}": e["company"].lower() in full
        for e in draft.experiences
    }
    keywords.update({
        "Job-keyword mirroring (Power BI)": "power bi" in cv_text,
        "Job-keyword mirroring (PostgreSQL)": "postgresql" in cv_text or "sql" in cv_text,
    })
    for label, present in keywords.items():
        marker = "OK " if present else "MISS"
        print(f"  [{marker}] {label}")

    cl_word_count = sum(
        len(str(cl.get(k, "")).split()) for k in ["para1", "para2", "para3", "para4"]
    )
    print(f"\nCL body word count: {cl_word_count} (target 280-360)")

    print("\n" + "=" * 60)
    print("LATEX RENDER:")
    print("=" * 60)
    exporter = LatexExporter(config, profile_text, ROOT)
    try:
        manifest = exporter.export(draft, config["output_dir"])
    except LatexError as e:
        print(f"  RENDER FAILED: {e}")
        return 1
    print(f"  CV: {manifest['cv']['pdf_path']}")
    print(f"  CL: {manifest['cl']['pdf_path']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
