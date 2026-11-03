"""Jinja2 env + filters + profile contact parser for LaTeX rendering.

Jinja delimiters are remapped (`<<` `>>` for vars, `<%` `%>` for blocks) so
they don't collide with LaTeX's `{}`. Filters: `e` escapes LaTeX specials,
`bullets` turns writer-style "Title\\n- foo\\n- bar" into a LaTeX itemize.
"""

from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader


_BACKSLASH_SENTINEL = "\x00BSL\x00"
_TILDE_SENTINEL = "\x00TIL\x00"
_CARET_SENTINEL = "\x00CAR\x00"

# Pairs applied in order. Backslash/tilde/caret first map to sentinels so
# their replacement text (which contains `{}`) doesn't get re-escaped by the
# `{` and `}` pass.
_LATEX_REPL: list[tuple[str, str]] = [
    ("\\", _BACKSLASH_SENTINEL),
    ("~", _TILDE_SENTINEL),
    ("^", _CARET_SENTINEL),
    ("&", "\\&"),
    ("%", "\\%"),
    ("$", "\\$"),
    ("#", "\\#"),
    ("_", "\\_"),
    ("{", "\\{"),
    ("}", "\\}"),
    (_BACKSLASH_SENTINEL, "\\textbackslash{}"),
    (_TILDE_SENTINEL, "\\textasciitilde{}"),
    (_CARET_SENTINEL, "\\textasciicircum{}"),
]


def latex_escape(text: str) -> str:
    if not text:
        return ""
    for a, b in _LATEX_REPL:
        text = text.replace(a, b)
    return text


def inline_bullets(text: str) -> str:
    """Like bullets() but joins items with ' · ' separator for compact 1-line render."""
    if not text:
        return ""
    items = []
    for line in text.strip().split("\n"):
        s = line.lstrip()
        if s.startswith("- "):
            items.append(s[2:])
    return " \\textbullet{} ".join(latex_escape(it) for it in items)


# A leading "Label: rest" is bolded by `bullets_bold` / `labeled_lines`. The label is
# capped short and must not look like a URL, so a mid-sentence colon (or "https://…")
# never turns into a bold run.
_LABEL_RE = re.compile(r"^([^:]{1,60}):\s+(.+)$", re.DOTALL)


def _split_label(text: str) -> tuple[str, str] | None:
    """Split "Label: rest" into its parts, or None if it isn't label-shaped."""
    match = _LABEL_RE.match(text.strip())
    if not match:
        return None
    label, rest = match.group(1).strip(), match.group(2).strip()
    if not label or not rest or "//" in label:
        return None
    return label, rest


def _parse_bullet_block(text: str) -> tuple[list[str], list[str]]:
    """Split writer output into (header lines, bullet items).

    Lines before the first bullet are header text; later non-bullet lines are
    dropped so the itemize block stays clean.
    """
    header: list[str] = []
    items: list[str] = []
    seen_bullet = False
    for line in text.strip().split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("- "):
            seen_bullet = True
            items.append(stripped[2:])
        elif not seen_bullet:
            header.append(line)
    return header, items


def _itemize(header: list[str], items: list[str], bold_labels: bool) -> str:
    parts = []
    if header:
        parts.append(latex_escape("\n".join(header).strip()))
    if items:
        parts.append("\\begin{itemize}")
        for item in items:
            split = _split_label(item) if bold_labels else None
            if split:
                label, rest = split
                parts.append(
                    f"  \\item {{\\bfseries {latex_escape(label)}:}} {latex_escape(rest)}"
                )
            else:
                parts.append(f"  \\item {latex_escape(item)}")
        parts.append("\\end{itemize}")
    return "\n".join(parts)


def bullets(text: str) -> str:
    """Parse writer output: lines starting with '- ' become \\item entries."""
    if not text:
        return ""
    header, items = _parse_bullet_block(text)
    return _itemize(header, items, bold_labels=False)


def bullets_bold(text: str) -> str:
    """Like `bullets`, but bolds a leading "Label:" in each item.

    The writer emits projects as "- Name (context): sentence" and education as
    "- School: degree", so the label carries the scannable information.
    """
    if not text:
        return ""
    header, items = _parse_bullet_block(text)
    return _itemize(header, items, bold_labels=True)


def labeled_lines(text: str) -> str:
    """Render "Label: values" lines as LaTeX lines with the label bolded.

    Used for the CV skills block, where the writer emits one
    "Category: tool1, tool2" per line and the category should stand out.
    """
    if not text:
        return ""
    rendered: list[str] = []
    for line in text.strip().split("\n"):
        stripped = line.strip()
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()
        if not stripped:
            continue
        split = _split_label(stripped)
        if split:
            label, rest = split
            rendered.append(
                f"{{\\bfseries {latex_escape(label)}:}} {latex_escape(rest)}"
            )
        else:
            rendered.append(latex_escape(stripped))
    return "\\\\\n".join(rendered)


def address(text: str) -> str:
    """Escape an address block and join its lines with LaTeX line breaks (\\\\).

    The model emits newline-separated address lines; LaTeX collapses raw
    newlines, so each line is escaped (via latex_escape) and rejoined with
    `\\\\` so it renders as a stacked block. Empty input → empty string.
    """
    if not text:
        return ""
    lines = [ln.strip() for ln in text.replace("\\n", "\n").splitlines() if ln.strip()]
    # Drop bracketed placeholder/instruction lines the model sometimes emits when it
    # doesn't know the address, e.g. "[Firmenname, falls vorhanden, sonst leer lassen]".
    lines = [ln for ln in lines if not (ln.startswith("[") and ln.endswith("]"))]
    if not lines:
        return ""
    # Join with `\\`, guarding each continuation with `{}` so a line starting with `[`
    # can never be misread as `\\`'s optional length argument ("Missing number" error).
    return " \\\\ {}".join(latex_escape(ln) for ln in lines)


def jinja_env(templates_dir: Path) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        variable_start_string="<<",
        variable_end_string=">>",
        block_start_string="<%",
        block_end_string="%>",
        comment_start_string="<#",
        comment_end_string="#>",
        autoescape=False,
        keep_trailing_newline=True,
    )
    env.filters["e"] = latex_escape
    env.filters["bullets"] = bullets
    env.filters["bullets_bold"] = bullets_bold
    env.filters["labeled_lines"] = labeled_lines
    env.filters["inline_bullets"] = inline_bullets
    env.filters["address"] = address
    return env


_KONTAKT_RE = re.compile(r"^##\s*Kontakt\s*\n((?:.+\n?)+?)(?=\n##\s|\Z)", re.MULTILINE)
_KV_RE = re.compile(r"^\s*([a-z_]+)\s*:\s*(.+?)\s*$")


def parse_kontakt(profile_text: str) -> dict[str, str]:
    """Extract the `## Kontakt` block as a dict of key: value lines."""
    match = _KONTAKT_RE.search(profile_text)
    if not match:
        raise ValueError(
            "profile is missing a '## Kontakt' block with key:value lines (see profile.example.md)"
        )
    out: dict[str, str] = {}
    for line in match.group(1).splitlines():
        m = _KV_RE.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    required = {"name", "email", "phone", "address"}
    missing = required - out.keys()
    if missing:
        raise ValueError(f"## Kontakt block missing required keys: {sorted(missing)}")
    out.setdefault("surname_display", out["name"].split()[-1].upper())
    out.setdefault("city", city_from_address(out["address"]))
    return out


def city_from_address(address: str) -> str:
    """Best-effort city from a one-line address: last comma part, postal code dropped.

    "Musterstraße 1, 1010 Wien" -> "Wien". Set `city:` in ## Kontakt when this guesses wrong.
    """
    last = address.split(",")[-1].strip()
    return re.sub(r"^[A-Z]{0,2}-?\d{4,5}\s+", "", last).strip()
