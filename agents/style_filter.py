"""Post-generation style filter — detects banned AI phrases and stylistic tells."""

from __future__ import annotations

import re

BANNED_PHRASES = [
    "leverage", "leveraging", "leveraged",
    "utilize", "utilizing", "utilized",
    "cutting-edge", "cutting edge",
    "seasoned",
    "spearhead", "spearheading", "spearheaded",
    "synergy", "synergies",
    "ecosystem",
    "holistic",
    "paradigm",
    "robust",
    "scalable",
    "passionate about",
    "committed to excellence",
    "proven track record",
    "deep understanding",
    "i thrive",
    "i am eager",
    "i am excited",
    "i am confident",
    "dynamic environment",
    "fast-paced", "fast paced",
    "results-driven", "results driven",
    "harnessing the power of",
    "at the forefront of",
    "strategic vision",
    "adept at",
    "well-versed", "well versed",
    "well-positioned", "well positioned",
    "particularly compelling",
    "strongly resonates",
    "aligns well",
    "highly motivating",
    "highly motivated",
    "tangible value",
]

EM_DASH = "\u2014"


def find_violations(text: str) -> list[str]:
    """Scan text for banned phrases and stylistic tells. Returns list of unique violations."""
    if not text:
        return []

    found: set[str] = set()
    lower = text.lower()

    for phrase in BANNED_PHRASES:
        pattern = r"\b" + re.escape(phrase) + r"\b"
        if re.search(pattern, lower):
            found.add(phrase)

    if EM_DASH in text:
        found.add("em-dash (—)")

    return sorted(found)


def find_violations_in_dict(sections: dict[str, str]) -> list[str]:
    """Scan all string values of a dict for violations. Returns combined unique list."""
    combined = " ".join(str(v) for v in sections.values() if v)
    return find_violations(combined)


def strip_em_dashes(text: str) -> str:
    """Replace em-dashes deterministically. Model-side re-prompts are unreliable for this."""
    if not text:
        return text
    # ' — ' → ', ' and bare '—' → ', '
    return text.replace(f" {EM_DASH} ", ", ").replace(EM_DASH, ", ")


# Plain-language replacements for every banned phrase, applied deterministically so AI
# tells never ship — and we never burn an API call re-prompting for them.
REPLACEMENTS: dict[str, str] = {
    "leveraging": "using", "leveraged": "used", "leverage": "use",
    "utilizing": "using", "utilized": "used", "utilize": "use",
    "cutting-edge": "modern", "cutting edge": "modern",
    "seasoned": "experienced",
    "spearheading": "leading", "spearheaded": "led", "spearhead": "lead",
    "synergies": "collaboration", "synergy": "collaboration",
    "ecosystem": "environment",
    "holistic": "complete",
    "paradigm": "model",
    "robust": "reliable",
    "scalable": "efficient",
    "passionate about": "focused on",
    "committed to excellence": "focused on quality",
    "proven track record": "track record",
    "deep understanding": "strong understanding",
    "i thrive": "i work well",
    "i am eager": "i am keen", "i am excited": "i am keen", "i am confident": "i am sure",
    "dynamic environment": "changing environment",
    "fast-paced": "busy", "fast paced": "busy",
    "results-driven": "results-focused", "results driven": "results-focused",
    "harnessing the power of": "using",
    "at the forefront of": "leading in",
    "strategic vision": "strategy",
    "adept at": "skilled in",
    "well-versed": "experienced", "well versed": "experienced",
    "well-positioned": "ready", "well positioned": "ready",
    "particularly compelling": "notable",
    "strongly resonates": "fits well",
    "aligns well": "fits well",
    "highly motivating": "motivating",
    "highly motivated": "motivated",
    "tangible value": "real value",
}


def _replace_banned(text: str) -> str:
    """Swap banned phrases for plain alternatives, preserving leading capitalization.
    Longer phrases first so multi-word matches win over their single-word parts."""
    for phrase in sorted(REPLACEMENTS, key=len, reverse=True):
        repl = REPLACEMENTS[phrase]
        pattern = re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)

        def _sub(m: re.Match, _repl=repl) -> str:
            return _repl[:1].upper() + _repl[1:] if m.group(0)[:1].isupper() else _repl

        text = pattern.sub(_sub, text)
    return text


def sanitize_text(text: str) -> str:
    """Deterministic style cleanup: strip em-dashes + replace banned phrases."""
    if not text:
        return text
    return _replace_banned(strip_em_dashes(text))


def sanitize_sections(sections: dict[str, str]) -> dict[str, str]:
    """Apply deterministic style fixes to all string values in a section dict."""
    return {
        k: sanitize_text(v) if isinstance(v, str) else v
        for k, v in sections.items()
    }
