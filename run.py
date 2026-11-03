#!/usr/bin/env python3
"""Main pipeline orchestrator — discovers jobs, generates applications, renders PDFs via LaTeX."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

import yaml
from google import genai

from agents import JobSearcher, ApplicationWriter, HRAgent
from agents.job_searcher import NotAJobPostingError
from agents.latex_exporter import LatexExporter, LatexError
from agents.profile import candidate_context, load_profile, profile_age_days
from agents.timing import StageTimer, human_duration
from agents.utils import QuotaExhaustedError, RateLimitedError, set_rpm_delay
from agents.web_research import fetch_url_text
import ui
from agents.usage_tracker import usage_tracker
from rich.markup import escape
from models import ApplicationDraft, HRReview, JobLanguage, JobListing
from tracker import ApplicationTracker

logger = logging.getLogger("pipeline")
ui.setup_logging(verbose="--verbose" in sys.argv)


_MISSING_SENTINELS = {"unknown", "unknown role", "n/a", "none", "-"}


def _is_missing(value: str) -> bool:
    """A field counts as missing if it's empty, a bracketed placeholder the model
    sometimes emits, or a known 'unknown' sentinel."""
    if not value or not value.strip():
        return True
    s = value.strip()
    if "[" in s and "]" in s:
        return True
    return s.lower() in _MISSING_SENTINELS


def split_urls(text: str) -> list[str]:
    """Split pasted URLs on semicolons, whitespace and newlines, preserving order.

    Terminal pastes arrive in every shape — one per line, semicolon-separated, or
    space-separated on a single line — so accept all three and deduplicate rather
    than making the user reformat.
    """
    seen: set[str] = set()
    urls: list[str] = []
    for part in re.split(r"[;\s]+", text.strip()):
        candidate = part.strip().strip(",")
        if not candidate.lower().startswith(("http://", "https://")):
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        urls.append(candidate)
    return urls


def prompt_for_urls() -> list[str]:
    """Read job URLs from the terminal until an empty line ends the list."""
    ui.note("Paste job links, one per line or separated by ';'. An empty line starts the run.")
    collected: list[str] = []
    while True:
        try:
            line = ui.ask("link")
        except EOFError:
            break
        if not line:
            break
        found = split_urls(line)
        if not found:
            ui.warn("No http(s) link in that line, so it was skipped.")
            continue
        collected.extend(u for u in found if u not in collected)
        ui.note(f"{len(collected)} link(s) queued")
    return collected

# ------------------------------------------------------------------
# Pipeline
# ------------------------------------------------------------------


class Pipeline:
    def __init__(self, config_path: str = "config.yaml", auto: bool = False):
        self.auto = auto
        # Load config
        project_root = Path(__file__).resolve().parent
        with open(project_root / config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        # Load profile
        profile_path = project_root / self.config["profile"]
        self.profile_path = profile_path
        self.profile_text = load_profile(profile_path)
        self.review_days = int(self.config.get("profile_review_days", 30))
        self.review_state_path = project_root / "profile_review.json"

        # Date-stamped output directory. Anchor to the repo root so a relative
        # output_dir is CWD-independent; an absolute output_dir is respected as-is.
        self.output_dir = str(
            project_root / self.config["output_dir"] / date.today().isoformat()
        )
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)

        # Initialize Gemini client
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

        # Call pacing (lower with billing enabled — see config gemini.rpm_delay)
        set_rpm_delay((self.config.get("gemini") or {}).get("rpm_delay", 7))

        # Per-task model tiering — bulk (scoring/parsing) stays cheap while
        # writing and review use stronger, and deliberately different, models.
        DEFAULT_MODEL = "gemini-3.5-flash-lite"
        models = self.config.get("models") or {}
        model_bulk = models.get("bulk", DEFAULT_MODEL)
        model_writing = models.get("writing", DEFAULT_MODEL)
        model_review = models.get("review", DEFAULT_MODEL)
        self.model_writing = model_writing

        # HR review calibration (configurable; defaults match prior behavior)
        hr_cfg = self.config.get("hr_review") or {}

        # Initialize agents
        self.searcher = JobSearcher(self.config, self.profile_text, self.client, model=model_bulk)
        self.writer = ApplicationWriter(self.config, self.profile_text, self.client, model=model_writing)
        self.hr = HRAgent(
            self.client,
            candidate_context=candidate_context(self.profile_text),
            model=model_review,
            max_loops=int(hr_cfg.get("max_loops", 2)),
            pass_score=int(hr_cfg.get("pass_score", 7)),
            profile_text=self.profile_text,
            market=self.config.get("market") or "European",
        )
        self.exporter = LatexExporter(self.config, self.profile_text, project_root)
        self.tracker = ApplicationTracker()

        # Run timing + trend log
        self.timer = StageTimer()
        self.run_log_path = project_root / "run_log.json"
        self.deferred_path = project_root / "deferred_jobs.json"

    # ------------------------------------------------------------------
    # Profile freshness nudge
    # ------------------------------------------------------------------

    def _banner(self, mode: str) -> None:
        ui.banner(mode, [("profile", self.profile_path.name), ("model", self.model_writing)])

    def _ensure_profile_fresh(self) -> bool:
        """If the profile hasn't been edited or confirmed within `review_days`, ask the
        user to confirm it's current. Returns False to abort so they can edit it first.
        Skipped in --auto mode."""
        if self.auto:
            return True

        mtime = date.fromtimestamp(self.profile_path.stat().st_mtime)
        days = profile_age_days(mtime, self._load_review_date(), date.today())
        if days < self.review_days:
            return True

        ui.section("Profile check", f"last updated or confirmed {days} days ago")
        ui.say("Are roles, skills, salary and availability in your profile still current?")
        ui.say(ui.link(self.profile_path, self.profile_path.name))
        ui.keys(("Enter", "yes, continue"), ("n", "no, I'll edit it first"))
        if ui.ask().lower() == "n":
            ui.note(f"Update {self.profile_path.name}, then run again.")
            return False

        self._save_review_date(date.today())
        ui.ok(f"Confirmed. Next check in {self.review_days} days.")
        return True

    def _load_review_date(self) -> date | None:
        try:
            data = json.loads(self.review_state_path.read_text(encoding="utf-8"))
            return date.fromisoformat(data["last_reviewed"])
        except (FileNotFoundError, KeyError, ValueError):
            return None

    def _save_review_date(self, d: date) -> None:
        self.review_state_path.write_text(
            json.dumps({"last_reviewed": d.isoformat()}, indent=2), encoding="utf-8"
        )

    def run(self) -> None:
        """Execute the full pipeline."""
        self._banner("search and apply")

        if not self._ensure_profile_fresh():
            return

        self.timer = StageTimer()

        boards = " and ".join(ui.board(p) for p in self.config["search"]["platforms"])
        ui.section("Search", self.config["search"].get("location", ""))
        try:
            with self.timer.stage("discovery"), ui.status(f"Searching {boards}, then scoring each posting…"):
                jobs = self._discover_jobs()
        except QuotaExhaustedError:
            ui.fail("The daily Gemini quota is used up, so no posting could be scored.",
                    "Enable billing on your Google Cloud project, or run again after the daily reset.")
            return
        except RateLimitedError:
            ui.fail("Gemini is overloaded right now, so no posting could be scored.",
                    "This usually passes within minutes. Run again shortly.")
            return
        stats = getattr(self.searcher, "last_stats", {})
        if not jobs:
            ui.warn("No new postings above your match threshold.",
                    "Run again later, or widen search.keyword_groups in config.yaml.")
            return
        found = stats.get("found")
        ui.ok(f"{found} postings on {boards}, {len(jobs)} match your profile"
              if found else f"{len(jobs)} postings match your profile")

        selected = self._user_select_jobs(jobs)
        if not selected:
            ui.note("No jobs picked. Nothing to do.")
            return

        results: list[dict] = []
        for i, job in enumerate(selected, 1):
            counter = f"{i}/{len(selected)}" if len(selected) > 1 else ""
            try:
                result, control = self._process_with_recovery(job, counter)
            except KeyboardInterrupt:
                ui.warn("Stopped. The remaining jobs were skipped.")
                break
            except Exception as e:
                logger.error(f"Failed to process {job.company}: {e}", exc_info=True)
                ui.fail(f"{escape(job.company)}: {escape(str(e))}")
                results.append({"job": job.id, "company": job.company, "title": job.title,
                                "status": "error", "error": str(e)})
                continue

            if result is not None:
                results.append(result)
            if control == "abort":
                ui.warn("Run stopped. The remaining jobs were skipped.")
                break

        self._print_summary(results)
        self._finalize_run(results)

    def run_urls(self, urls: list[str]) -> None:
        """Direct-apply several postings in one run, each fully isolated.

        Every URL is fetched, parsed and processed on its own; a posting that
        cannot be read is reported and skipped without stopping the batch.
        """
        self._banner(f"apply to {len(urls)} postings")

        if not self._ensure_profile_fresh():
            return

        self.timer = StageTimer()
        results: list[dict] = []

        for i, url in enumerate(urls, 1):
            ui.section(f"Posting {i} of {len(urls)}", url)
            try:
                job = self._fetch_and_parse(url)
            except NotAJobPostingError as e:
                self._report_not_a_posting(e, url)
                results.append({"job": url, "company": "?", "title": url, "status": "skipped"})
                continue
            except Exception as e:
                logger.error(f"Failed to read {url}: {e}", exc_info=True)
                ui.fail(f"Could not read the posting: {escape(str(e))}")
                results.append({"job": url, "company": "?", "title": url,
                                "status": "error", "error": str(e)})
                continue

            try:
                result, control = self._process_with_recovery(job, f"{i}/{len(urls)}")
            except KeyboardInterrupt:
                ui.warn("Stopped. The remaining postings were skipped.")
                break
            except Exception as e:
                logger.error(f"Failed to process {job.company}: {e}", exc_info=True)
                ui.fail(f"{escape(job.company)}: {escape(str(e))}")
                results.append({"job": job.id, "company": job.company, "title": job.title,
                                "status": "error", "error": str(e)})
                continue

            if result is not None:
                results.append(result)
            if control == "abort":
                ui.warn("Run stopped. The remaining postings were skipped.")
                break

        self._print_summary(results)
        self._finalize_run(results)

    def _fetch_and_parse(self, url: str) -> JobListing:
        """Fetch one posting URL and parse it into a JobListing."""
        with self.timer.stage("fetch"), ui.status("Reading the posting…"):
            raw_text = fetch_url_text(url)
        if len(raw_text.strip()) < 200:
            raise NotAJobPostingError(
                "The page returned almost no text, so the portal likely blocks scraping."
            )
        with self.timer.stage("parse"), ui.status("Extracting role, company and requirements…"):
            return self.searcher.parse_posting(raw_text, url=url)

    def run_single(self, url: str | None = None, paste: bool = False) -> None:
        """Direct-apply path: generate an application for one specific posting,
        skipping discovery and scoring entirely."""
        self._banner("apply to one posting")

        if not self._ensure_profile_fresh():
            return

        self.timer = StageTimer()

        raw_text = ""
        if paste:
            ui.section("Paste the job description", "then press Ctrl-D (Ctrl-Z and Enter on Windows)")
            raw_text = sys.stdin.read()
        elif url:
            with self.timer.stage("fetch"), ui.status("Reading the posting…"):
                raw_text = fetch_url_text(url)
            if len(raw_text.strip()) < 200:
                ui.warn("The page returned almost no text. The portal likely blocks scraping.",
                        "Paste the description instead:")
                ui.command(f"python run.py --paste --url {url}")
                return
        else:
            ui.fail("Give a posting with --url <link>, or paste one with --paste.")
            return

        if len(raw_text.strip()) < 50:
            ui.warn("No posting text came in. Nothing to do.")
            return

        try:
            with self.timer.stage("parse"), ui.status("Extracting role, company and requirements…"):
                job = self.searcher.parse_posting(raw_text, url=url or "")
        except NotAJobPostingError as e:
            self._report_not_a_posting(e, url)
            return

        self._print_job_detail(job)

        if not self.auto:
            ui.keys(("Enter", "write the application"), ("n", "cancel"))
            if ui.ask().lower() == "n":
                ui.note("Cancelled.")
                return

        try:
            result, _control = self._process_with_recovery(job)
        except Exception as e:
            logger.error(f"Failed to process {job.company}: {e}", exc_info=True)
            ui.fail(f"{escape(job.company)}: {escape(str(e))}")
            result = {
                "job": job.id, "company": job.company, "title": job.title,
                "status": "error", "error": str(e),
            }
        if result is None:
            result = {"job": job.id, "company": job.company, "title": job.title, "status": "aborted"}

        self._print_summary([result])
        self._finalize_run([result])

    def _report_not_a_posting(self, error: Exception, url: str | None) -> None:
        """Explain a rejected posting and point at the paste fallback.

        Portals routinely serve a consent dialog or JS shell instead of the
        posting; pasting the description by hand is the reliable way through.
        """
        if url:
            ui.warn(f"No readable job posting on that page. {escape(str(error))}",
                    "Portals often block scraping. Paste the description instead:")
            ui.command(f"python run.py --paste --url {url}")
        else:
            ui.warn(f"That text doesn't look like a job posting. {escape(str(error))}",
                    "Check that the full description was pasted.")

    def run_deferred(self) -> None:
        """Retry jobs the user marked for later (deferred_jobs.json)."""
        self._banner("retry saved jobs")

        jobs = self._load_deferred()
        if not jobs:
            ui.note("No saved jobs. When Gemini limits a run, press m to save a job for later.")
            return
        ui.say(f"{len(jobs)} job(s) saved for later")

        if not self._ensure_profile_fresh():
            return

        self.timer = StageTimer()
        # Clear the queue; re-marks and aborted/unreached jobs are re-added below.
        self._save_deferred([])

        results: list[dict] = []
        for i, job in enumerate(jobs, 1):
            try:
                result, control = self._process_with_recovery(job, f"{i}/{len(jobs)}")
            except KeyboardInterrupt:
                self._save_deferred(self._load_deferred() + jobs[i - 1:])
                ui.warn("Stopped. The remaining jobs stay saved for later.")
                break
            except Exception as e:
                logger.error(f"Failed to process {job.company}: {e}", exc_info=True)
                ui.fail(f"{escape(job.company)}: {escape(str(e))}")
                results.append({"job": job.id, "company": job.company, "title": job.title,
                                "status": "error", "error": str(e)})
                continue

            if result is not None:
                results.append(result)
            if control == "abort":
                # Current job + the rest stay deferred (re-marks already saved themselves).
                self._save_deferred(self._load_deferred() + [job] + jobs[i:])
                ui.warn("Run stopped. The remaining jobs stay saved for later.")
                break

        self._print_summary(results)
        self._finalize_run(results)

    # ------------------------------------------------------------------
    # Run timing + trend log
    # ------------------------------------------------------------------

    def _finalize_run(self, results: list[dict]) -> None:
        """Append the run to run_log.json (per-stage timings and cost, for trends)."""
        total = self.timer.total_seconds
        accepted = sum(1 for r in results if r.get("status") == "accepted")
        usage = usage_tracker.summary()

        self._append_run_log(results, total, accepted, usage)
        ui.console.print()

    def _append_run_log(self, results: list[dict], total: float, accepted: int, usage: dict) -> None:
        """Append one run entry to run_log.json (best-effort; never breaks a run)."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": "auto" if self.auto else "interactive",
            "jobs_processed": len(results),
            "accepted": accepted,
            "total_seconds": round(total, 1),
            "stages": {label: round(secs, 1) for label, secs in self.timer.stages.items()},
            "cost_usd": round(usage.get("total_cost_usd", 0.0), 4),
            "api_calls": usage.get("total_calls", 0),
        }
        try:
            log = []
            if self.run_log_path.exists():
                log = json.loads(self.run_log_path.read_text(encoding="utf-8"))
            log.append(entry)
            self.run_log_path.write_text(
                json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"Could not write run_log.json: {e}")

    # ------------------------------------------------------------------
    # Step 1 — Discovery
    # ------------------------------------------------------------------

    def _discover_jobs(self) -> list[JobListing]:
        jobs = self.searcher.search_all()
        if jobs:
            self.searcher.save_results(jobs, self.output_dir)
        return jobs

    # ------------------------------------------------------------------
    # Step 2 — User Selection
    # ------------------------------------------------------------------

    def _user_select_jobs(self, jobs: list[JobListing]) -> list[JobListing]:
        ui.console.print()
        # Role and company share what the terminal has left; the board column goes first
        # on narrow terminals.
        wide = ui.console.width >= 100
        avail = ui.console.width - (2 + 6 + 12 + (9 if wide else 0)) - 2 * (6 if wide else 5) - 2
        role_w, company_w = min(34, avail * 55 // 100), min(26, avail * 45 // 100)
        cols = [("#", 2), ("Match", 6), ("Role", role_w), ("Company", company_w)]
        cols += [("Board", 9)] if wide else []
        t = ui.table(*cols, ("Note", 12), right=("#", "Match"))
        for i, job in enumerate(jobs, 1):
            style, label = ui.LEGITIMACY.get(job.legitimacy_tier, ("muted", ""))
            flag = f"[{style}]{label}[/]" if label else ""
            row = [f"[muted]{i}[/]",
                   f"[{ui.score_style(job.match_score)}]{round(job.match_score * 100)}[/]",
                   escape(job.title), escape(job.company)]
            row += [f"[muted]{ui.board(job.platform)}[/]"] if wide else []
            t.add_row(*row, flag)
        ui.console.print(ui.indent(t))

        if self.auto:
            selected = [j for j in jobs if j.legitimacy_tier != "suspicious"]
            skipped = len(jobs) - len(selected)
            ui.note(f"Auto mode: taking {len(selected)} of {len(jobs)}"
                    + (f", skipping {skipped} likely fake" if skipped else ""))
            return selected

        ui.console.print()
        ui.keys(("1,3", "pick jobs"), ("all", "every job"), ("d2", "details of job 2"), ("q", "quit"))

        while True:
            choice = ui.ask().lower()

            if choice == "q":
                return []
            if choice == "all":
                return list(jobs)

            if choice.startswith("d"):
                try:
                    idx = int(choice[1:]) - 1
                    if 0 <= idx < len(jobs):
                        self._print_job_detail(jobs[idx])
                    else:
                        ui.warn(f"There is no job {idx + 1}.")
                except ValueError:
                    ui.warn("Type d and a number, for example d2.")
                continue

            try:
                indices = [int(x.strip()) - 1 for x in choice.split(",")]
                selected = [jobs[i] for i in indices if 0 <= i < len(jobs)]
                if selected:
                    return selected
                ui.warn(f"Pick numbers between 1 and {len(jobs)}.")
            except ValueError:
                ui.warn("Type job numbers separated by commas, for example 1,3.")

    def _print_job_detail(self, job: JobListing) -> None:
        ui.section(escape(job.title), escape(job.company))
        scored = job.platform != "direct"  # a pasted or linked posting has no match score
        facts = [("Match", f"[{ui.score_style(job.match_score)}]{round(job.match_score * 100)}[/]")] if scored else []
        facts += [("Where", escape(job.location or "not stated")),
                 ("Type", escape(job.employment_type or "not stated")),
                 ("Language", job.language.value.upper())]
        if job.posted_date:
            facts.append(("Posted", escape(job.posted_date)))
        grid = ui.Table.grid(padding=(0, 2))
        grid.add_column(style="muted")
        grid.add_column()
        for label, value in facts:
            grid.add_row(label, value)
        if job.url:
            grid.add_row("Link", ui.link_url(job.url))
        if job.match_reasoning and scored:
            grid.add_row("Why", escape(job.match_reasoning))
        if job.requirements:
            grid.add_row("Asks for", escape(", ".join(job.requirements[:8])))
        style, label = ui.LEGITIMACY.get(job.legitimacy_tier, ("muted", ""))
        if label:
            why = "; ".join(job.legitimacy_signals) or "no reason given"
            grid.add_row("Posting", f"[{style}]{label}[/]: {escape(why)}")
        ui.console.print(ui.indent(grid))
        ui.console.print()

    # ------------------------------------------------------------------
    # Steps 3-6 — Process a single job
    # ------------------------------------------------------------------

    def _process_job(self, job: JobListing, counter: str = "") -> dict:
        result = {"job": job.id, "company": job.company, "title": job.title}
        slip = ui.Slip(escape(job.title), escape(job.company), counter)

        with self.timer.stage("research"), slip.step("Company research") as s:
            research = self.writer.research_company(job)
            if research:
                s.detail = "found sources on the web"
            else:
                s.warn("nothing reliable found, the letter will stick to the posting")

        with self.timer.stage("generate"), slip.step("CV and letter") as s:
            draft = self.writer.generate(job, research=research)
            words = sum(len(str(draft.cover_letter.get(f"para{i}", "")).split()) for i in range(1, 5))
            s.detail = f"letter {words} words"

        with self.timer.stage("hr_review"), slip.step("Review") as s:
            rounds: list[str] = []

            def on_review(n: int, review: HRReview, passed: bool) -> None:
                rounds.append(f"CV {review.cv_score}  letter {review.cl_score}")
                s.detail = " → ".join(rounds) + ("" if passed else ", rewriting…")

            draft, review = self.hr.review_loop(self.writer, job, draft, on_review=on_review)
            passed = self.hr._passed(review)
            s.detail = " → ".join(rounds)
            if not passed:
                s.warn(f"{s.detail}, below the bar of {self.hr.pass_score}: see hr_review.json")

        # Ask the user to supply any required field the generator left blank/placeholder
        self._fill_missing_fields(draft)

        # Keep the review: it is the only record of why a draft scored what it did,
        # which is what you want when a rendered .tex looks weak.
        self.writer.save_posting(draft, self.output_dir)
        folder = Path(self.output_dir) / draft.job.folder_name
        self.hr.save_review(review, str(folder))

        result["cv_score"] = review.cv_score
        result["cl_score"] = review.cl_score
        result["passed_review"] = passed
        result["folder"] = str(folder)

        try:
            with self.timer.stage("render"), slip.step("PDFs") as s:
                manifest = self.exporter.export(draft, self.output_dir)
                pages = (manifest["cv"].get("pages", 0), manifest["cl"].get("pages", 0))
                s.detail = "one page each" if pages == (1, 1) else f"CV {pages[0]} pages, letter {pages[1]}"
                if max(pages) > 1:
                    s.warn(f"CV {pages[0]} pages, letter {pages[1]}: shorten before sending")
        except LatexError as e:
            logger.error(f"LaTeX export failed: {e}")
            ui.fail("xelatex could not build the PDFs.", "The LaTeX log above shows why.")
            result["status"] = "export_failed"
            return result
        result["export_manifest"] = manifest

        app = self.tracker.add_application(
            draft,
            cv_pdf_path=manifest["cv"]["pdf_path"],
            cl_pdf_path=manifest["cl"]["pdf_path"],
            output_dir=self.output_dir,
        )
        result["status"] = "accepted"
        result["tracker_id"] = app.id
        ui.console.print(f"    [muted]saved to[/] {ui.link(folder, folder.name + '/')}")
        return result

    # ------------------------------------------------------------------
    # Fill missing required fields
    # ------------------------------------------------------------------

    def _fill_missing_fields(self, draft: ApplicationDraft) -> None:
        """Before rendering, ask the user to supply any required field the generator
        left blank or as a placeholder (job title, recipient address, …). Enter skips
        a field. Skipped entirely in --auto."""
        if self.auto:
            return

        job = draft.job
        cl = draft.cover_letter

        # (key, current value, label). Extend this list to prompt for more fields.
        fields = [
            ("title", job.title, "Job title"),
            ("recipient", cl.get("recipient", ""),
             "Recipient address (contact, company, street, postcode and city, separated by commas)"),
        ]
        missing = [(k, label) for k, value, label in fields if _is_missing(value)]
        if not missing:
            return

        ui.console.print()
        ui.warn("A few details are missing from the posting. Type them in, or press Enter to skip.")
        for key, label in missing:
            ui.note(label)
            entry = ui.ask()
            if not entry:
                continue
            if key == "title":
                job.title = entry
                if _is_missing(cl.get("subject", "")):
                    de = draft.language == JobLanguage.DE
                    cl["subject"] = (f"Bewerbung als {entry}" if de else f"Application as {entry}")
            elif key == "recipient":
                lines = [
                    p.strip()
                    for p in entry.replace("\\n", "\n").replace(",", "\n").splitlines()
                    if p.strip()
                ]
                cl["recipient"] = "\n".join(lines)

    # ------------------------------------------------------------------
    # Rate-limit recovery
    # ------------------------------------------------------------------

    def _process_with_recovery(self, job: JobListing, counter: str = "") -> tuple[dict | None, str]:
        """Process a job; on rate-limit/quota errors let the user skip, retry, mark
        for next time, or abort. Returns (result_or_None, control) where control is
        'ok' or 'abort'."""
        while True:
            try:
                return self._process_job(job, counter), "ok"
            except (QuotaExhaustedError, RateLimitedError) as e:
                action = self._rate_limit_prompt(job, e)
                if action == "retry":
                    continue
                if action == "skip":
                    return {"job": job.id, "company": job.company, "title": job.title,
                            "status": "skipped"}, "ok"
                if action == "mark":
                    self._mark_deferred(job)
                    return {"job": job.id, "company": job.company, "title": job.title,
                            "status": "deferred"}, "ok"
                return None, "abort"

    def _rate_limit_prompt(self, job: JobListing, error: Exception) -> str:
        """Ask what to do after a rate-limit/quota error. Returns skip|retry|mark|abort."""
        is_quota = isinstance(error, QuotaExhaustedError)
        if is_quota:
            ui.fail("The daily Gemini quota is used up.",
                    "Enable billing on your Google Cloud project, or wait for the daily reset.")
        else:
            ui.fail("Gemini kept refusing requests (rate limit), even after retries.")

        if self.auto:
            action = "abort" if is_quota else "skip"
            ui.note(f"Auto mode: {'stopping the run' if action == 'abort' else 'skipping this job'}")
            return action

        ui.keys(("r", "retry"), ("s", "skip this job"), ("m", "save it for later"), ("a", "stop the run"))
        choices = {"s": "skip", "r": "retry", "m": "mark", "a": "abort"}
        while True:
            c = ui.ask().lower()
            if c in choices:
                return choices[c]
            if c in choices.values():
                return c
            ui.warn("Type r, s, m or a.")

    def _load_deferred(self) -> list[JobListing]:
        if not self.deferred_path.exists():
            return []
        try:
            return [JobListing.from_dict(d) for d in json.loads(self.deferred_path.read_text(encoding="utf-8"))]
        except Exception as e:
            logger.warning(f"Could not read deferred_jobs.json: {e}")
            return []

    def _save_deferred(self, jobs: list[JobListing]) -> None:
        try:
            self.deferred_path.write_text(
                json.dumps([j.to_dict() for j in jobs], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"Could not write deferred_jobs.json: {e}")

    def _mark_deferred(self, job: JobListing) -> None:
        """Append a job to deferred_jobs.json to retry in a later run."""
        self._save_deferred(self._load_deferred() + [job])
        ui.ok("Saved for later. Retry it with:")
        ui.command("python run.py --deferred")

    # ------------------------------------------------------------------
    # Step 5 — User Preview
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def _print_summary(self, results: list[dict]) -> None:
        if not results:
            return
        done = [r for r in results if r.get("status") == "accepted"]
        usage = usage_tracker.summary()
        total = self.timer.total_seconds

        note = f"in {human_duration(total)}"
        if usage["total_calls"]:
            note += f", {usage['total_calls']} Gemini calls for about ${usage['total_cost_usd']:.2f}"
        if len(results) == 1:
            title = "Application ready" if done else "Application not finished"
        else:
            title = f"{len(done)} of {len(results)} applications ready"
        ui.section(title, note)

        labels = {"skipped": ("warn", "skipped"), "deferred": ("warn", "saved for later"),
                  "error": ("fail", "failed"), "export_failed": ("fail", "PDF failed"),
                  "aborted": ("muted", "stopped")}
        avail = ui.console.width - (3 + 7 + 14) - 2 * 5 - 2
        t = ui.table(("Company", min(26, avail * 45 // 100)), ("Role", min(34, avail * 55 // 100)),
                     ("CV", 3), ("Letter", 7), ("Status", 14), right=("CV", "Letter"))
        for r in results:
            status = r.get("status")
            if status == "accepted" or "cv_score" in r:
                cv = f"[{ui.hr_style(r['cv_score'])}]{r['cv_score']}[/]"
                cl = f"[{ui.hr_style(r['cl_score'])}]{r['cl_score']}[/]"
            else:
                cv = cl = ""
            if status == "accepted":
                where = "[pass]ready[/]" if r.get("passed_review") else "[warn]check review[/]"
            else:
                style, label = labels.get(status, ("muted", status or ""))
                where = f"[{style}]{label}[/]"
            t.add_row(escape(r.get("company", "?")), escape(r.get("title", "")), cv, cl, where)
        ui.console.print(ui.indent(t))

        weak = [r for r in done if not r.get("passed_review")]
        long_docs = [r for r in done
                     if any(d.get("pages", 0) > 1 for d in r.get("export_manifest", {}).values()
                            if isinstance(d, dict))]
        if weak:
            ui.warn(f"{len(weak)} scored below the review bar.", "Each folder's hr_review.json says what to fix.")
        if long_docs:
            ui.warn(f"{len(long_docs)} run over one page.", "Shorten the .tex and run xelatex again.")
        if done:
            ui.console.print()
            ui.say(f"PDFs are in {ui.link(self.output_dir)}")
            ui.say("Read every PDF before you send it. When you've sent one, log it with")
            ui.command("python run_tracker.py")

# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Job application pipeline")
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Non-interactive mode: auto-select all scored jobs and accept all drafts",
    )
    parser.add_argument(
        "--url",
        action="append",
        metavar="URL",
        help="Direct apply: posting URL (skips discovery). Repeatable, or pass several "
             "separated by ';'. Use --url with no value to be prompted for a list.",
    )
    parser.add_argument(
        "--urls",
        action="store_true",
        help="Direct apply: prompt for a list of posting URLs, one per line or ';'-separated",
    )
    parser.add_argument(
        "--paste",
        action="store_true",
        help="Direct apply: read a single job description from stdin (optionally with --url to record the link)",
    )
    parser.add_argument(
        "--deferred",
        action="store_true",
        help="Retry jobs previously marked for later (deferred_jobs.json)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed logs (requests, retries, file writes)",
    )
    args = parser.parse_args()

    # --url is repeatable and each value may itself hold several ';'-separated links.
    urls = split_urls(" ".join(args.url)) if args.url else []

    try:
        pipeline = Pipeline(auto=args.auto)
        if args.deferred:
            pipeline.run_deferred()
        elif args.paste:
            # Pasting a description is inherently one posting at a time.
            pipeline.run_single(url=urls[0] if urls else None, paste=True)
        elif args.urls or (args.url and not urls):
            collected = prompt_for_urls()
            if not collected:
                ui.note("No links given. Nothing to do.")
                return
            pipeline.run_urls(collected)
        elif len(urls) == 1:
            pipeline.run_single(url=urls[0], paste=False)
        elif urls:
            pipeline.run_urls(urls)
        else:
            pipeline.run()
    except KeyboardInterrupt:
        ui.console.print()
        ui.note("Stopped.")
        sys.exit(0)
    except FileNotFoundError as e:
        ui.fail(f"File not found: {escape(str(e))}")
        sys.exit(1)
    except Exception as e:
        if "API_KEY" in str(e).upper() or "auth" in str(e).lower() or "401" in str(e):
            ui.fail("Gemini rejected the API key.",
                    "Put a valid key in .env as GEMINI_API_KEY=… (see README, step 3).")
            sys.exit(1)
        raise


if __name__ == "__main__":
    main()
