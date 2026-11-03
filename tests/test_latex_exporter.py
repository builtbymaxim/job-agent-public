"""Tests for latex_utils filters and LatexExporter pipeline."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_HAS_XELATEX = shutil.which("xelatex") is not None


def _render_font() -> str | None:
    """A main font this machine actually has: JOB_AGENT_FONT, else the macOS default,
    else the Helvetica-metric TeX Gyre Heros (Linux/Docker). None if neither exists."""
    if os.getenv("JOB_AGENT_FONT"):
        return os.environ["JOB_AGENT_FONT"]
    if not shutil.which("fc-list"):
        return "Helvetica Neue"  # no fontconfig to ask (e.g. macOS without it): keep the default
    for font in ("Helvetica Neue", "TeX Gyre Heros"):
        out = subprocess.run(["fc-list", font], capture_output=True, text=True).stdout
        if out.strip():
            return font
    return None

from agents.latex_exporter import LatexError, LatexExporter  # noqa: E402
from agents.latex_utils import (  # noqa: E402
    address,
    bullets,
    bullets_bold,
    jinja_env,
    labeled_lines,
    latex_escape,
    parse_kontakt,
)
from models import ApplicationDraft, JobLanguage, JobListing  # noqa: E402


# ------------------------------------------------------------------
# latex_escape
# ------------------------------------------------------------------

def test_escape_handles_all_specials():
    raw = "100% off $5 & 3_x #1 {n} ~tilde ^hat"
    out = latex_escape(raw)
    assert "\\%" in out
    assert "\\$" in out
    assert "\\&" in out
    assert "\\_" in out
    assert "\\#" in out
    assert "\\{" in out and "\\}" in out
    assert "\\textasciitilde{}" in out
    assert "\\textasciicircum{}" in out


def test_escape_backslash_first():
    # backslash must be replaced before any "\X" substitutions, else they get
    # double-escaped to "\textbackslash{}X"
    assert latex_escape("a\\b") == "a\\textbackslash{}b"
    assert latex_escape("\\& not double") == "\\textbackslash{}\\& not double"


def test_escape_empty_string():
    assert latex_escape("") == ""
    assert latex_escape(None) == ""  # type: ignore[arg-type]


# ------------------------------------------------------------------
# bullets
# ------------------------------------------------------------------

def test_bullets_parses_header_and_items():
    out = bullets("Title — Subtitle\n- one\n- two")
    assert "Title" in out
    assert "\\begin{itemize}" in out
    assert "\\item one" in out
    assert "\\item two" in out
    assert "\\end{itemize}" in out


def test_bullets_no_items_emits_header_only():
    out = bullets("Just a paragraph, no bullets")
    assert "\\begin{itemize}" not in out
    assert "Just a paragraph" in out


def test_bullets_escapes_special_chars_in_items():
    out = bullets("Title\n- 50% off & free")
    assert "\\%" in out
    assert "\\&" in out


def test_address_drops_placeholder_lines():
    # Model sometimes emits bracketed instructions when it has no address.
    assert address("[Firmenname, falls vorhanden, sonst leer lassen]\n[PLZ und Ort]") == ""


def test_address_guards_leading_bracket():
    # A line break followed by '[' must not be read as \\'s optional length arg.
    out = address("Line1\n[bracket]rest")
    assert "\\\\ {}" in out  # guard inserted
    assert "Missing number" not in out


def test_address_joins_real_lines():
    out = address("ORF Zentrum\n1136 Wien")
    assert "ORF Zentrum" in out and "1136 Wien" in out and "\\\\" in out


def test_bullets_empty_returns_empty():
    assert bullets("") == ""


# ------------------------------------------------------------------
# bullets_bold / labeled_lines — bold "Label:" lead-ins
# ------------------------------------------------------------------

def test_bullets_bold_bolds_leading_label():
    out = bullets_bold("- Beispielprojekt (privat): Backtesting-Engine in Python.")
    assert "\\item {\\bfseries Beispielprojekt (privat):} Backtesting-Engine in Python." in out


def test_bullets_bold_leaves_labelless_items_plain():
    out = bullets_bold("- Ein Satz ohne Doppelpunkt")
    assert "\\item Ein Satz ohne Doppelpunkt" in out
    assert "\\bfseries" not in out


def test_bullets_bold_ignores_url_colon():
    out = bullets_bold("- Repo https://github.com/x/y ist offen")
    assert "\\bfseries" not in out


def test_bullets_bold_ignores_late_colon():
    # a colon past the label cap is mid-sentence punctuation, not a label
    long_lead = "w" * 70
    out = bullets_bold(f"- {long_lead}: rest")
    assert "\\bfseries" not in out


def test_bullets_bold_escapes_label_and_rest():
    out = bullets_bold("- R & D: 100% Abdeckung")
    assert "\\bfseries R \\& D:" in out
    assert "100\\%" in out


def test_labeled_lines_bolds_each_category():
    out = labeled_lines("Code: Python, R\nBI: Power BI")
    assert "{\\bfseries Code:} Python, R" in out
    assert "{\\bfseries BI:} Power BI" in out
    assert "\\\\" in out  # lines joined with a LaTeX break


def test_labeled_lines_strips_bullet_prefix():
    out = labeled_lines("- Code: Python")
    assert out.startswith("{\\bfseries Code:}")


def test_labeled_lines_empty_returns_empty():
    assert labeled_lines("") == ""
    assert bullets_bold("") == ""


# ------------------------------------------------------------------
# parse_kontakt
# ------------------------------------------------------------------

def test_parse_kontakt_extracts_required_keys():
    text = (
        "# Title\n\n"
        "## Kontakt\n"
        "name: Max\n"
        "email: a@b.c\n"
        "phone: +43 1\n"
        "address: Wien\n"
        "dob: 01.01.2000\n\n"
        "## Other\nstuff\n"
    )
    k = parse_kontakt(text)
    assert k["name"] == "Max"
    assert k["email"] == "a@b.c"
    assert k["phone"] == "+43 1"
    assert k["address"] == "Wien"
    assert k["dob"] == "01.01.2000"


def test_parse_kontakt_missing_block_raises():
    with pytest.raises(ValueError, match="missing a '## Kontakt' block"):
        parse_kontakt("# Title\n\n## Other\nstuff\n")


def test_parse_kontakt_missing_required_keys_raises():
    text = "## Kontakt\nname: x\n"
    with pytest.raises(ValueError, match="missing required keys"):
        parse_kontakt(text)


# ------------------------------------------------------------------
# LatexExporter — full render + compile
# ------------------------------------------------------------------

_VALID_PROFILE = (
    "## Kontakt\n"
    "name: Test User\n"
    "surname_display: USER\n"
    "dob: 01.01.2000\n"
    "address: Teststraße 1, 1010 Wien\n"
    "email: test@example.com\n"
    "phone: +43 1 234 5678\n"
    "github: testuser\n"
)


def _make_draft() -> ApplicationDraft:
    job = JobListing(
        title="Data Analyst",
        company="TestCo",
        location="Wien",
        platform="linkedin",
        url="https://example.com/job/1",
        description="Test description",
        requirements=[],
        match_score=0.8,
        match_reasoning="ok",
        language=JobLanguage.DE,
        employment_type="fulltime",
        posted_date="2026-05-21",
    )
    return ApplicationDraft(
        job=job,
        cv_sections={
            "PROFIL": "Kurzprofil-Text mit Umlauten: äöüß.",
            "KENNTNISSE": "Python; SQL; Power BI; 100% Excel.",
            "PROJEKTE": "Bachelorarbeit zur Analyse.",
            "AUSBILDUNG": "- BSc, Beispiel-Uni",
            "SPRACHEN": "- Deutsch",
        },
        cover_letter={
            "recipient": "TestCo\nMusterstraße 1\n1010 Wien",
            "date": "Wien, 21.05.2026",
            "subject": "Bewerbung als Data Analyst",
            "para1": "Sehr geehrte Damen und Herren, ich bewerbe mich.",
            "para2": "Meine Kenntnisse passen.",
            "para3": "Konkret habe ich bei Acme gearbeitet.",
            "para4": "Ich freue mich auf Ihre Rückmeldung.",
        },
        company_research="",
        language=JobLanguage.DE,
        experiences=[
            {"company": "Acme GmbH", "role": "Intern", "location": "Wien", "dates": "2024",
             "bullets": "- Dashboard erstellt\n- Reports automatisiert"},
            {"company": "Beta AG", "role": "PM Assistant", "location": "Wien", "dates": "2022",
             "bullets": "- Projektcontrolling"},
        ],
    )


_ROUTING = {
    "templates": {
        "de": {"cv": "cv/eu_de.tex", "cl": "cl/eu_de.tex"},
        "en": {"cv": "cv/eu_en.tex", "cl": "cl/eu_en.tex"},
    }
}


@pytest.mark.skipif(not _HAS_XELATEX, reason="xelatex not installed (e.g. CI without TeX)")
def test_exporter_full_render(tmp_path):
    font = _render_font()
    if font is None:
        pytest.skip("neither Helvetica Neue nor TeX Gyre Heros is installed")
    config = {**_ROUTING, "latex": {"font": font}}
    exporter = LatexExporter(config=config, profile_text=_VALID_PROFILE, project_root=ROOT)
    draft = _make_draft()
    manifest = exporter.export(draft, str(tmp_path))
    assert "cv" in manifest and "cl" in manifest
    cv_pdf = Path(manifest["cv"]["pdf_path"])
    cl_pdf = Path(manifest["cl"]["pdf_path"])
    assert cv_pdf.exists() and cv_pdf.stat().st_size > 0
    assert cl_pdf.exists() and cl_pdf.stat().st_size > 0


def test_exporter_raises_on_unmapped_language(tmp_path):
    exporter = LatexExporter(config={"templates": {}}, profile_text=_VALID_PROFILE, project_root=ROOT)
    with pytest.raises(LatexError):
        exporter.export(_make_draft(), str(tmp_path))


def test_exporter_raises_on_bad_template(tmp_path, monkeypatch):
    exporter = LatexExporter(config={}, profile_text=_VALID_PROFILE, project_root=ROOT)
    # Force a non-existent template path
    monkeypatch.setattr(exporter, "_pick_templates", lambda _d: ("cv/__nope__.tex", "cl/eu_de.tex"))
    draft = _make_draft()
    with pytest.raises(Exception):
        exporter.export(draft, str(tmp_path))


# ------------------------------------------------------------------
# Cover-letter salutation
# ------------------------------------------------------------------

def _cl_context(exporter, draft):
    return exporter._cl_context(draft)


def test_salutation_falls_back_to_neutral_german():
    """A letter without a greeting reads as broken; the neutral form is never wrong."""
    from agents.latex_exporter import LatexExporter
    from models import ApplicationDraft, JobLanguage, JobListing

    job = JobListing(title="Data Analyst", company="ACME", location="Wien", platform="direct",
                     url="", description="d", requirements=[], match_score=1.0,
                     match_reasoning="", language=JobLanguage.DE)
    draft = ApplicationDraft(job=job, cv_sections={}, cover_letter={}, company_research="",
                             language=JobLanguage.DE)
    ex = LatexExporter.__new__(LatexExporter)
    ex.kontakt, ex.root, ex.font = {}, Path("/tmp"), "Helvetica"
    assert _cl_context(ex, draft)["salutation"] == "Sehr geehrte Damen und Herren,"


def test_salutation_falls_back_to_neutral_english():
    from agents.latex_exporter import LatexExporter
    from models import ApplicationDraft, JobLanguage, JobListing

    job = JobListing(title="Data Analyst", company="ACME", location="Vienna", platform="direct",
                     url="", description="d", requirements=[], match_score=1.0,
                     match_reasoning="", language=JobLanguage.EN)
    draft = ApplicationDraft(job=job, cv_sections={}, cover_letter={}, company_research="",
                             language=JobLanguage.EN)
    ex = LatexExporter.__new__(LatexExporter)
    ex.kontakt, ex.root, ex.font = {}, Path("/tmp"), "Helvetica"
    assert _cl_context(ex, draft)["salutation"] == "Dear Hiring Manager,"


def test_model_supplied_salutation_wins():
    from agents.latex_exporter import LatexExporter
    from models import ApplicationDraft, JobLanguage, JobListing

    job = JobListing(title="Data Analyst", company="ACME", location="Wien", platform="direct",
                     url="", description="d", requirements=[], match_score=1.0,
                     match_reasoning="", language=JobLanguage.DE)
    draft = ApplicationDraft(job=job, cv_sections={},
                             cover_letter={"salutation": "Sehr geehrter Herr Mag. Muster,"},
                             company_research="", language=JobLanguage.DE)
    ex = LatexExporter.__new__(LatexExporter)
    ex.kontakt, ex.root, ex.font = {}, Path("/tmp"), "Helvetica"
    assert _cl_context(ex, draft)["salutation"] == "Sehr geehrter Herr Mag. Muster,"


def test_parse_kontakt_derives_city_and_surname():
    k = parse_kontakt(
        "## Kontakt\nname: Lena Beispiel\nemail: a@b.c\nphone: 1\n"
        "address: Hauptstraße 5, 80331 München\n"
    )
    assert k["city"] == "München"
    assert k["surname_display"] == "BEISPIEL"
    assert "dob" not in k


def test_dateline_uses_profile_city_not_model():
    from agents.latex_exporter import LatexExporter
    exp = LatexExporter.__new__(LatexExporter)
    exp.kontakt = {"city": "München", "city_en": "Munich"}
    assert exp._dateline("02.11.2026", JobLanguage.DE) == "München, 02.11.2026"
    assert exp._dateline("02.11.2026", JobLanguage.EN) == "Munich, 02.11.2026"
    assert exp._dateline("Berlin, 02.11.2026", JobLanguage.DE) == "Berlin, 02.11.2026"


def test_render_tightens_layout_until_one_page(tmp_path):
    from unittest.mock import patch
    from agents.latex_exporter import ExportedDocument, LatexExporter
    exp = LatexExporter.__new__(LatexExporter)
    fits = []

    def fake(template, context, folder, stem):
        fits.append(context["fit"])
        return ExportedDocument("x.pdf", "x.tex", pages=2 if context["fit"] < 2 else 1)

    with patch.object(LatexExporter, "_compile_once", side_effect=fake):
        doc = exp._render_and_compile("cl/eu_de.tex", {}, tmp_path, "x")
    assert fits == [0, 1, 2]
    assert doc.pages == 1


def test_page_count_parsed_from_xelatex_output():
    from agents.latex_exporter import _PAGES_RE
    out = "Output written on /tmp/a/Anschreiben_X.pdf (1 page).\nTranscript written"
    assert _PAGES_RE.search(out).group(1) == "1"
    assert _PAGES_RE.search("Output written on /x y/a.pdf (2 pages).").group(1) == "2"


def test_company_slug_transliterates_umlauts():
    job = JobListing(title="t", company="Müller & Söhne Getränke GmbH", location="", url="",
                     platform="", description="", requirements=[], match_score=0,
                     match_reasoning="", language=JobLanguage.DE)
    assert job.company_slug == "MuellerSoehneGetraenkeGmbh"
