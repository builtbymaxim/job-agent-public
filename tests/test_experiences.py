"""Tests for multi-candidate experience parsing + assembly (Phase 11)."""

from agents.profile import parse_experiences
from agents.writer import ApplicationWriter

PROFILE = """## Berufserfahrung

### Acme GmbH — Data Analyst
**Wien | 2024–2025**

- built dashboards
- automated reports
**Mehrwert:** value line.

---

### Globex AG — Intern
**Graz | 2022**

- supported reporting

## Projekte
- something
"""


def test_parse_experiences():
    exps = parse_experiences(PROFILE)
    assert len(exps) == 2
    assert exps[0]["company"] == "Acme GmbH" and exps[0]["role"] == "Data Analyst"
    assert exps[0]["location"] == "Wien" and exps[0]["dates"] == "2024–2025"
    assert exps[0]["bullets"] == ["built dashboards", "automated reports"]  # Mehrwert excluded
    assert exps[1]["company"] == "Globex AG"


def _writer_with(exps):
    w = ApplicationWriter.__new__(ApplicationWriter)  # bypass __init__ (no client)
    w.experiences = exps
    return w


_EXPS = [
    {"company": "A", "role": "r", "location": "Wien", "dates": "2024", "bullets": ["x"]},
    {"company": "B", "role": "r2", "location": "Wien", "dates": "2022", "bullets": ["y"]},
    {"company": "C", "role": "r3", "location": "Wien", "dates": "2018", "bullets": ["z"]},
]


def test_assemble_selects_by_index_with_profile_meta():
    w = _writer_with(_EXPS)
    out = w._assemble_experiences([{"index": 0, "bullets": "- tailored A"},
                                   {"index": 1, "bullets": "- tailored B"}])
    assert [e["company"] for e in out] == ["A", "B"]
    assert out[0]["bullets"] == "- tailored A"  # tailored bullets kept
    assert out[0]["dates"] == "2024"            # meta from profile, not model


def test_assemble_ignores_bad_index_and_dedupes():
    w = _writer_with(_EXPS)
    out = w._assemble_experiences([{"index": 99, "bullets": "x"},
                                   {"index": 2, "bullets": "- C"},
                                   {"index": 2, "bullets": "- dup"}])
    assert [e["company"] for e in out] == ["C"]


def test_assemble_fallback_when_empty():
    w = _writer_with(_EXPS)
    out = w._assemble_experiences([])
    assert [e["company"] for e in out] == ["A", "B"]  # top 2 from profile
