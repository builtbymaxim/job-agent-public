#!/usr/bin/env python3
"""Application tracker UI — manage sent applications, interview prep, follow-ups."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

import yaml
from google import genai

from agents import InterviewAgent
from agents.profile import load_profile
from metrics import compute_metrics
from models import ApplicationStatus, TrackedApplication
from tracker import ApplicationTracker
import ui
from rich.markup import escape

# ------------------------------------------------------------------
# Tracker UI
# ------------------------------------------------------------------


class TrackerUI:
    def __init__(self):
        project_root = Path(__file__).resolve().parent
        with open(project_root / "config.yaml", "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        profile_path = project_root / self.config["profile"]
        self.profile_text = load_profile(profile_path)

        self.tracker = ApplicationTracker()
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.interview_agent = InterviewAgent(
            self.profile_text, self.client, market=self.config.get("market") or "European"
        )

    def _status_line(self):
        """`7 applications: 2 sent, 1 interview, …` with each status in its colour."""
        summary = self.tracker.summary()
        if not summary:
            return ui.Text("No applications yet. They appear here once run.py has written one.",
                           style="muted")
        order = ["sent", "response", "interview", "offer", "rejected", "ghosted"]
        line = ui.Text(f"{self.tracker.count} applications: ")
        parts = sorted(summary.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99)
        for i, (status, count) in enumerate(parts):
            if i:
                line.append(", ")
            line.append(f"{count} {status}", style=ui.STATUS_STYLES.get(status, ""))
        return line

    def run(self) -> None:
        """Main menu loop."""
        ui.banner("applications")
        menu = [
            ("1", "Overview", "every application and its next step"),
            ("2", "Update status", "after a reply, an interview or an offer"),
            ("3", "Interview prep", "likely questions and answers from your profile"),
            ("4", "Follow-up email", "a polite nudge after a quiet week or two"),
            ("5", "Deadlines", "what is due in the next 14 days"),
            ("6", "Metrics", "how far your applications get"),
            ("q", "Quit", ""),
        ]
        while True:
            ui.console.print()
            ui.console.print(ui.indent(self._status_line()))
            ui.console.print()
            grid = ui.Table.grid(padding=(0, 2))
            grid.add_column(style="key")
            grid.add_column()
            grid.add_column(style="muted")
            for key, label, what in menu:
                grid.add_row(key, label, what)
            ui.console.print(ui.indent(grid))

            choice = ui.ask().lower()
            if choice == "q":
                break
            actions = {"1": self._show_overview, "2": self._update_status, "3": self._interview_prep,
                       "4": self._follow_up, "5": self._show_deadlines, "6": self._show_metrics}
            if choice in actions:
                actions[choice]()
            else:
                ui.warn("Type a number from the menu, or q to quit.")

    # ------------------------------------------------------------------
    # [1] Overview
    # ------------------------------------------------------------------

    def _show_overview(self) -> None:
        apps = self.tracker.get_all()
        if not apps:
            ui.note("No applications yet.")
            return

        ui.section("Overview")
        avail = ui.console.width - (2 + 10 + 12) - 2 * 6 - 2
        t = ui.table(("#", 2), ("Company", min(24, avail * 30 // 100)),
                     ("Role", min(28, avail * 35 // 100)), ("Status", 10), ("Sent", 12),
                     ("Next step", min(32, avail * 35 // 100)), right=("#",))
        for i, app in enumerate(apps, 1):
            style = ui.STATUS_STYLES.get(app.status.value, "")
            days = app.days_since_sent
            step = escape(app.next_step) if app.next_step else ""
            if app.deadline:
                step += f" [muted]by {escape(app.deadline)}[/]"
            t.add_row(f"[muted]{i}[/]", escape(app.company), escape(app.position),
                      f"[{style}]{app.status.value}[/]",
                      "today" if days == 0 else f"{days} days ago" if days != 1 else "yesterday",
                      step)
        ui.console.print(ui.indent(t))
        ui.pause()

    # ------------------------------------------------------------------
    # [2] Update status
    # ------------------------------------------------------------------

    def _update_status(self) -> None:
        app = self._select_application("Which application changed?")
        if not app:
            return

        statuses = list(ApplicationStatus)
        ui.section(f"{escape(app.company)}", f"now {app.status.value}")
        grid = ui.Table.grid(padding=(0, 2))
        grid.add_column(style="key")
        grid.add_column()
        for i, s in enumerate(statuses, 1):
            grid.add_row(str(i), f"[{ui.STATUS_STYLES.get(s.value, '')}]{s.value}[/]")
        ui.console.print(ui.indent(grid))

        try:
            idx = int(ui.ask("new status")) - 1
        except ValueError:
            ui.warn("Type the number of the new status.")
            return
        if not 0 <= idx < len(statuses):
            ui.warn(f"Pick a number between 1 and {len(statuses)}.")
            return
        new_status = statuses[idx]

        ui.note("Optional details. Press Enter to keep what is there.")
        contact = ui.ask(f"contact person [muted]({escape(app.contact_person or 'none')})[/]") or None
        next_step = ui.ask(f"next step [muted]({escape(app.next_step or 'none')})[/]") or None
        deadline = ui.ask(f"deadline YYYY-MM-DD [muted]({escape(app.deadline or 'none')})[/]") or None
        notes = ui.ask(f"notes [muted]({escape(app.notes or 'none')})[/]") or None

        self.tracker.update_status(
            app.id,
            new_status,
            contact_person=contact,
            next_step=next_step,
            deadline=deadline,
            notes=notes,
        )
        ui.ok(f"{escape(app.company)} is now [{ui.STATUS_STYLES.get(new_status.value, '')}]"
              f"{new_status.value}[/].")

        # Auto-offer interview prep on transition to INTERVIEW
        if new_status == ApplicationStatus.INTERVIEW:
            ui.keys(("y", "write an interview prep guide now"), ("Enter", "later"))
            if ui.ask().lower() == "y":
                self._run_prep(app)

    # ------------------------------------------------------------------
    # [3] Interview prep
    # ------------------------------------------------------------------

    def _interview_prep(self) -> None:
        app = self._select_application("Prepare for which interview?")
        if not app:
            return
        self._run_prep(app)
        ui.pause()

    def _run_prep(self, app: TrackedApplication) -> None:
        """Generate prep guide, persist its path, show the first part."""
        try:
            with ui.status(f"Writing interview prep for {app.company}…"):
                path = self.interview_agent.prepare(app)
            self.tracker.set_interview_prep_path(app.id, path)
            ui.ok(f"Saved to {ui.link(path)}")
            self._preview_markdown(Path(path).read_text(encoding="utf-8"), path)
        except Exception as e:
            ui.fail(f"Could not write the prep guide: {escape(str(e))}")

    # ------------------------------------------------------------------
    # [4] Follow-up email
    # ------------------------------------------------------------------

    def _preview_markdown(self, content: str, path: str | None = None, lines: int = 60) -> None:
        """Render the first `lines` lines of a markdown document in the terminal."""
        from rich.markdown import Markdown
        parts = content.split("\n")
        ui.console.print()
        ui.console.print(ui.indent(Markdown("\n".join(parts[:lines])), 4))
        if len(parts) > lines and path:
            ui.note(f"{len(parts) - lines} more lines in the file.")

    def _follow_up(self) -> None:
        app = self._select_application("Follow up on which application?")
        if not app:
            return

        try:
            with ui.status(f"Writing a follow-up to {app.company}, sent {app.days_since_sent} days ago…"):
                result = self.interview_agent.generate_followup(app, language=app.language)
            if result.endswith(".md"):
                ui.ok(f"Saved to {ui.link(result)}")
                content = Path(result).read_text(encoding="utf-8")
            else:
                content = result
            self._preview_markdown(content)
        except Exception as e:
            ui.fail(f"Could not write the follow-up: {escape(str(e))}")

        ui.pause()

    # ------------------------------------------------------------------
    # [5] Deadlines — due soon
    # ------------------------------------------------------------------

    def _show_deadlines(self) -> None:
        from datetime import date as _date

        apps = self.tracker.due_soon(within_days=14)
        ui.section("Deadlines", "next 14 days")
        if not apps:
            ui.note("Nothing due in the next 14 days.")
            ui.pause()
            return

        today = _date.today()
        t = ui.table(("Due", 12), ("Company", 24), ("Role", 28), "Next step")
        for app in apps:
            left = (_date.fromisoformat(app.deadline.strip()) - today).days
            style = "fail" if left <= 2 else ("warn" if left <= 7 else "")
            when = "today" if left == 0 else "tomorrow" if left == 1 else f"in {left} days"
            t.add_row(f"[{style}]{when}[/]", escape(app.company), escape(app.position),
                      escape(app.next_step or ""))
        ui.console.print(ui.indent(t))
        ui.pause()

    # ------------------------------------------------------------------
    # [6] Metrics
    # ------------------------------------------------------------------

    def _show_metrics(self) -> None:
        apps = self.tracker.get_all()
        if not apps:
            ui.note("No applications yet.")
            ui.pause()
            return

        m = compute_metrics(apps)
        f, r = m["funnel"], m["rates"]
        ui.section("How far your applications get", f"{m['total']} applications")

        grid = ui.Table.grid(padding=(0, 2))
        grid.add_column()
        grid.add_column(justify="right")
        grid.add_column(style="ink")
        grid.add_column(justify="right", style="muted")
        top = max(f["applied"], 1)
        for label, key, rate in [("Applied", "applied", 100.0), ("Got a reply", "responded", r["response"]),
                                 ("Interview", "interview", r["interview"]), ("Offer", "offer", r["offer"])]:
            grid.add_row(label, str(f[key]), ui.bar(f[key] / top, 30), f"{rate:.0f} %")
        ui.console.print(ui.indent(grid))

        def counts(d: dict) -> str:
            items = sorted(d.items(), key=lambda kv: kv[1], reverse=True)
            return ", ".join(f"{v} {escape(str(k))}" for k, v in items)

        ui.console.print()
        rows = [("Language", counts({k.upper(): v for k, v in m["by_language"].items()}))]
        # Archetype / board only exist for applications tracked after they were added.
        if m["by_board"]:
            rows.append(("Board", counts({ui.board(k): v for k, v in m["by_board"].items()})))
        if m["by_archetype"]:
            rows.append(("Role type", counts(m["by_archetype"])))
        details = ui.Table.grid(padding=(0, 2))
        details.add_column(style="muted")
        details.add_column()
        for row in rows:
            details.add_row(*row)
        ui.console.print(ui.indent(details))
        ui.pause()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _select_application(self, question: str) -> TrackedApplication | None:
        """Show a numbered list and let the user pick an application."""
        apps = self.tracker.get_all()
        if not apps:
            ui.note("No applications yet.")
            return None

        ui.section(question)
        t = ui.table(("#", 2), ("Company", 26), ("Role", 30), ("Status", 10), right=("#",))
        for i, app in enumerate(apps, 1):
            style = ui.STATUS_STYLES.get(app.status.value, "")
            t.add_row(f"[muted]{i}[/]", escape(app.company), escape(app.position),
                      f"[{style}]{app.status.value}[/]")
        ui.console.print(ui.indent(t))
        ui.keys(("number", "pick"), ("Enter", "back"))

        choice = ui.ask().lower()
        if choice in ("", "q"):
            return None
        try:
            idx = int(choice) - 1
        except ValueError:
            ui.warn("Type the number of an application.")
            return None
        if 0 <= idx < len(apps):
            return apps[idx]
        ui.warn(f"Pick a number between 1 and {len(apps)}.")
        return None

# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------


def main():
    try:
        app = TrackerUI()
        app.run()
    except KeyboardInterrupt:
        ui.console.print()
        ui.note("Bye.")
        sys.exit(0)
    except Exception as e:
        if "API_KEY" in str(e).upper() or "auth" in str(e).lower() or "401" in str(e):
            ui.fail("Gemini rejected the API key.",
                    "Put a valid key in .env as GEMINI_API_KEY=… (see README, step 3).")
            sys.exit(1)
        raise

if __name__ == "__main__":
    main()
