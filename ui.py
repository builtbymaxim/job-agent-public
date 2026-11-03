"""Shared terminal look for run.py and run_tracker.py.

One rich Console with a small palette. The ink blue is a lighter take on the navy of
the rendered PDFs (#1A3A5C), so the terminal and the documents read as one product.
Everything the user sees goes through the helpers here; logs stay out of the way
unless --verbose is set.

The one distinctive element is the routing slip (`Slip`): every application shows
its steps as a checklist that fills in as work completes, with timings and results.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from pathlib import Path

from rich import box
from rich.console import Console
from rich.live import Live
from rich.logging import RichHandler
from rich.markup import escape
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

INK = "#5b8dc9"
PASS = "#5faf7f"
WARN = "#d9a441"
FAIL = "#d9625e"

THEME = Theme({
    "ink": INK,
    "title": f"bold {INK}",
    "key": f"bold {INK}",
    "pass": PASS,
    "warn": WARN,
    "fail": FAIL,
    "muted": "grey62",
    "path": f"underline {INK}",
})

console = Console(theme=THEME, highlight=False)

STATUS_STYLES = {
    "sent": "ink",
    "response": f"bold {INK}",
    "interview": "pass",
    "offer": f"bold {PASS}",
    "accepted": f"bold {PASS}",
    "rejected": "fail",
    "ghosted": "muted",
    "error": "fail",
}

LEGITIMACY = {
    # tier: (style, short label shown next to the job)
    "high_confidence": ("muted", ""),
    "caution": ("warn", "unclear"),
    "suspicious": ("fail", "likely fake"),
}


def score_style(score: float) -> str:
    """Style for a 0-1 match score."""
    return "pass" if score >= 0.80 else ("warn" if score >= 0.60 else "fail")


def hr_style(score: int) -> str:
    """Style for a 0-10 review score."""
    return "pass" if score >= 7 else ("warn" if score >= 5 else "fail")


# ----------------------------------------------------------------------
# Text blocks
# ----------------------------------------------------------------------

def banner(mode: str, facts: list[tuple[str, str]] | None = None) -> None:
    """Product name + what this run is, then a quiet line of key facts."""
    console.print()
    console.print(Text.assemble(("job-agent", "bold"), "  ", (mode, "ink")))
    if facts:
        line = Text()
        for i, (label, value) in enumerate(facts):
            if i:
                line.append("    ")
            line.append(f"{label} ", "muted")
            line.append(value)
        console.print(line)


def section(title: str, note: str = "") -> None:
    """Start of a part of the run: a blank line, a bold title, an optional note."""
    console.print()
    line = Text(title, "title")
    if note:
        line.append(f"  {note}", "muted")
    console.print(line)


def say(message: str) -> None:
    console.print(f"  {message}")


def note(message: str) -> None:
    console.print(f"  [muted]{message}[/]")


def ok(message: str) -> None:
    console.print(f"  [pass]✓[/] {message}")


def warn(message: str, hint: str = "") -> None:
    console.print(f"  [warn]![/] {message}")
    if hint:
        console.print(f"    [muted]{hint}[/]")


def fail(message: str, hint: str = "") -> None:
    console.print(f"  [fail]✗[/] {message}")
    if hint:
        console.print(f"    [muted]{hint}[/]")


def command(cmd: str) -> None:
    """A command the user can copy, on its own line."""
    console.print(f"    [ink]{escape(cmd)}[/]")


def keys(*pairs: tuple[str, str]) -> None:
    """Keyboard hints: `1,3` pick jobs   `all` every job …"""
    line = Text("  ")
    for i, (k, what) in enumerate(pairs):
        if i:
            line.append("    ")
        line.append(k, "key")
        line.append(f" {what}", "muted")
    console.print(line)


def ask(prompt: str = "") -> str:
    """Read a line of input after an ink chevron. Returns the stripped answer."""
    label = f"  [ink]›[/] {prompt} " if prompt else "  [ink]›[/] "
    return console.input(label).strip()


def pause() -> None:
    console.print()
    console.input("  [muted]Enter to go back[/] ")


def link(path: str | Path, label: str | None = None) -> str:
    """Markup for a clickable file path (terminals without link support show the label)."""
    p = Path(path)
    try:
        shown = label or str(p.relative_to(Path.cwd()))
    except ValueError:
        shown = label or str(p)
    return f"[path link={p.resolve().as_uri()}]{escape(shown)}[/]"


def link_url(url: str, max_len: int = 70) -> str:
    """Markup for a clickable web link, shortened for display."""
    shown = url if len(url) <= max_len else url[: max_len - 1] + "…"
    return f"[path link={escape(url)}]{escape(shown)}[/]"


def table(*columns: str | tuple[str, int], right: tuple[str, ...] = ()) -> Table:
    """A quiet table: header underline only, no grid.

    A column is a name, or (name, width) to fix its width; the last unsized column
    takes what is left and is cut with an ellipsis. Columns in `right` align right.
    """
    t = Table(box=box.SIMPLE_HEAD, header_style="muted", show_edge=False, pad_edge=False,
              padding=(0, 2, 0, 0), expand=False)
    specs = [col if isinstance(col, tuple) else (col, None) for col in columns]
    fixed = sum(w for _, w in specs if w) + 2 * len(specs) + 2  # padding + indent
    free = [n for n, w in specs if not w]
    rest = max(10, (console.width - fixed) // max(1, len(free)))
    for name, width in specs:
        t.add_column(name, justify="right" if name in right else "left", no_wrap=True,
                     overflow="ellipsis", width=width or None, min_width=width,
                     max_width=width or rest)
    return t


BOARDS = {"linkedin": "LinkedIn", "indeed": "Indeed", "glassdoor": "Glassdoor",
          "google": "Google", "zip_recruiter": "ZipRecruiter"}


def board(name: str) -> str:
    return BOARDS.get(name.lower(), name.capitalize())


def indent(renderable, spaces: int = 2):
    from rich.padding import Padding
    return Padding(renderable, (0, 0, 0, spaces))


def bar(fraction: float, width: int = 24) -> str:
    """Horizontal bar from eighth-blocks, for funnels and shares."""
    fraction = max(0.0, min(1.0, fraction))
    eighths = round(fraction * width * 8)
    full, rest = divmod(eighths, 8)
    return "█" * full + ("▏▎▍▌▋▊▉"[rest - 1] if rest else "")


@contextmanager
def status(message: str):
    """Spinner for a single quiet wait that isn't part of a routing slip."""
    with console.status(f"[ink]{message}[/]", spinner="dots", spinner_style="ink"):
        yield


# ----------------------------------------------------------------------
# Routing slip
# ----------------------------------------------------------------------

class Step:
    """One line on the slip. Set `detail` while running; call `warn()` to flag it."""

    def __init__(self, label: str):
        self.label = label
        self.detail = ""
        self.state = "run"   # run | ok | warn | fail
        self.started = time.monotonic()

    def warn(self, detail: str) -> None:
        self.state, self.detail = "warn", detail

    def render(self, width: int) -> Text:
        mark = {"ok": ("✓", "pass"), "warn": ("!", "warn"), "fail": ("✗", "fail")}.get(self.state)
        line = Text("  ")
        if mark:
            line.append(mark[0], mark[1])
        else:
            line.append(" ")
        line.append(f" {self.label:<{width}}")
        secs = time.monotonic() - self.started
        line.append(f"{_seconds(secs):>7}", "muted")
        if self.detail:
            line.append("   ")
            line.append(self.detail, "warn" if self.state == "warn" else "")
        return line


class Slip:
    """Checklist for one application: each step prints as it completes.

        slip = ui.Slip("CRM Manager (m/w/d)", "Isarwerk Mobility GmbH", "1/2")
        with slip.step("Company research") as s:
            ...
            s.detail = "2 sources"
    """

    WIDTH = 18

    def __init__(self, title: str, company: str, counter: str = ""):
        console.print()
        head = Text()
        if counter:
            head.append(f"{counter}  ", "muted")
        head.append(title, "bold")
        head.append(f"  {company}", "ink")
        console.print(head)

    @contextmanager
    def step(self, label: str):
        s = Step(label)

        class _Running:
            def __rich__(self_inner):
                line = s.render(Slip.WIDTH)
                spin = Spinner("dots", style="ink")
                frame = spin.render(time.monotonic())
                line = Text.assemble(Text("  "), frame, line[3:])
                return line

        with Live(_Running(), console=console, refresh_per_second=12, transient=True):
            try:
                yield s
            except BaseException:
                s.state = "fail"
                console.print(s.render(Slip.WIDTH))
                raise
        if s.state == "run":
            s.state = "ok"
        console.print(s.render(Slip.WIDTH))


def _seconds(secs: float) -> str:
    return f"{secs:.1f} s" if secs < 60 else f"{int(secs // 60)} min {int(secs % 60)} s"


# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------

_NOISY_LOGGERS = ("httpx", "httpcore", "google_genai", "jobspy", "JobSpy", "urllib3")


def setup_logging(verbose: bool = False) -> None:
    """Warnings and errors only by default, everything with --verbose; both via rich so
    they don't tear through spinners."""
    handler = RichHandler(console=console, show_time=verbose, show_path=False,
                          markup=False, rich_tracebacks=True, omit_repeated_times=False)
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING,
                        format="%(name)s: %(message)s", handlers=[handler], force=True)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def quiet_noisy_loggers() -> None:
    """Kept for callers that configure logging themselves."""
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
