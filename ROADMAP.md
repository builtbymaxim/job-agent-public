# ROADMAP

Forward plan to elevate `job_agent` from a solid script to an S-tier local job
pipeline. See [CLAUDE.md](CLAUDE.md) for current architecture.

## Direction (decided)

- **Interface:** deepen pipeline logic first; a private app (iOS or web) is a *maybe,
  later* — no frontend now.
- **LLM + privacy:** stay on Gemini for quality, but **strip PII** from prompts now;
  local/hybrid models are a future option.
- **Autonomy:** **manual** — the user reviews and sends every application. No SMTP
  auto-send, no auto-apply.
- **Data:** keep flat JSON; add lightweight metrics. No DB migration.
- **Profile:** structured front-matter + prose, single source of truth, multi-user /
  public-repo ready.
- **Performance:** time every run and report it — the tool exists to save time.
- **Entry modes:** add a direct-URL / paste apply path alongside discovery.

Shipped already (v1–v4): generation, CRM state management, multi-language strictness
(DE/EN), email drafting.

---

## Phase 0 — Repo hygiene + profile privacy — DONE

**Goal:** No real PII in the repo or its history; safe to publish and usable by a second
person.

- Untrack the real profile; gitignore the `profile_*.md` pattern.
- Add a sanitized, tracked `profile.example.md` with placeholder PII; new users copy it
  to their own gitignored profile and point `config.yaml` `profile:` at it.
- Fix salary typo `45.0000€` → `45.000€`.
- History scrubbed: no real profile, assets or application output in any commit.
- Sharing readiness: MIT `LICENSE`; candidate-specific prompt content removed (writer
  archetypes, interview coach, dateline city now from `## Kontakt`); job market
  configurable via `config.yaml` `market:`; interview agent redacts contact PII too.
- PDF layout: cover letter on DIN 5008 Form B coordinates; automatic one-page fitting;
  no page breaks inside paragraphs/items; `scripts/check_layout.py` worst-case check.
- README: step-by-step onboarding (prerequisites, API key, profile, assets, first run).

**Verify:** `git ls-files | grep profile` shows only `profile.example.md`; a fresh clone
using `profile.example.md` runs.

## Phase 4.5 — Direct / single-job apply mode *(near-term, high value)*

**Goal:** Apply to a posting the user already found, without running discovery/scoring.

- New entry path: `python run.py --url <job_url>` and `--paste` (JD from stdin/file for
  paywalled portals).
- Reuse `agents/web_research.py` to fetch the page; one Gemini call parses raw JD →
  `JobListing`. Match-scoring is skipped (the user already chose to apply).
- Hand straight to the existing per-job path (`run.py` `_process_job`): research →
  generate → HR loop → preview → render → track. No new generation logic.

**Verify:** `python run.py --url <real posting>` produces a tracked application with
rendered CV/CL PDFs, equal in quality to the discovery path.

## Phase 5 — Privacy / PII redaction layer *(near-term, quick win)*

**Goal:** No contact PII ever leaves the machine via an LLM prompt.

- Add a redaction helper (`agents/privacy.py` or in `agents/utils.py`) that strips the
  `## Kontakt` block (and inline address/phone/email/DOB) from `profile_text` before
  writer prompts.
- Apply in `writer.py` where `self.profile_text` enters the CV and CL prompts. Leave the
  `latex_exporter` contact path untouched (already local).

**Verify:** unit test asserts the redacted prompt contains none of the address, phone,
email, or DOB; an `--auto` run still renders the full letterhead in the PDF.

## Phase 6 — Profile schema restructure *(single source of truth, multi-user)*

**Goal:** One parseable profile the whole pipeline derives from; kill the
hardcoded-summary drift.

- Restructure into **facts header** (machine-parsed: contact, target_roles, location,
  salary, languages, availability) + **prose body** (the narrative the writer consumes).
- Replace the hardcoded `_compress_profile` in `job_searcher.py` with a function that
  **derives** the scoring summary from the profile. Extend `parse_kontakt`
  (`agents/latex_utils.py`) into a general front-matter parser.
- Writer consumes the **redacted prose body** (Phase 5 invariant holds).
- Keep `profile.example.md` in lockstep with the schema.

**Verify:** editing a profile field changes both scorer and writer behavior (no stale
values); letterhead still renders.

## Phase 7 — Quality depth *(the "deepen logic" core)* — DONE

**Goal:** Draft quality is the gap between "good script" and "S-tier." Shipped:

- **7a — de-hardcoded HR reviewer:** calibration derives from the profile
  (`candidate_context` ← `## Eckdaten`) instead of a fixed persona. Prompt builder extracted + unit-tested.
- **7b — per-task model tiering:** config `models:` (bulk / writing / review); each agent
  takes a `model` param. Defaults stay on flash-lite (no cost change until configured).
- **7c — configurable HR calibration:** `hr_review:` (`pass_score`, `max_loops`) instead
  of hardcoded `>=7` / 2 loops.
- **7d — CL grounding + de-drift:** cover letter must ground company hooks in
  research/posting (no invented company facts); experience paragraph names projects from
  the profile generically (no candidate-specific names).

**Deferred from here →** the CV **generation** prompt is still candidate-specific (fixed
skill categories, project pick-list, two named employers). Generalizing it is a larger
refactor; tracked as Phase 11.

## Phase 11 — Multi-candidate CV generation — DONE

**Goal:** Make CV generation profile-driven for any candidate, not just the original author. Shipped:

- `agents/profile.py` `parse_experiences` reads `## Berufserfahrung` into structured
  entries (company/role/location/dates/bullets).
- Writer passes an **indexed** experience list to the model, which selects the relevant
  entries per job and tailors bullets; `_assemble_experiences` recombines them with the
  profile's company/role/dates (never invented) → `ApplicationDraft.experiences`.
- CV prompt de-hardcoded: KENNTNISSE / PROJEKTE / AUSBILDUNG now derive from the profile
  (no fixed skill labels, project pick-list, or named employers).
- `latex_exporter` passes `experiences`; `templates/cv/*.tex` **loop** over N entries
  (the two fixed employer boxes are gone).
- Assets onboarding: photo/signature render only `if has_photo`/`if has_signature`, so a
  fresh clone without them still renders.

**Verified:** unit tests (`tests/test_experiences.py`) + a live run — the model picked
the two relevant employers, skipped an unrelated one, pulled exact company/dates from the
profile, rendered the portrait; CV quality unchanged.

## Phase 8 — Run performance + timing instrumentation — DONE

**Goal:** Every run is timed and reported; the pipeline can be optimized over time.

- `agents/timing.py` `StageTimer` (context-manager, accumulates per label) +
  `human_duration`. Stages wrapped in `run.py`: discovery (and fetch/parse for direct
  apply), per-job generate / hr_review / render. User-preview think-time is excluded.
- End-of-run **"N application(s) in M"** banner + per-stage breakdown (slowest flagged).
- `_append_run_log` persists each run to `run_log.json` (gitignored): timestamp, mode,
  jobs processed, accepted, total + per-stage seconds, cost, api calls.

**Verify:** `python run.py --auto` prints the banner with correct counts + elapsed time
and appends an entry to `run_log.json`. Unit tests in `tests/test_timing.py`.

## Phase 9 — Metrics on JSON — DONE

**Goal:** Funnel visibility without a DB.

- `metrics.py` `compute_metrics()` (pure) over tracked applications: funnel
  (applied → responded → interview → offer), response/interview/offer rates,
  by-status / by-language / **by-archetype / by-board** splits, rejected/ghosted counts.
- `TrackedApplication` now stores `archetype` + `board` (back-compatible: legacy records
  default to empty and are omitted from those breakdowns).
- Surfaced as `run_tracker.py` menu item **[6] Metrics**, rendered with rich tables.
- Tests in `tests/test_metrics.py`.

**Note:** by-tone and time-to-response still need extra fields/event timestamps on
`TrackedApplication` — left for a future increment.

## CLI facelift (rich) — DONE *(alongside Phases 8–9)*

- `ui.py`: shared rich `Console` + helpers (tables, panels, rules, spinners, score/status
  styles). Added `rich` to `requirements.txt`.
- Spinners on the long waits (scrape+score, fetch, parse, generate, hr_review, render).
- Rich tables for the job-selection list, run performance banner, tracker overview,
  deadlines, and metrics. Full live multi-pane TUI intentionally skipped (conflicts with
  the blocking `input()` flow).

## Phase 10 — Parked / future *(documented, not built)*

- Local / hybrid models via HuggingFace (route bulk → local GGUF; cloud for writing).
- Web-form answer bank (reusable per-job portal answers in the tracker).
- Private app (iOS or web) once the core is S-tier.

## Pipeline hardening — DONE *(2026-08)*

**Goal:** stop the pipeline producing confident fiction, and match the real workflow
(paste several links, review the finished `.tex`, no per-draft prompts).

Root causes found and fixed:

- **Fabricated postings.** Direct apply only checked that a fetch returned >200 chars, so a
  cookie banner or JS shell passed and the model reconstructed a plausible posting from the URL
  slug — one real run placed an Austrian asset manager in Berlin and tailored the whole
  application to it. `parse_posting` now classifies first and raises `NotAJobPostingError`,
  offering the `--paste` fallback.
- **Fabricated company facts.** `_research_company` fell back to model priors whenever web
  research came up empty (common — the search skips the big job boards). Fallback removed:
  no web content means no research and no model call.
- **A blind quality gate.** The reviewer never saw the profile, so it could not detect an
  invented employer or metric. It now gets the redacted profile as ground truth and runs a
  fabrication check that caps affected scores at 3.
- **Mixed-up multi-job runs.** Not context bleed — every Gemini call is stateless. Pasting
  several JDs merged them into one blob that `parse_posting` truncated at 6000 chars. `run_urls`
  processes each URL in isolation; unreadable postings are skipped without stopping the batch.
- **A self-grading reviewer.** All three tiers ran on `gemini-2.5-flash-lite`. Writer and
  reviewer are now deliberately different GA models, with the reviewer the stronger reasoner.
- **A wasteful revision loop.** Each rewrite re-ran web research and regenerated from scratch,
  discarding sections that had already scored well. `generate()` accepts cached research and the
  previous draft; the writer also sees the reviewer's rubric up front.

**Workflow changes:** no per-draft preview — everything renders to `.tex`/PDF and the summary
reports HR scores, flagging drafts below the pass bar. `cv_draft.md`/`cl_draft.md` are no longer
written (nothing read them); `hr_review.json` holds the final verdict only.

**Verify:** `pytest` (103 tests) + `python scripts/smoke_latex.py`. A run against a blocked
portal reports "No readable job posting found" instead of producing an application.
