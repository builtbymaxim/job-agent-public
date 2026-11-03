#!/usr/bin/env python3
"""Run the real CLI against simulated job boards and a simulated Gemini.

Used to produce the README screenshots and to try the interface without an API
key. Everything runs in a throwaway copy of the project (default: a temp dir), so
your own profile, tracker and output are never touched.

    python scripts/demo/run_demo.py                  # full pipeline (python run.py)
    python scripts/demo/run_demo.py --url https://…  # any run.py arguments
    python scripts/demo/run_demo.py tracker          # CRM with sample applications

DEMO_SPEED scales the simulated waits (default 1.0; 0 = no waits).
"""

from __future__ import annotations

import json
import os
import re
import runpy
import shutil
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SPEED = float(os.getenv("DEMO_SPEED", "1.0"))


def wait(seconds: float) -> None:
    if SPEED > 0:
        time.sleep(seconds * SPEED)


# ----------------------------------------------------------------------
# Simulated job boards
# ----------------------------------------------------------------------

JOBS = [
    # title, company, platform, score, legitimacy, signal
    ("Marketing Manager (m/w/d)", "Bergbahn Getränke AG", "linkedin", 0.91, "high_confidence", ""),
    ("CRM Manager Lifecycle (m/w/d)", "Isarwerk Mobility GmbH", "indeed", 0.86, "high_confidence", ""),
    ("Brand Manager Food (m/w/d)", "Alpenkorn Naturkost GmbH", "linkedin", 0.82, "high_confidence", ""),
    ("Senior Marketing Manager DACH", "Nordlicht Software GmbH", "linkedin", 0.74, "caution",
     "Vague team context, 14 must-haves"),
    ("Performance Marketing Lead", "Kaufhaus Lindner KG", "indeed", 0.68, "high_confidence", ""),
    ("Marketing Allrounder (m/w/d)", "Top Karriere 24", "indeed", 0.63, "suspicious",
     "Generic text, no company details"),
]


def fake_scrape(self, platform: str) -> pd.DataFrame:
    wait(1.2)
    rows = []
    for i, (title, company, plat, *_rest) in enumerate(JOBS):
        if plat != platform:
            continue
        rows.append({
            "title": title, "company": company, "location": "München, Bayern",
            "job_url": f"https://example.com/jobs/{i}", "job_type": "fulltime",
            "date_posted": (date.today() - timedelta(days=i + 1)).isoformat(),
            "description": f"{company} sucht eine:n {title}. Kampagnenplanung, CRM, "
                           "Zusammenarbeit mit Agenturen, Reporting mit GA4.",
        })
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------
# Simulated Gemini
# ----------------------------------------------------------------------

CV = {
    "PROFIL": "Marketing-Managerin mit vier Jahren Erfahrung in Konsumgütern und E-Commerce. "
              "Ich verbinde Kampagnenplanung mit messbarem CRM-Marketing in HubSpot und GA4.",
    "KENNTNISSE": "Marketing: HubSpot, Meta Ads, Google Ads, GA4, Looker Studio\nTools: Canva",
    "PROJEKTE": "- Zero-Waste-Pop-up (privat): zweiwöchiger Store mit einer NGO, 1.800 Besuche.",
    "AUSBILDUNG": "- LMU München — M.Sc. Betriebswirtschaftslehre (Marketing)",
    "SPRACHEN": "- Deutsch (Muttersprache)\n- Englisch (C1)\n- Französisch (B1)",
    "EXPERIENCES": [
        {"index": 0, "bullets": "- Kampagnenkalender der Marke mit 400.000 EUR Budget\n"
                                "- CRM in HubSpot aufgebaut: Verteiler von 12.000 auf 41.000\n"
                                "- Steuerung von zwei Agenturen"},
        {"index": 1, "bullets": "- Meta- und Google-Ads-Kampagnen\n- GA4-Tracking und wöchentlicher KPI-Report"},
    ],
}

CL_PARAS = [
    "Ihre neue Bergbahn-Limonade kommt im Frühjahr in den bayerischen Handel, und genau "
    "diesen Start möchte ich mitgestalten: von der Kampagnenidee bis zur Messung im Regal.",
    "Bei Brezel & Co. verantworte ich den Kampagnenkalender der Marke mit einem Budget von "
    "400.000 Euro. Das E-Mail- und CRM-Programm habe ich in HubSpot aufgebaut und den "
    "Verteiler von 12.000 auf 41.000 Kontakte vergrößert.",
    "Davor habe ich bei Isarshop Meta- und Google-Ads-Kampagnen betreut und das GA4-Tracking "
    "eingerichtet. Ein privates Pop-up mit einer Münchner NGO hat mir gezeigt, wie viel ein "
    "gutes Ladenerlebnis für eine Marke leisten kann.",
    "Ab Januar 2027 stehe ich Ihnen zur Verfügung. Über ein persönliches Gespräch, in dem wir "
    "die Pläne für den Marktstart besprechen, freue ich mich sehr.",
]

_reviews_seen: dict[str, int] = {}


class _Resp:
    def __init__(self, text: str):
        self.text = text
        self.usage_metadata = type("U", (), {
            "prompt_token_count": 1800, "candidates_token_count": 400,
            "thoughts_token_count": 0, "cached_content_token_count": 0,
        })()


class _Models:
    def generate_content(self, model, contents, config=None):
        system = getattr(config, "system_instruction", "") or ""
        text = str(contents)
        if "Score each job" in system:
            wait(2.0)
            out = []
            for i, title in enumerate(re.findall(r"Title: (.+)", text)):
                spec = next(j for j in JOBS if j[0] == title)
                out.append({
                    "index": i, "match_score": spec[3],
                    "match_reasoning": "Passt zu Kampagnen- und CRM-Erfahrung in Konsumgütern.",
                    "requirements": ["Kampagnenplanung", "HubSpot", "GA4"], "language": "de",
                    "archetype": "general",
                    "legitimacy": {"tier": spec[4], "signals": [spec[5]] if spec[5] else []},
                })
            return _Resp(json.dumps(out))
        if "is_posting" in system:
            wait(1.0)
            return _Resp(json.dumps({
                "is_posting": True, "title": JOBS[0][0], "company": JOBS[0][1],
                "location": "München", "employment_type": "Vollzeit", "language": "de",
                "requirements": ["Kampagnenplanung", "HubSpot", "GA4"], "archetype": "general",
                "posted_date": "", "description": "Marketing Manager für eine Getränkemarke.",
            }))
        if "cv_score" in system:
            wait(1.8)
            company = re.search(r"Company: (.+)", system)
            key = company.group(1) if company else "?"
            n = _reviews_seen.get(key, 0)
            _reviews_seen[key] = n + 1
            if key == JOBS[1][1] and n == 0:
                return _Resp('{"cv_score": 8, "cl_score": 6, "cv_feedback": "Gut.", '
                             '"cl_feedback": "Der Einstieg ist zu allgemein."}')
            return _Resp('{"cv_score": 8, "cl_score": 8, "cv_feedback": "Gut.", "cl_feedback": "Gut."}')
        if '"para1"' in system:
            wait(2.2)
            return _Resp(json.dumps({
                "recipient": "Personalabteilung\nAm Brunnen 3\n83022 Rosenheim", "date": date.today().strftime("%d.%m.%Y"),
                "subject": "Bewerbung als Marketing Manager (m/w/d)",
                "salutation": "Sehr geehrte Damen und Herren,",
                **{f"para{i + 1}": p for i, p in enumerate(CL_PARAS)},
            }))
        if "EXPERIENCES" in system:
            wait(2.4)
            return _Resp(json.dumps(CV))
        if "company" in system.lower() and "research" in system.lower():
            wait(1.0)
            return _Resp("Getränkehersteller aus Rosenheim, Familienunternehmen in dritter Generation.")
        return _Resp("{}")


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.models = _Models()


def fake_research(company: str, location: str = "") -> list[dict]:
    wait(1.2)
    return [{"url": "https://example.com/about", "text": f"{company} ist ein Familienunternehmen."}]


def fake_fetch(url: str) -> str:
    wait(0.8)
    return "Marketing Manager (m/w/d) bei Bergbahn Getränke AG in Rosenheim. " * 20


# ----------------------------------------------------------------------
# Sandbox
# ----------------------------------------------------------------------

def make_sandbox(root: Path) -> None:
    for name in ("run.py", "run_tracker.py", "ui.py", "models.py", "tracker.py", "metrics.py"):
        shutil.copy(REPO / name, root / name)
    shutil.copytree(REPO / "agents", root / "agents", ignore=shutil.ignore_patterns("__pycache__"))
    (root / "templates").symlink_to(REPO / "templates")
    shutil.copy(HERE / "demo_profile.md", root / "demo_profile.md")
    config = yaml.safe_load((REPO / "config.yaml").read_text(encoding="utf-8"))
    config["profile"] = "demo_profile.md"
    config["market"] = "German"
    config["gemini"] = {"rpm_delay": 0}
    config["latex"] = {"font": "TeX Gyre Heros"}
    config["search"]["location"] = "München, Germany"
    config["search"]["keyword_groups"] = ["Marketing Manager, Brand Manager, CRM Manager"]
    (root / "config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    (root / "profile_review.json").write_text(json.dumps({"last_reviewed": date.today().isoformat()}))


def seed_tracker(root: Path) -> None:
    """Sample applications in every stage, for the CRM screens."""
    today = date.today()
    rows = [
        ("Bergbahn Getränke AG", "Marketing Manager (m/w/d)", 2, "sent", "", ""),
        ("Isarwerk Mobility GmbH", "CRM Manager Lifecycle", 6, "interview", "Frau Huber",
         "Zweitgespräch am Donnerstag"),
        ("Alpenkorn Naturkost GmbH", "Brand Manager Food", 9, "response", "", "Case Study bis Freitag"),
        ("Kaufhaus Lindner KG", "Performance Marketing Lead", 15, "sent", "", ""),
        ("Nordlicht Software GmbH", "Senior Marketing Manager DACH", 21, "rejected", "", ""),
        ("Stadtwerke Freising", "Online Marketing Manager", 30, "ghosted", "", ""),
        ("Brauhaus Sendling GmbH", "Marketing Lead", 34, "offer", "Herr Maier", "Angebot prüfen"),
    ]
    apps = []
    for i, (company, position, days, status, contact, step) in enumerate(rows):
        sent = today - timedelta(days=days)
        apps.append({
            "id": f"demo-{i}", "company": company, "position": position,
            "date_sent": sent.isoformat(), "status": status, "contact_person": contact,
            "next_step": step,
            "deadline": (today + timedelta(days=2 + i)).isoformat() if step else "",
            "language": "de", "board": "linkedin",
        })
    (root / "applications.json").write_text(json.dumps(apps, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    args = sys.argv[1:]
    root = Path(os.getenv("DEMO_ROOT") or tempfile.mkdtemp(prefix="job-agent-demo-"))
    root.mkdir(parents=True, exist_ok=True)
    if not (root / "run.py").exists():
        make_sandbox(root)
    os.environ.setdefault("GEMINI_API_KEY", "demo")
    sys.path.insert(0, str(root))
    os.chdir(root)

    import google.genai
    google.genai.Client = FakeClient
    import agents.job_searcher
    import agents.web_research
    import agents.writer
    agents.job_searcher.JobSearcher._scrape_platform = fake_scrape
    agents.writer.research_company_web = fake_research
    agents.web_research.fetch_url_text = fake_fetch

    if args[:1] == ["tracker"]:
        if not (root / "applications.json").exists():
            seed_tracker(root)
        sys.argv = [str(root / "run_tracker.py"), *args[1:]]
        runpy.run_path(str(root / "run_tracker.py"), run_name="__main__")
    else:
        sys.argv = [str(root / "run.py"), *args]
        runpy.run_path(str(root / "run.py"), run_name="__main__")


if __name__ == "__main__":
    main()
