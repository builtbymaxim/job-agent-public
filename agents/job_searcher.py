"""Job discovery agent — scrapes platforms via JobSpy, scores against profile with Claude."""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
from google import genai
from google.genai import types
from jobspy import scrape_jobs

from agents.profile import build_scoring_summary
from agents.utils import generate_with_retry

from models import JobListing, JobLanguage

logger = logging.getLogger(__name__)

MODEL = "gemini-3.5-flash-lite"
SEEN_JOBS_PATH = Path(__file__).resolve().parent.parent / "seen_jobs.json"


class NotAJobPostingError(RuntimeError):
    """The supplied text is not a readable job posting (cookie banner, login wall,
    empty JS shell). Raised instead of letting the model invent a plausible one."""


class JobSearcher:
    def __init__(self, config: dict, profile_text: str, client: genai.Client, model: str = MODEL):
        self.config = config["search"]
        self.profile_text = profile_text
        self.client = client
        self.model = model
        self.seen_urls: dict[str, str] = self._load_seen()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def search_all(self) -> list[JobListing]:
        """Scrape all platforms, deduplicate, filter seen, score with Claude, sort."""
        raw_jobs: list[dict] = []
        self.last_stats = {"found": 0, "new": 0, "matched": 0}

        for platform in self.config["platforms"]:
            logger.info(f"Scraping {platform}...")
            try:
                df = self._scrape_platform(platform)
                jobs = self._dataframe_to_dicts(df, platform)
                logger.info(f"  Found {len(jobs)} jobs on {platform}")
                raw_jobs.extend(jobs)
            except Exception as e:
                logger.error(f"  Failed to scrape {platform}: {e}")
                continue

        # Deduplicate and filter seen BEFORE scoring (saves API tokens)
        raw_jobs = self._deduplicate_raw(raw_jobs)
        self.last_stats["found"] = len(raw_jobs)
        raw_jobs = self._filter_seen_raw(raw_jobs)
        self.last_stats["new"] = len(raw_jobs)

        if not raw_jobs:
            logger.info("No new jobs found after filtering.")
            return []

        # Score all jobs in a single Claude call
        logger.info(f"Scoring {len(raw_jobs)} jobs against profile...")
        scored_jobs = self._batch_score(raw_jobs)

        # Filter and sort
        min_score = self.config.get("min_match_score", 0.60)
        scored_jobs = [j for j in scored_jobs if j.match_score >= min_score]
        scored_jobs.sort(key=lambda j: j.match_score, reverse=True)
        scored_jobs = scored_jobs[: self.config.get("max_jobs", 20)]

        self._save_seen()
        logger.info(f"Total after filtering: {len(scored_jobs)} jobs")
        return scored_jobs

    def save_results(self, jobs: list[JobListing], output_dir: str) -> str:
        """Save job list to JSON file in output_dir."""
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)
        filename = path / f"jobs_{date.today().isoformat()}.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump([j.to_dict() for j in jobs], f, ensure_ascii=False, indent=2)
        logger.info(f"Saved {len(jobs)} jobs to {filename}")
        return str(filename)

    def parse_posting(self, raw_text: str, url: str = "") -> JobListing:
        """Parse one raw job-posting (URL text or pasted JD) into a JobListing.

        Direct-apply path: the user already chose this job, so there is no
        match-scoring — score is fixed to 1.0 and legitimacy to high_confidence.

        Raises `NotAJobPostingError` if the text is not a readable posting (cookie
        banner, login wall, JS shell). Fetched pages routinely clear a naive length
        check while containing no posting at all, and the model will happily invent
        a plausible one from the URL slug — so the model must positively confirm a
        posting is present before any field is trusted.
        """
        # Job boards prepend a few thousand characters of cookie-consent text, so a
        # tight window can cut into the posting itself: on LinkedIn pages the salary,
        # contact and employment type routinely sat past a 6000-char cut. Context is
        # cheap at this tier; losing the terms of the job is not.
        excerpt = raw_text.strip()[:16000]
        system_prompt = """Extract structured fields from this single job posting.

The text is a scraped web page. Ignore cookie banners, navigation, login prompts, and —
importantly — any RELATED, RECOMMENDED or "similar jobs" listings elsewhere on the page.
Extract ONLY the one posting the page is about. Never merge details from several postings.

FIRST decide whether the text actually IS a job posting. Cookie banners, consent
dialogs, login/paywall pages, search-result lists, error pages, and near-empty
JavaScript shells are NOT job postings. Neither is a page that merely mentions a
company or a job title without describing an actual role.

If it is NOT a single readable job posting, return exactly:
{"is_posting": false, "reason": "<short reason>"}

Never guess or reconstruct a posting from a URL, a page title, or general knowledge
about the company. Absent a real posting in the text, return is_posting false.

If it IS a job posting, return ONLY a JSON object:
{"is_posting": true, "title": "...", "company": "...", "location": "...",
 "employment_type": "...", "language": "de" or "en", "requirements": ["...", ...],
 "archetype": "data_analyst"|"bi_developer"|"risk_analyst"|"data_science_ml"|"business_analyst"|"general",
 "posted_date": "YYYY-MM-DD or empty string if not stated",
 "description": "<= 600 char clean summary of the role"}

- Every field must come from the text. Leave a field empty rather than inventing it.
- title: the ROLE title only, as it would read in a letter's subject line. Strip any company
  name, location, or board suffix the page title appends (e.g. "AI Associate (all genders) -
  ACME Berlin, Germany" -> "AI Associate (all genders)").
- location: the work location stated in the posting. Empty string if not stated.
- language: the language the posting is written in.
- requirements: 3-8 key requirements pulled from the text.
- archetype: the single best-fit role category.
- posted_date: the publish date if the posting states one, else "".
No text outside the JSON object."""

        response = generate_with_retry(
            self.client,
            self.model,
            contents=f"Job posting:\n{excerpt}",
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=4096,  # includes Gemini 3.x thinking tokens
                response_mime_type="application/json",
            ),
        )
        return self._parse_posting_response(response.text or "", raw_text, url)

    def _parse_posting_response(self, text: str, raw_text: str, url: str) -> JobListing:
        """Build a JobListing from the single-posting extraction response.

        Raises `NotAJobPostingError` when the model reports no posting, when the
        response is unparseable, or when the fields it returned are too thin to be
        a real extraction — an application built on invented facts is worse than
        no application.
        """
        _VALID_ARCHETYPES = frozenset(
            ("data_analyst", "bi_developer", "risk_analyst", "data_science_ml", "business_analyst", "general")
        )
        obj = self._extract_json_object(text)
        if obj is None:
            raise NotAJobPostingError(
                "Could not parse the posting — the page may not contain a job description."
            )

        if obj.get("is_posting") is False:
            raise NotAJobPostingError(obj.get("reason") or "No job posting found in the text.")

        # A posting the model could not name is not a posting it actually read.
        title, company = (obj.get("title") or "").strip(), (obj.get("company") or "").strip()
        if not title or not company:
            raise NotAJobPostingError(
                "Extraction returned no job title or company — the text is likely not a posting."
            )

        lang = obj.get("language", "de")
        if lang not in ("de", "en"):
            lang = "de"
        archetype = obj.get("archetype", "general")
        if archetype not in _VALID_ARCHETYPES:
            archetype = "general"

        return JobListing(
            title=title,
            company=company,
            location=obj.get("location", ""),
            platform="direct",
            url=url,
            description=obj.get("description") or raw_text.strip()[:600],
            requirements=list(obj.get("requirements", []))[:8],
            match_score=1.0,
            match_reasoning="Direct apply — user-selected posting",
            language=JobLanguage(lang),
            employment_type=obj.get("employment_type") or "",
            posted_date=obj.get("posted_date") or "",
            archetype=archetype,
            legitimacy_tier="high_confidence",
            legitimacy_signals=["user-selected"],
        )

    # ------------------------------------------------------------------
    # Private — scraping
    # ------------------------------------------------------------------

    def _scrape_platform(self, platform: str) -> pd.DataFrame:
        """Scrape a single platform using JobSpy, across keyword groups."""
        keyword_groups = self.config.get(
            "keyword_groups",
            ["Data Analyst, Business Intelligence, Reporting"],
        )
        location = self.config.get("location", "Wien, Austria")
        results_wanted = self.config.get("results_wanted", 15)
        hours_old = self.config.get("hours_old", 168)

        frames: list[pd.DataFrame] = []
        for keywords in keyword_groups:
            kwargs = {
                "site_name": [platform],
                "search_term": keywords,
                "location": location,
                "results_wanted": results_wanted,
                "hours_old": hours_old,
            }
            if platform == "indeed":
                kwargs["country_indeed"] = self.config.get("country_indeed", "Austria")

            try:
                df = scrape_jobs(**kwargs)
                if df is not None and not df.empty:
                    frames.append(df)
                    logger.info(f"    {platform} [{keywords[:40]}]: {len(df)} results")
            except Exception as e:
                logger.warning(f"    {platform} [{keywords[:40]}] failed: {e}")
                continue

        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    def _dataframe_to_dicts(self, df: pd.DataFrame, platform: str) -> list[dict]:
        """Convert JobSpy DataFrame rows to intermediate dicts."""
        if df.empty:
            return []

        jobs = []
        for _, row in df.iterrows():
            title = str(row.get("title", "")) if pd.notna(row.get("title")) else ""
            company = (
                str(row.get("company", "")) if pd.notna(row.get("company")) else ""
            )
            if not title or not company:
                continue

            location = (
                str(row.get("location", "")) if pd.notna(row.get("location")) else ""
            )
            url = (
                str(row.get("job_url", "")) if pd.notna(row.get("job_url")) else ""
            )
            description = (
                str(row.get("description", ""))[:2000]
                if pd.notna(row.get("description"))
                else ""
            )
            job_type = (
                str(row.get("job_type", "")) if pd.notna(row.get("job_type")) else ""
            )
            posted = (
                str(row.get("date_posted", ""))
                if pd.notna(row.get("date_posted"))
                else ""
            )

            jobs.append(
                {
                    "title": title,
                    "company": company,
                    "location": location,
                    "platform": platform,
                    "url": url,
                    "description": description,
                    "employment_type": job_type,
                    "posted_date": posted,
                }
            )
        return jobs

    # ------------------------------------------------------------------
    # Private — scoring
    # ------------------------------------------------------------------

    def _batch_score(self, raw_jobs: list[dict]) -> list[JobListing]:
        """Score all jobs in a single Claude Haiku call."""
        profile_summary = self._compress_profile()

        # Build compact job list for prompt
        jobs_text = ""
        for i, job in enumerate(raw_jobs):
            desc_excerpt = job["description"][:500].replace("\n", " ")
            jobs_text += (
                f"\n--- JOB {i} ---\n"
                f"Title: {job['title']}\n"
                f"Company: {job['company']}\n"
                f"Location: {job['location']}\n"
                f"Type: {job['employment_type']}\n"
                f"Description: {desc_excerpt}\n"
            )

        system_prompt = f"""Score each job 0.0-1.0 against this candidate:

{profile_summary}

Scoring rubric (average these 5 dimensions), judged against the candidate summary above:
1. Skill match (0.0-1.0)
2. Experience level fit (0.0-1.0)
3. Industry relevance to the candidate's target domains (0.0-1.0)
4. Location match against the candidate's location (0.0-1.0)
5. Language fit against the candidate's languages (0.0-1.0)

For each job, also:
- Extract 3-5 key requirements from the description
- Detect language: "de" or "en"
- Provide brief match_reasoning (1 sentence)
- Classify role archetype as exactly one of: "data_analyst", "bi_developer", "risk_analyst",
  "data_science_ml", "business_analyst", "general".
  data_analyst: operational reporting, dashboards, KPIs, SQL, Power BI.
  bi_developer: data modeling, ETL pipelines, BI architecture.
  risk_analyst: credit/market/retail risk, statistical modeling, regulatory reporting.
  data_science_ml: ML models, NLP, Python/sklearn, experimentation.
  business_analyst: requirements, process, stakeholder, PM tools.
  general: if none of the above fits clearly.
- Assess posting legitimacy. Return {{"tier": "<tier>", "signals": ["<reason>", ...]}}.
  Tier "high_confidence": specific role, realistic requirements, clear team context.
  Tier "caution": vague JD, inflated requirements (10+ must-haves), generic language
    ("we are always looking for"), missing company context.
  Tier "suspicious": ghost-job pattern — no specific team, no real project mentioned,
    contradictory or absent location/salary with suspicious phrasing.
  signals: 1-3 brief phrases explaining the tier.

Return ONLY a JSON array:
[{{"index": 0, "match_score": 0.75, "match_reasoning": "...", "requirements": ["..."],
  "language": "de", "archetype": "data_analyst",
  "legitimacy": {{"tier": "high_confidence", "signals": ["specific team context"]}}
}}, ...]

No text outside the JSON array."""

        response = generate_with_retry(
            self.client,
            self.model,
            contents=f"Score these {len(raw_jobs)} jobs:\n{jobs_text}",
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=24576,  # includes Gemini 3.x thinking tokens
                response_mime_type="application/json",
            ),
        )

        return self._parse_scores(response, raw_jobs)

    def _parse_scores(self, response, raw_jobs: list[dict]) -> list[JobListing]:
        """Parse scoring response and merge with raw job data."""
        text = response.text or ""

        json_str = self._extract_json_array(text)
        if not json_str:
            logger.warning(
                "Failed to parse scoring response, assigning default scores. "
                "Response head: %r tail: %r len=%d",
                text[:200], text[-200:], len(text),
            )
            return self._fallback_jobs(raw_jobs)

        try:
            scores = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning(
                "JSON decode failed for scoring response: %s. "
                "json_str head: %r tail: %r len=%d",
                e, json_str[:200], json_str[-200:], len(json_str),
            )
            return self._fallback_jobs(raw_jobs)

        _VALID_ARCHETYPES = frozenset(
            ("data_analyst", "bi_developer", "risk_analyst", "data_science_ml", "business_analyst", "general")
        )
        _VALID_TIERS = frozenset(("high_confidence", "caution", "suspicious"))
        _TIER_DOWNGRADE = {"high_confidence": "caution", "caution": "suspicious", "suspicious": "suspicious"}

        jobs = []
        for score_entry in scores:
            idx = score_entry.get("index", -1)
            if idx < 0 or idx >= len(raw_jobs):
                continue

            raw = raw_jobs[idx]
            try:
                lang = score_entry.get("language", "de")
                if lang not in ("de", "en"):
                    lang = "de"

                archetype = score_entry.get("archetype", "general")
                if archetype not in _VALID_ARCHETYPES:
                    archetype = "general"

                legitimacy = score_entry.get("legitimacy", {})
                legitimacy_tier = legitimacy.get("tier", "high_confidence")
                if legitimacy_tier not in _VALID_TIERS:
                    legitimacy_tier = "high_confidence"
                legitimacy_signals = list(legitimacy.get("signals", []))[:3]

                # Downgrade tier if posting is older than 30 days
                posted = raw.get("date_posted") or raw.get("posted_date", "")
                if posted:
                    try:
                        from datetime import date as _date, datetime as _dt
                        pd = _dt.fromisoformat(str(posted)).date() if "T" in str(posted) else _date.fromisoformat(str(posted)[:10])
                        if (_date.today() - pd).days > 30:
                            legitimacy_tier = _TIER_DOWNGRADE[legitimacy_tier]
                            legitimacy_signals = ["posting >30 days old"] + legitimacy_signals
                    except (ValueError, TypeError):
                        pass

                job = JobListing(
                    title=raw["title"],
                    company=raw["company"],
                    location=raw["location"],
                    platform=raw["platform"],
                    url=raw["url"],
                    description=raw["description"],
                    requirements=score_entry.get("requirements", []),
                    match_score=float(score_entry.get("match_score", 0.5)),
                    match_reasoning=score_entry.get("match_reasoning", ""),
                    language=JobLanguage(lang),
                    employment_type=raw.get("employment_type", ""),
                    posted_date=raw.get("posted_date", str(posted)),
                    archetype=archetype,
                    legitimacy_tier=legitimacy_tier,
                    legitimacy_signals=legitimacy_signals,
                )
                jobs.append(job)
            except (ValueError, KeyError) as e:
                logger.warning(f"Skipping job {idx}: {e}")

        return jobs

    def _fallback_jobs(self, raw_jobs: list[dict]) -> list[JobListing]:
        """Assign default score of 0.5 to all jobs if scoring fails."""
        jobs = []
        for raw in raw_jobs:
            jobs.append(
                JobListing(
                    title=raw["title"],
                    company=raw["company"],
                    location=raw["location"],
                    platform=raw["platform"],
                    url=raw["url"],
                    description=raw["description"],
                    requirements=[],
                    match_score=0.5,
                    match_reasoning="Scoring unavailable — review manually",
                    language=JobLanguage.DE,
                    employment_type=raw.get("employment_type", ""),
                    posted_date=raw.get("posted_date", ""),
                )
            )
        return jobs

    # ------------------------------------------------------------------
    # Private — JSON parsing
    # ------------------------------------------------------------------

    def _extract_json_array(self, text: str) -> str | None:
        """Extract the first JSON array from text, handling markdown code blocks."""
        code_block = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
        if code_block:
            return code_block.group(1)

        start = text.find("[")
        if start == -1:
            return None

        depth = 0
        for i in range(start, len(text)):
            if text[i] == "[":
                depth += 1
            elif text[i] == "]":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        return None

    def _extract_json_object(self, text: str) -> dict | None:
        """Extract the first JSON object from text, handling markdown code blocks."""
        code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        candidate = code_block.group(1) if code_block else None

        if candidate is None:
            start = text.find("{")
            if start == -1:
                return None
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start : i + 1]
                        break

        if not candidate:
            return None
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None

    # ------------------------------------------------------------------
    # Private — deduplication & seen tracking
    # ------------------------------------------------------------------

    def _deduplicate_raw(self, jobs: list[dict]) -> list[dict]:
        """Remove duplicate raw jobs by normalized URL."""
        seen: dict[str, dict] = {}
        for job in jobs:
            key = self._normalize_url(job.get("url", ""))
            if key and key not in seen:
                seen[key] = job
        return list(seen.values())

    def _filter_seen_raw(self, jobs: list[dict]) -> list[dict]:
        """Remove jobs already seen in previous runs."""
        new_jobs = []
        today = date.today().isoformat()
        for job in jobs:
            key = self._normalize_url(job.get("url", ""))
            if key and key not in self.seen_urls:
                self.seen_urls[key] = today
                new_jobs.append(job)
        return new_jobs

    def _normalize_url(self, url: str) -> str:
        if not url:
            return ""
        parsed = urlparse(url)
        # Indeed uses query params (jk=...) as unique ID, keep them
        if "indeed" in parsed.netloc and parsed.query:
            return f"{parsed.netloc}{parsed.path.rstrip('/')}?{parsed.query}"
        return f"{parsed.netloc}{parsed.path.rstrip('/')}"

    def _load_seen(self) -> dict[str, str]:
        if SEEN_JOBS_PATH.exists():
            with open(SEEN_JOBS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("urls", {})
        return {}

    def _save_seen(self) -> None:
        with open(SEEN_JOBS_PATH, "w", encoding="utf-8") as f:
            json.dump({"urls": self.seen_urls}, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # Private — profile
    # ------------------------------------------------------------------

    def _compress_profile(self) -> str:
        """Derive the scoring summary from the profile (single source of truth).

        No hardcoded duplicate — edits to the profile flow straight into scoring.
        """
        return build_scoring_summary(self.profile_text)
