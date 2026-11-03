"""Edit a draft by hand in $EDITOR — no LLM round-trip.

For small fixes ("remove SQL in 'SQL analysis'") re-prompting the model is wasteful and
non-deterministic. This serializes the draft to a marker-delimited temp file, opens the
user's editor, and parses the edited text straight back into the draft dataclass.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path
from shutil import which

from models import ApplicationDraft

_CV_PREFIX = "CV: "
_CL_PREFIX = "CL: "
_MARKER_RE = re.compile(r"^===\s*(.+?)\s*===\s*$")


def _value_to_text(val) -> str:
    if isinstance(val, list):
        return "\n".join(str(x) for x in val)
    return str(val)


def serialize_draft(draft: ApplicationDraft) -> str:
    """Render the draft as an editable, marker-delimited document."""
    lines = [
        "# Edit the text under each marker. Keep the '=== ... ===' lines unchanged.",
        "# Lines starting with '#' before the first marker are ignored. Save and close when done.",
        "",
    ]
    for key, val in draft.cv_sections.items():
        lines += [f"=== {_CV_PREFIX}{key} ===", _value_to_text(val), ""]
    for key, val in draft.cover_letter.items():
        lines += [f"=== {_CL_PREFIX}{key} ===", _value_to_text(val), ""]
    return "\n".join(lines)


def _split_blocks(text: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        m = _MARKER_RE.match(line)
        if m:
            if current is not None:
                blocks[current] = "\n".join(buf).strip("\n")
            current = m.group(1)
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        blocks[current] = "\n".join(buf).strip("\n")
    return blocks


def parse_into_draft(text: str, draft: ApplicationDraft) -> None:
    """Write edited blocks back into the draft (only known keys are updated)."""
    for label, body in _split_blocks(text).items():
        if label.startswith(_CV_PREFIX):
            key = label[len(_CV_PREFIX):]
            if key in draft.cv_sections:
                draft.cv_sections[key] = body
        elif label.startswith(_CL_PREFIX):
            key = label[len(_CL_PREFIX):]
            if key in draft.cover_letter:
                draft.cover_letter[key] = body


def _default_editor() -> str:
    for cand in ("nano", "vim", "vi"):
        if which(cand):
            return cand
    return "vi"


def edit_draft_in_editor(draft: ApplicationDraft) -> bool:
    """Open the draft in $EDITOR; apply edits in place. Returns True if changed."""
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or _default_editor()
    with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(serialize_draft(draft))
        tmp = f.name
    try:
        before = Path(tmp).read_text(encoding="utf-8")
        subprocess.run([*shlex.split(editor), tmp], check=False)
        after = Path(tmp).read_text(encoding="utf-8")
        if after == before:
            return False
        parse_into_draft(after, draft)
        return True
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
