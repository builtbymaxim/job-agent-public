"""Profile parsing — single source of truth for candidate facts.

A profile is structured front-matter + prose:
- machine-parsed key:value blocks (`## Kontakt` for the letterhead, `## Eckdaten` for
  scoring facts), parsed by `parse_section_kv`;
- prose sections the writer consumes.

`build_scoring_summary` derives the scoring prompt's candidate summary directly from the
profile, so editing the profile is enough — there is no hardcoded duplicate to drift.
Contact PII is never included here (see `agents/privacy.py`).
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from agents.privacy import redact_profile

_KV_RE = re.compile(r"^\s*([a-z_]+)\s*:\s*(.+?)\s*$")

# Facts pulled from the `## Eckdaten` block, in display order, with prompt labels.
_FACT_LABELS: list[tuple[str, str]] = [
    ("level", "Level"),
    ("target_roles", "Target roles"),
    ("location", "Location"),
    ("languages", "Languages"),
    ("salary", "Salary expectation"),
    ("availability", "Availability"),
]

# Prose sections (by `##` heading) that inform scoring. Kept tight to bound prompt size.
_SCORING_SECTIONS: tuple[str, ...] = (
    "Kurzprofil",
    "Technische Kenntnisse",
    "Differenzierungsmerkmale",
)


def load_profile(path: Path) -> str:
    """Read the candidate profile, or exit with setup instructions if it is missing."""
    if not path.exists():
        raise SystemExit(
            f"Profile not found: {path.name}\n"
            f"  cp profile.example.md {path.name}\n"
            "then fill it in (or point config.yaml `profile:` at your own file)."
        )
    return path.read_text(encoding="utf-8-sig")  # tolerate a BOM (Windows editors)


def parse_section_kv(profile_text: str, heading: str) -> dict[str, str]:
    """Parse a `## <heading>` block of `key: value` lines into a dict. Empty if absent."""
    pattern = re.compile(
        rf"^##\s*{re.escape(heading)}\s*\n((?:.+\n?)+?)(?=\n##\s|\Z)", re.MULTILINE
    )
    match = pattern.search(profile_text)
    if not match:
        return {}
    out: dict[str, str] = {}
    for line in match.group(1).splitlines():
        m = _KV_RE.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def extract_section(profile_text: str, heading: str) -> str:
    """Return the prose under a `## <heading>` block (heading excluded). '' if absent."""
    pattern = re.compile(
        rf"^##\s*{re.escape(heading)}\s*\n(.*?)(?=\n##\s|\Z)", re.MULTILINE | re.DOTALL
    )
    match = pattern.search(profile_text)
    return match.group(1).strip() if match else ""


def build_scoring_summary(profile_text: str) -> str:
    """Derive the scoring-prompt candidate summary from the profile.

    Uses the `## Eckdaten` facts header plus a few scoring-relevant prose sections. Falls
    back to the full redacted profile if the structured blocks are absent (older profiles).
    """
    facts = parse_section_kv(profile_text, "Eckdaten")

    fact_lines = [f"- {label}: {facts[key]}" for key, label in _FACT_LABELS if facts.get(key)]
    section_blocks = [
        f"## {h}\n{body}"
        for h in _SCORING_SECTIONS
        if (body := extract_section(profile_text, h))
    ]

    if not fact_lines and not section_blocks:
        return redact_profile(profile_text)

    parts = ["CANDIDATE PROFILE SUMMARY:"]
    parts.extend(fact_lines)
    if section_blocks:
        parts.append("")
        parts.extend(section_blocks)
    return "\n".join(parts)


_EXP_META_RE = re.compile(r"^\*\*(.+?)\*\*")


def parse_experiences(profile_text: str) -> list[dict]:
    """Parse the `## Berufserfahrung` block into structured entries.

    Each `### Company — Role` sub-block, with a `**Location | Dates**` meta line and
    `- ` bullets, becomes {company, role, location, dates, bullets}. Company/role/dates
    come straight from the profile so the writer can tailor bullets without inventing
    employers. Empty list if the section is absent.
    """
    block = extract_section(profile_text, "Berufserfahrung")
    if not block:
        return []

    entries: list[dict] = []
    for part in re.split(r"^###\s+", block, flags=re.MULTILINE):
        part = part.strip()
        if not part:
            continue
        lines = part.splitlines()
        title = lines[0].strip()
        if "—" in title:
            company, role = title.split("—", 1)
        elif " - " in title:
            company, role = title.split(" - ", 1)
        else:
            company, role = title, ""

        location, dates = "", ""
        bullets: list[str] = []
        for ln in lines[1:]:
            s = ln.strip()
            meta = _EXP_META_RE.match(s)
            if meta and not dates and "|" in meta.group(1):
                loc, _, dt = meta.group(1).partition("|")
                location, dates = loc.strip(), dt.strip()
            elif s.startswith("- "):
                bullets.append(s[2:].strip())

        entries.append({
            "company": company.strip(),
            "role": role.strip(),
            "location": location,
            "dates": dates,
            "bullets": bullets,
        })
    return entries


def candidate_context(profile_text: str) -> str:
    """One-line candidate descriptor (level + target roles) for reviewer calibration.

    Derived from `## Eckdaten` so the HR reviewer judges against the right level/field
    instead of a hardcoded persona. Empty string if the facts block is absent.
    """
    facts = parse_section_kv(profile_text, "Eckdaten")
    bits = []
    if facts.get("level"):
        bits.append(facts["level"])
    if facts.get("target_roles"):
        bits.append(f"target roles: {facts['target_roles']}")
    return " | ".join(bits)


def profile_age_days(mtime: date, last_reviewed: date | None, today: date) -> int:
    """Days since the profile was last edited (file mtime) or explicitly confirmed,
    whichever is more recent. Used to decide when to nudge the user to review it."""
    ref = mtime if (last_reviewed is None or last_reviewed < mtime) else last_reviewed
    return (today - ref).days
