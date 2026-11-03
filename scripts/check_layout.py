#!/usr/bin/env python3
"""Layout check: render worst-case DE + EN drafts and measure the PDFs.

No Gemini calls. Fills every section up to the writer's word caps, adds a
placeholder photo + signature, compiles with xelatex, then reads word positions
back with `pdftotext -bbox` and checks the cover letter against DIN 5008 Form B
(letterhead above 45 mm, address zone 62.7-90 mm at 25 mm, subject at ~98.5 mm,
one page, closing + signature + name together) and the CV for a single page.

Needs xelatex and poppler-utils (pdftotext, pdfinfo). Exit code 1 on any failure.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.latex_exporter import LatexExporter  # noqa: E402
from models import ApplicationDraft, JobLanguage, JobListing  # noqa: E402

PT_TO_MM = 25.4 / 72

PROFILE = """## Kontakt
name: Maximiliane Musterfrau-Beispielhaft
headline: M.Sc. Wirtschaftsinformatik · Data Analytics · Business Intelligence · KI
dob: 01.01.1995
address: Lange Beispielstraße 123/4/56, 1010 Wien
city_en: Vienna
email: maximiliane.musterfrau@example.com
phone: +43 660 000 00 00
github: example-handle
github_url: https://github.com/example-handle
linkedin: maximiliane-musterfrau
linkedin_url: https://www.linkedin.com/in/maximiliane-musterfrau/
"""


DE_TEXT = (
    "In meinem Praktikum habe ich ein internes Reporting aufgebaut, das heute die zentrale "
    "Grundlage für die Steuerung von Vertrieb und Produkten ist. Ich habe die Daten aus drei "
    "Systemen in SQL zusammengeführt, die Kennzahlen mit dem Management abgestimmt und daraus "
    "Dashboards in Power BI entwickelt. Dabei habe ich gelernt, dass gute Analysen erst dann "
    "wirken, wenn sie verständlich sind und in Entscheidungen einfließen. Genau diese Verbindung "
    "von sauberer Datenbasis und klarer Kommunikation möchte ich in Ihr Team einbringen."
)
EN_TEXT = (
    "During my internship I built an internal reporting tool that is now the main basis for "
    "steering sales and products. I combined data from three systems in SQL, agreed the key "
    "figures with management and turned them into Power BI dashboards. Along the way I learned "
    "that good analysis only matters once people understand it and act on it. That link between "
    "a clean data foundation and clear communication is what I want to bring to your team."
)


def words(n: int, de: bool) -> str:
    """Realistic application prose, cut to exactly n words."""
    pool = (DE_TEXT if de else EN_TEXT).split()
    return " ".join(pool[i % len(pool)] for i in range(n)).rstrip(".,") + "."


def draft(lang: JobLanguage) -> ApplicationDraft:
    de = lang == JobLanguage.DE
    w = lambda n: words(n, de)  # noqa: E731
    cv = {
        "PROFIL": w(80),
        "KENNTNISSE": "\n".join(f"Kategorie {i}: " + ", ".join(w(3)[:-1].split()) + ", Tool, Werkzeug"
                                for i in range(1, 7)),
        "PROJEKTE": "\n".join(f"- Projekt {i} (privat): {w(47)}" for i in range(1, 4)),
        "AUSBILDUNG": f"- Beispieluniversität — M.Sc. Wirtschaftsinformatik (laufend)\n- {w(25)}\n"
                      f"- Beispieluniversität — B.Sc. Wirtschaftsinformatik\n- Thesis: {w(20)}",
        "SPRACHEN": "- Deutsch (Muttersprache)\n- Englisch (C1)\n- Französisch (B2)\n- Spanisch (A2)",
    }
    exps = [
        {"company": "Sehr Lange Beispielunternehmen Holding GmbH", "role": "Business Intelligence Analyst",
         "location": "Wien", "dates": "seit 03.2025", "bullets": "\n".join(f"- {w(20)}" for _ in range(4))},
        {"company": "Beispiel Asset Management GmbH", "role": "Praktikant Sales & Reporting",
         "location": "Wien", "dates": "2024–2025", "bullets": "\n".join(f"- {w(18)}" for _ in range(3))},
        {"company": "Muster Architektur ZT GmbH", "role": "Projektmanagement-Assistenz",
         "location": "Wien", "dates": "2022–2023", "bullets": "\n".join(f"- {w(15)}" for _ in range(2))},
    ]
    cl = {
        "recipient": "z. H. Frau Mag. Beispiel\nAbteilung Human Resources\nBeispielgasse 12-14\n1030 Wien",
        "date": "02.11.2026",
        "subject": ("Bewerbung als Senior Business Intelligence Analyst (m/w/d) im Bereich Risikocontrolling"
                    if de else
                    "Application for Senior Business Intelligence Analyst (all genders), Risk Controlling"),
        "salutation": "Sehr geehrte Frau Mag. Beispiel," if de else "Dear Ms Beispiel,",
        "para1": w(90), "para2": w(90), "para3": w(90), "para4": w(90),
    }
    job = JobListing(
        title="Senior Business Intelligence Analyst (m/w/d)",
        company="Beispielhafte Österreichische Versicherungs- und Rückversicherungs AG",
        location="Wien", url="https://example.com/x", platform="test",
        description="layout check", requirements=[], match_score=1.0,
        match_reasoning="", language=lang,
    )
    return ApplicationDraft(job=job, cv_sections=cv, cover_letter=cl, company_research="",
                            language=lang, experiences=exps)


def boxes(pdf: Path) -> list[dict]:
    """Word boxes (mm, origin top-left) per page from pdftotext -bbox."""
    html = subprocess.run(["pdftotext", "-bbox", str(pdf), "-"], capture_output=True, text=True).stdout
    out, page = [], 0
    for line in html.splitlines():
        if "<page " in line:
            page += 1
        m = re.search(r'xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)<', line)
        if m:
            x0, y0, x1, y1 = (float(v) * PT_TO_MM for v in m.groups()[:4])
            out.append({"page": page, "x0": x0, "y0": y0, "x1": x1, "y1": y1, "t": m.group(5)})
    return out


def pages(pdf: Path) -> int:
    info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
    return int(re.search(r"Pages:\s+(\d+)", info).group(1))


def find(bx: list[dict], text: str) -> dict:
    hit = [b for b in bx if b["t"] == text]
    if not hit:
        raise AssertionError(f"word {text!r} not found in PDF")
    return hit[0]


def check_cl(pdf: Path, lang: JobLanguage) -> list[str]:
    errs = []
    bx = boxes(pdf)
    n = pages(pdf)
    if n != 1:
        errs.append(f"cover letter has {n} pages (expected 1)")
    name_top = find(bx, "MAXIMILIANE")
    if name_top["y0"] < 10:
        errs.append(f"letterhead starts at {name_top['y0']:.1f} mm (collides with accent bar)")
    letterhead_bottom = max(b["y1"] for b in bx if b["page"] == 1 and b["y0"] < 45 and b["x0"] > 100)
    if letterhead_bottom > 45:
        errs.append(f"letterhead ends at {letterhead_bottom:.1f} mm (DIN: above 45 mm)")
    company = find(bx, "Beispielhafte")
    addr = [b for b in bx if b["page"] == 1 and 62 <= b["y0"] and b["y1"] <= 95 and b["x0"] < 110]
    if abs(company["x0"] - 25) > 1:
        errs.append(f"address starts at x={company['x0']:.1f} mm (DIN: 25 mm)")
    if company["y0"] < 62.7 or max(b["y1"] for b in addr) > 90:
        errs.append(f"address zone spans {company['y0']:.1f}-{max(b['y1'] for b in addr):.1f} mm (DIN: 62.7-90)")
    if max(b["x1"] for b in addr) > 105 + 0.5:
        errs.append("address wider than the 80 mm zone")
    date = find(bx, "02.11.2026")
    if not (180 <= date["x1"] <= 190.5) or not (80 <= date["y1"] <= 90.5):
        errs.append(f"dateline at x1={date['x1']:.1f}, y1={date['y1']:.1f} mm (expected right margin, above 90 mm)")
    subj = find(bx, "Bewerbung" if lang == JobLanguage.DE else "Application")
    if abs(subj["y0"] - 98.46) > 1.5:
        errs.append(f"subject starts at {subj['y0']:.1f} mm (DIN: 98.46 mm)")
    closing = find(bx, "freundlichen" if lang == JobLanguage.DE else "regards")
    sig_name = [b for b in bx if b["t"] == "Maximiliane" and b["y0"] > closing["y0"]]
    if not sig_name:
        errs.append("name under the closing is missing")
    else:
        gap = sig_name[0]["y0"] - closing["y1"]
        if not (12 <= gap <= 22):
            errs.append(f"signature space {gap:.1f} mm (expected 12-22 mm)")
        if sig_name[0]["page"] != closing["page"]:
            errs.append("closing and name split across pages")
    last = max(b["y1"] for b in bx if b["page"] == 1)
    if last > 297 - 15:
        errs.append(f"text reaches {last:.1f} mm (into the bottom bars)")
    print(f"    CL {lang.value}: pages={n} letterhead<= {letterhead_bottom:.1f} address {company['y0']:.1f}mm "
          f"x={company['x0']:.1f} subject {subj['y0']:.1f}mm date x1={date['x1']:.1f} last text {last:.1f}mm")
    return errs


def check_cv(pdf: Path, lang: JobLanguage) -> list[str]:
    n = pages(pdf)
    bx = boxes(pdf)
    last = max(b["y1"] for b in bx)
    print(f"    CV {lang.value}: pages={n} last text {last:.1f}mm")
    return [] if n == 1 else [f"CV has {n} pages (expected 1)"]


def main() -> int:
    if not shutil.which("xelatex") or not shutil.which("pdftotext"):
        print("SKIP: needs xelatex and pdftotext")
        return 0
    import os
    try:
        from PIL import Image, ImageDraw
    except ImportError:  # no Pillow: check without photo/signature
        Image = None
    font = os.getenv("JOB_AGENT_FONT") or "TeX Gyre Heros"
    errs: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "templates").symlink_to(ROOT / "templates")
        (root / "assets").mkdir()
        if Image is not None:
            Image.new("RGB", (600, 800), (180, 190, 200)).save(root / "assets" / "photo.png")
            sig = Image.new("RGBA", (900, 300), (0, 0, 0, 0))
            ImageDraw.Draw(sig).line([(20, 250), (300, 40), (500, 260), (880, 60)],
                                     fill=(20, 20, 80, 255), width=12)
            sig.save(root / "assets" / "signature.png")
        import yaml
        config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        config["latex"] = {"font": font}
        exp = LatexExporter(config, PROFILE, root)
        for lang in (JobLanguage.DE, JobLanguage.EN):
            m = exp.export(draft(lang), str(root / "out" / lang.value))
            errs += check_cl(Path(m["cl"]["pdf_path"]), lang)
            errs += check_cv(Path(m["cv"]["pdf_path"]), lang)
            if os.getenv("KEEP_LAYOUT_PDFS"):
                dest = Path(os.environ["KEEP_LAYOUT_PDFS"]) / lang.value
                dest.mkdir(parents=True, exist_ok=True)
                for k in ("cv", "cl"):
                    shutil.copy(m[k]["pdf_path"], dest)
    for e in errs:
        print("  FAIL:", e)
    print("ALL OK" if not errs else f"{len(errs)} problem(s)")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
