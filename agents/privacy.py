"""PII redaction — strip contact data from profile text before it reaches an LLM.

Contact PII (name, address, DOB, email, phone) belongs in the LaTeX letterhead only,
where `latex_exporter` injects it locally via `parse_kontakt`. It must never appear in a
generation prompt. `redact_profile` removes the `## Kontakt` block and scrubs any stray
email/phone, returning text that is safe to send to the model.
"""

from __future__ import annotations

import re

_KONTAKT_HEADING = re.compile(r"^#{1,3}\s*Kontakt\s*$", re.IGNORECASE)
_HEADING = re.compile(r"^#{1,3}\s+\S")
_RULE = "---"
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\d)\+\d[\d\s()./-]{7,}\d")  # only international (+..) form


def redact_profile(text: str) -> str:
    """Return profile text with the `## Kontakt` block removed and stray email/phone
    scrubbed. Safe to embed in an LLM prompt.

    The Kontakt block runs from a `## Kontakt` heading until the next markdown heading
    or `---` rule. The email/phone scrub is defensive belt-and-suspenders; the phone
    pattern is restricted to the international `+..` form to avoid mangling the many
    legitimate numbers in the prose (metrics, dates, counts).
    """
    out: list[str] = []
    skipping = False
    for line in text.splitlines():
        if _KONTAKT_HEADING.match(line.strip()):
            skipping = True
            continue
        if skipping:
            if _HEADING.match(line) or line.strip() == _RULE:
                skipping = False
            else:
                continue
        out.append(line)

    body = "\n".join(out)
    body = _EMAIL.sub("[redacted]", body)
    body = _PHONE.sub("[redacted]", body)
    return body
