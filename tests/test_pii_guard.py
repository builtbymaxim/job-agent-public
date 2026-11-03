"""Pre-publish guard: fail if real contact PII lands in a tracked file.

Scans git-tracked text files for email addresses and phone numbers. Placeholder values
(example.* domains, the tracked profile.example.md) are allowed; anything else fails so a
real address/email/phone can't silently get committed to a public repo again.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCAN_EXT = {".py", ".md", ".tex", ".yaml", ".yml", ".txt", ".cfg", ".toml", ".json"}
ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")
ALLOWED_EMAILS = {"noreply@anthropic.com"}
# Known placeholder phone numbers (normalized to digits) allowed in examples/fixtures.
ALLOWED_PHONES = {"436600000000", "4312345678", "4312300", "43660000"}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\+\d[\d \-().]{8,}\d")


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _tracked_text_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [ROOT / f for f in out.splitlines() if Path(f).suffix in SCAN_EXT]


def test_no_real_emails_in_tracked_files():
    bad: list[str] = []
    for f in _tracked_text_files():
        text = f.read_text(encoding="utf-8", errors="ignore")
        for email in EMAIL_RE.findall(text):
            low = email.lower()
            domain = low.split("@", 1)[1]
            if low in ALLOWED_EMAILS:
                continue
            if any(domain == d or domain.endswith("." + d) for d in ALLOWED_EMAIL_DOMAINS):
                continue
            bad.append(f"{f.relative_to(ROOT)}: {email}")
    assert not bad, "Non-placeholder email(s) in tracked files:\n" + "\n".join(bad)


def test_no_real_phone_numbers_in_tracked_files():
    bad: list[str] = []
    for f in _tracked_text_files():
        text = f.read_text(encoding="utf-8", errors="ignore")
        for phone in PHONE_RE.findall(text):
            if _digits(phone) in ALLOWED_PHONES:
                continue
            bad.append(f"{f.relative_to(ROOT)}: {phone.strip()}")
    assert not bad, "Non-placeholder phone number(s) in tracked files:\n" + "\n".join(bad)
