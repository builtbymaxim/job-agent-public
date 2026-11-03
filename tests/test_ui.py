"""Terminal UI helpers: bars, tables, the routing slip and log routing."""

import io
import logging

import pytest
from rich.console import Console

import ui


@pytest.fixture
def out(monkeypatch):
    """Swap the shared console for one that writes plain text into a buffer."""
    buf = io.StringIO()
    monkeypatch.setattr(ui, "console", Console(file=buf, width=100, theme=ui.THEME,
                                               color_system=None, force_terminal=False))
    return buf


def test_bar_scales_with_fraction():
    assert ui.bar(1.0, 10) == "█" * 10
    assert ui.bar(0.0, 10) == ""
    assert ui.bar(0.5, 10) == "█" * 5
    assert len(ui.bar(0.33, 10)) == 4  # 3 full blocks + a partial one
    assert ui.bar(2.0, 10) == "█" * 10  # clamped


def test_slip_marks_done_and_failed_steps(out):
    slip = ui.Slip("Marketing Manager", "Acme GmbH", "1/2")
    with slip.step("Company research") as s:
        s.detail = "2 sources"
    with slip.step("Review") as s:
        s.warn("below the bar")
    with pytest.raises(RuntimeError):
        with slip.step("PDFs"):
            raise RuntimeError("xelatex missing")
    text = out.getvalue()
    assert "1/2  Marketing Manager  Acme GmbH" in text
    assert "✓ Company research" in text and "2 sources" in text
    assert "! Review" in text and "below the bar" in text
    assert "✗ PDFs" in text


def test_table_keeps_fixed_columns_visible(out):
    t = ui.table(("#", 2), ("Match", 6), ("Role", 20), "Note", right=("#", "Match"))
    t.add_row("1", "91", "A very long role title that will not fit", "x" * 200)
    ui.console.print(t)
    first_row = out.getvalue().splitlines()[2]
    assert first_row.split()[:2] == ["1", "91"]
    assert max(len(line) for line in out.getvalue().splitlines()) <= 100


def test_logging_is_quiet_unless_verbose(out):
    ui.setup_logging(verbose=False)
    logging.getLogger("agents.writer").info("noisy detail")
    logging.getLogger("agents.writer").warning("something to see")
    ui.setup_logging(verbose=True)
    logging.getLogger("agents.writer").info("now visible")
    ui.setup_logging(verbose=False)
    text = out.getvalue()
    assert "noisy detail" not in text
    assert "something to see" in text and "now visible" in text


def test_board_names():
    assert ui.board("linkedin") == "LinkedIn"
    assert ui.board("indeed") == "Indeed"
    assert ui.board("xing") == "Xing"
