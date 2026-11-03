# CLAUDE.md

Guidance for working in this repository. Read this before making changes.

## Overview

`job_agent` is a **local-first, single-user** AI job-application pipeline + CRM. It
discovers job postings, generates tailored CVs and cover letters with the Gemini API,
runs them through an HR-quality review loop, renders finished PDFs locally with
`xelatex`, and tracks each application's lifecycle.

**Philosophy (do not drift from these without explicit instruction):**
- **Local-first** — data, rendering, and state live on the machine. No server, no DB.
- **Human-in-the-loop** — the user reviews and sends every application. No auto-send.
- **Quality over volume** — the goal is strong, tailored applications, not throughput.

Four phases are shipped: generation, CRM state management, multi-language strictness
(DE/EN), email drafting. The forward plan lives in [ROADMAP.md](ROADMAP.md).

## Entry points

| Command | What it does |
|---------|--------------|
| `python run.py` | Full pipeline: discover → score → select → generate → review → render → track (interactive). |
| `python run.py --auto` | Non-interactive: auto-selects non-suspicious jobs, auto-accepts drafts. |
| `python run.py --url <url>` | Direct apply to one posting without discovery. Repeatable, or pass several `;`-separated. |
| `python run.py --urls` | Direct apply to several postings: prompts for a list (one per line or `;`-separated), each processed in isolation. |
| `python run.py --paste` | Paste one JD from stdin when the portal blocks scraping (single posting only). |
| `python run.py --deferred` | Retry jobs marked `[m]` for later during a rate-limit (`deferred_jobs.json`). |
| `python run_tracker.py` | CRM menu: overview, status update, interview prep, follow-up email, deadlines, metrics. |

## Architecture

Two orchestrators drive a set of single-purpose agents in `agents/`.

**`run.py` — `Pipeline`** (6 steps):
1. **Discovery** — `JobSearcher.search_all()`
2. **Selection** — user picks from a scored table (`--auto` selects all non-suspicious)
3–5. **Per job** — `_process_job()`: generate → HR review loop → LaTeX export → track. There is no
   per-draft prompt: everything renders, and the summary reports HR scores and flags drafts below
   the pass bar. The rendered `.tex` is the review surface.

**`run_tracker.py` — `TrackerUI`**: menu over `ApplicationTracker` + `InterviewAgent`.

### Agent roster (`agents/`)

| Module | Role |
|--------|------|
| `job_searcher.py` | JobSpy scrape (Indeed + LinkedIn) → URL dedup (`seen_jobs.json`) → Gemini batch-score (5 dims) + legitimacy tiering → `JobListing`. `parse_posting` handles the direct-apply path and raises `NotAJobPostingError` rather than inventing a posting. |
| `writer.py` | Company research (grounded, or empty) → tailored CV sections + cover letter (archetype/tone, ATS keyword mirroring, word caps). **Work experience is profile-driven**: `parse_experiences` reads `## Berufserfahrung`, the writer selects the relevant entries per job by index and tailors bullets (company/role/dates never invented) → `ApplicationDraft.experiences`. |
| `hr_agent.py` | Level-calibrated reviewer scores drafts 0–10 against the profile (fabrication check) plus per-archetype criteria; sends failing drafts back with the previous draft and cached research, up to `max_loops`. |
| `latex_exporter.py` | Renders Jinja2 `.tex` templates → compiles to PDF with `xelatex`; language-routed templates. Re-renders at tighter `fit` levels until each document is one page; the cover letter follows DIN 5008 Form B (absolute positions). |
| `interview_agent.py` | Interview prep guides + follow-up email drafts. |
| `web_research.py` | DuckDuckGo HTML search + page fetch (httpx + BeautifulSoup) — the *only* permitted source of company facts; no web content means no research. |
| `style_filter.py` | Anti-AI-tell pass: replaces banned phrases + em-dashes **deterministically** via `sanitize_sections` (no LLM re-prompt). |
| `latex_utils.py` | `parse_kontakt` (contact key:value parse), Jinja env, LaTeX-safe filters/escaping. |
| `usage_tracker.py` | Gemini token + cost accounting (`usage_tracker` singleton). |

### Data & persistence

- **`models.py`** — dataclasses: `JobListing`, `ApplicationDraft`, `HRReview`,
  `TrackedApplication`; enums `JobLanguage` (DE/EN), `ApplicationStatus`, `CompanyTone`.
  All use the `to_dict` / `from_dict` round-trip pattern.
- **`tracker.py`** — `ApplicationTracker`: CRUD over `applications.json` with **atomic
  writes** (temp file + `os.replace`).
- **`metrics.py`** — `compute_metrics()` (pure): funnel + rates + by-status/language from
  tracked apps, surfaced as tracker menu **[6] Metrics**.
- **`ui.py`** — shared rich `Console`, palette and helpers (see Conventions → Terminal UI).
- **`config.yaml`** — search keywords/boards/thresholds, DE/EN template routing,
  `profile` path, `market` (prompt job market), `output_dir`, `profile_review_days`, per-task `models:`
  (bulk/writing/review), `hr_review:` (`pass_score`, `max_loops`), `gemini:` (`rpm_delay`).
- **State files (gitignored):** `applications.json`, `seen_jobs.json`,
  `profile_review.json` (last profile-confirmation date), `run_log.json` (per-run timing +
  cost trend, written by `agents/timing.py` `StageTimer`), `deferred_jobs.json` (jobs the
  user marked to retry after a rate-limit), dated `applications/<date>/` output folders
  (per job: `job_posting.md`, `company_research.md`, `hr_review.json`, and the rendered
  `.tex` + `.pdf`).

### Data flow

```
scrape → dedup(URL) → Gemini score + legitimacy → user select
  → company research → generate CV/CL → style-filter → HR review loop
  → LaTeX render (xelatex) → track (scores reported in the run summary)
```

## Conventions

- **Gemini model** defaults to `gemini-2.5-flash-lite` (constant `MODEL` in each agent).
  Per-task overrides via `config.yaml` `models:` (bulk/writing/review) are threaded into
  each agent's `model` param. Key via `GEMINI_API_KEY` in `.env`.
- **HR reviewer** calibrates to the candidate via `candidate_context` (from `## Eckdaten`)
  — no hardcoded persona; pass bar + loops come from `hr_review:` config.
- **Language routing** — `JobLanguage` (DE/EN) selects templates via `config.yaml`
  `templates:` (DE → `eu_de`, EN → `eu_en`).
- **LaTeX templates** use Jinja with custom delimiters: `<< var >>`, `<% block %>`.
  Always run user text through the LaTeX-escaping filters in `latex_utils.py`.
- **Terminal UI** — all user-facing output goes through `ui.py` (one rich `Console`, palette
  derived from the PDF navy): `banner`, `section`, `ok`/`warn`/`fail` (with a fix hint),
  `ask`/`keys`, `table`, `status`, and `Slip` (the per-application routing slip of steps).
  No raw `print` or ANSI codes. Logs are WARNING+ by default, INFO with `--verbose`
  (`ui.setup_logging`). Messages say what happened and what to do next, in sentence case.
- **Demo + screenshots** — `scripts/demo/run_demo.py` runs the real CLI against simulated
  boards and Gemini in a temp copy; `record.py` + `render.mjs` regenerate `docs/media/`.
  Re-record after visible UI changes.
- **Atomic JSON writes** — never write state files in place; follow the temp-file +
  `os.replace` pattern in `tracker.py`.
- **Rate limits** — Gemini call pacing is `config.yaml` `gemini.rpm_delay` (keep low with
  billing enabled). `agents/utils.generate_with_retry` raises `QuotaExhaustedError`
  (daily/project cap — fail fast) or `RateLimitedError` (transient, retries exhausted); the
  pipeline then offers skip / retry / mark-for-later (`deferred_jobs.json`) / abort.
- **Grounding invariant** — the pipeline refuses to invent. `parse_posting` must confirm the text
  really is a posting (`NotAJobPostingError` otherwise, offering the `--paste` path), and
  `_research_company` returns `""` when web research finds nothing rather than falling back to
  model priors. A generic cover letter beats a confidently wrong one.
- **Reviewer sees the profile** — `HRAgent` receives the redacted profile as ground truth and runs a
  fabrication check before scoring; untraceable claims cap the score at 3. Keep `review` on a
  *different* model than `writing`: a model grading its own output is systematically lenient.

## Commits & workflow

- **Commit only with the user's explicit approval.** Do not commit or push
  automatically, even when work is finished and tests pass.
- Prefer **logical, atomic commits** — one coherent change per commit, with a clear
  message — but propose them and wait for the go-ahead before running `git commit`.
- Never commit a real profile or any PII (see the privacy invariant below).

## Privacy invariant (load-bearing — do not regress)

**Contact PII renders locally only; it must never appear in an LLM prompt.**
- Contact data (name, address, DOB, email, phone) is for the **LaTeX letterhead only**,
  injected locally via `parse_kontakt` in `latex_exporter.py`. It does **not** need to
  reach Gemini.
- The scoring path (`_compress_profile`) is PII-free. The writer path is enforced by
  `agents/privacy.py` `redact_profile`, applied at the `ApplicationWriter` boundary
  (`__init__`): the `## Kontakt` block is stripped (plus a defensive email/phone scrub)
  before any generation prompt. `HRAgent` and `InterviewAgent` redact at their boundaries too.
  Covered by `tests/test_privacy.py`.
- **Repo hygiene:** the real profile is **gitignored**; only `profile.example.md` is
  tracked. Never commit a real profile.

## Profile

The candidate profile is a single source of truth with two parts:
- **Facts header** — two machine-parsed `key: value` blocks: `## Kontakt` (letterhead
  contact, via `parse_kontakt`) and `## Eckdaten` (scoring facts: level, target_roles,
  location, languages, salary, availability).
- **Prose body** (markdown): the narrative the writer consumes (redacted of contact
  facts per the privacy invariant).

`agents/profile.py` `build_scoring_summary()` **derives** the scoring summary from the
profile (`## Eckdaten` facts + a few prose sections) — there is **no** hardcoded
duplicate, so profile edits flow straight into scoring. Parsers: `parse_section_kv`,
`extract_section`. Covered by `tests/test_profile.py`. **New-user onboarding:** copy
`profile.example.md` → a personal, gitignored profile → point `config.yaml` `profile:`
at it.

**Freshness nudge:** on interactive runs, if the profile hasn't been edited or confirmed
within `config.yaml` `profile_review_days` (default 30), the pipeline asks the user to
confirm it's still current before proceeding (`Pipeline._ensure_profile_fresh`, pure
helper `profile_age_days`). Skipped in `--auto`.

## Commands

```bash
python run.py [--auto]                 # full pipeline
python run.py --url <job_url>          # direct apply to one posting
python run.py --url "<a>;<b>;<c>"      # several postings, each isolated
python run.py --urls                   # prompt for a list of posting URLs
python run.py --paste --url <job_url>  # paste the JD if the portal blocks scraping
python run_tracker.py                  # CRM
pytest                                 # full test suite (tests/)
python scripts/smoke_latex.py          # LaTeX render smoke test
python scripts/check_layout.py         # worst-case layout check (page count + DIN 5008 positions)
python scripts/test_writer_quality.py  # writer quality harness
```

Requires a local TeX install providing `xelatex`.

## Roadmap

Forward plan, phases, and verification steps: **[ROADMAP.md](ROADMAP.md)**.
