"""Tests for the missing-field detector that drives the fill-in prompt."""

from run import _is_missing


def test_empty_is_missing():
    assert _is_missing("") is True
    assert _is_missing("   ") is True
    assert _is_missing(None) is True  # defensive


def test_bracketed_placeholder_is_missing():
    assert _is_missing("[Firmenname, falls vorhanden, sonst leer lassen]") is True
    assert _is_missing("[PLZ und Ort]") is True


def test_sentinels_are_missing():
    assert _is_missing("Unknown") is True
    assert _is_missing("unknown role") is True
    assert _is_missing("N/A") is True


def test_real_values_present():
    assert _is_missing("Data Engineer") is False
    assert _is_missing("ORF\n1136 Wien") is False
