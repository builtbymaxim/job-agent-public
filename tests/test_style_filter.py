"""Tests for deterministic banned-phrase replacement (no re-prompt)."""

from agents.style_filter import (
    BANNED_PHRASES,
    REPLACEMENTS,
    find_violations,
    sanitize_sections,
    sanitize_text,
)


def test_every_banned_phrase_has_a_replacement():
    missing = [p for p in BANNED_PHRASES if p not in REPLACEMENTS]
    assert missing == [], f"no replacement for: {missing}"


def test_sanitize_leaves_no_violations():
    s = ("I am eager to leverage cutting-edge tools. My robust, scalable approach "
         "aligns well and I am confident — truly.")
    out = sanitize_text(s)
    assert find_violations(out) == []
    assert "—" not in out  # em-dash gone


def test_capitalization_preserved():
    assert sanitize_text("Leveraging data").startswith("Using data")
    assert sanitize_text("we are leveraging data").startswith("we are using")


def test_sanitize_sections_applies_to_all_values():
    out = sanitize_sections({"a": "utilize Python", "b": "passionate about data"})
    assert out["a"] == "use Python"
    assert out["b"] == "focused on data"
