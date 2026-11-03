#!/usr/bin/env python3
"""Record a scripted demo session as an asciicast v2 file (.cast).

Runs scripts/demo/run_demo.py in a pseudo-terminal, types the scripted input with
human-like pauses, and records every byte of output with its timestamp.

    python scripts/demo/record.py pipeline docs/media/pipeline.cast
    python scripts/demo/record.py tracker  docs/media/tracker.cast

Render the result with scripts/demo/render.py. Linux/macOS only (uses pty).
"""

from __future__ import annotations

import json
import os
import pty
import re
import select
import struct
import sys
import tempfile
import time
from fcntl import ioctl
from pathlib import Path
from termios import TIOCSWINSZ

HERE = Path(__file__).resolve().parent
COLS, ROWS = 100, 34

# Each step: (regex to wait for in the output, keys to type). "\r" is Enter.
SCENARIOS: dict[str, dict] = {
    "pipeline": {"args": [], "steps": [(r"pick jobs", "2,3\r")]},
    "url": {"args": ["--url", "https://example.com/jobs/0"], "steps": [(r"write the application", "\r")]},
    "tracker": {"args": ["tracker"], "steps": [
        (r"Quit", "1\r"), (r"go back", "\r"), (r"Quit", "6\r"), (r"go back", "\r"),
        (r"Quit", "q\r"),
    ]},
}


def record(name: str, out: Path) -> None:
    scenario = SCENARIOS[name]
    env = {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor",
           "COLUMNS": str(COLS), "LINES": str(ROWS),
           "DEMO_ROOT": tempfile.mkdtemp(prefix="job-agent-rec-")}
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe(sys.executable, [sys.executable, str(HERE / "run_demo.py"), *scenario["args"]], env)
    ioctl(fd, TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))

    start = time.monotonic()
    events: list[list] = []
    seen = ""
    steps = list(scenario["steps"])
    deadline = start + 240  # never hang forever on a prompt that changed
    while time.monotonic() < deadline:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            text = data.decode("utf-8", errors="replace")
            events.append([round(time.monotonic() - start, 4), "o", text])
            seen += text
        if steps and re.search(steps[0][0], re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", seen)):
            pattern, keys = steps.pop(0)
            seen = ""
            time.sleep(1.2)
            for ch in keys:
                os.write(fd, ch.encode())
                time.sleep(0.12)
        pid_done, _ = os.waitpid(pid, os.WNOHANG)
        if pid_done:
            # drain what is left
            while select.select([fd], [], [], 0.2)[0]:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                events.append([round(time.monotonic() - start, 4), "o", data.decode("utf-8", "replace")])
            break

    out.parent.mkdir(parents=True, exist_ok=True)
    header = {"version": 2, "width": COLS, "height": ROWS, "timestamp": int(time.time()),
              "env": {"TERM": "xterm-256color"}}
    with out.open("w", encoding="utf-8") as f:
        f.write(json.dumps(header) + "\n")
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"recorded {len(events)} events, {events[-1][0] if events else 0:.1f}s -> {out}")


if __name__ == "__main__":
    record(sys.argv[1], Path(sys.argv[2]))
