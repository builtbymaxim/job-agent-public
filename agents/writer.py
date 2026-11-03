"""Content generation agent — researches companies, generates tailored CV sections and cover letters."""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path

from google import genai
from google.genai import types

from agents.privacy import redact_profile
from agents.profile import parse_experiences
from agents.style_filter import find_violations_in_dict, sanitize_sections, sanitize_text
from agents.utils import generate_with_retry
from agents.web_research import research_company_web
from models import JobListing, ApplicationDraft, HRReview, JobLanguage

logger = logging.getLogger(__name__)

MODEL = "gemini-3.6-flash"

# Strategy per role family. Deliberately candidate-agnostic: the model picks the
# proof points from the profile; nothing here names a specific employer or project.
ARCHETYPE_INSTRUCTIONS: dict[str, str] = {
    "data_analyst": (
        "ROLE ARCHETYPE — DATA ANALYST:\n"
        "- Primary proof point: the profile entry where analysis or reporting was actually USED "
        "(a dashboard, report or KPI that people relied on). Foreground tools, data sources and adoption.\n"
        "- KENNTNISSE ordering: BI & Reporting tools first, then data/SQL, then Python/analytics.\n"
        "- PROJEKTE: pick the projects most relevant to data pipelines and dashboard-driven insights.\n"
        "- CL framing: decision-relevant reporting, translating raw data into operational KPIs."
    ),
    "bi_developer": (
        "ROLE ARCHETYPE — BI DEVELOPER:\n"
        "- Primary proof point: the profile entry with the strongest data-modelling or BI-architecture "
        "work. Emphasise structures, consistency and design decisions, not just the report on top.\n"
        "- KENNTNISSE ordering: data modeling, ETL concepts, BI tools, SQL/databases first.\n"
        "- PROJEKTE: emphasise the data-pipeline and backend architecture aspects of your projects.\n"
        "- CL framing: reproducible data structures, scalable BI architecture."
    ),
    "risk_analyst": (
        "ROLE ARCHETYPE — RISK / CREDIT ANALYST:\n"
        "- Primary proof point: the most quantitative work in the profile (thesis, research, "
        "risk or statistical modelling). Emphasise validation, statistical rigour and any "
        "compliance/regulatory angle the profile actually supports.\n"
        "- KENNTNISSE ordering: statistical analysis and Python analytics first, then BI tools.\n"
        "- CL framing: bridging quantitative modeling with regulatory awareness."
    ),
    "data_science_ml": (
        "ROLE ARCHETYPE — DATA SCIENCE / ML:\n"
        "- Primary proof point: the profile's strongest ML work (thesis, research or project). "
        "Foreground pipeline design, evaluation methodology and empirical validation.\n"
        "- Secondary: further projects that show models reaching use or production.\n"
        "- KENNTNISSE ordering: Python ML stack (pandas, scikit-learn, NLP) first.\n"
        "- CL framing: end-to-end ML practitioner — research to production."
    ),
    "business_analyst": (
        "ROLE ARCHETYPE — BUSINESS / FUNCTIONAL ANALYST:\n"
        "- Primary proof point: the profile entry with the most direct stakeholder work "
        "(requirements, deliverables for decision-makers, cross-functional coordination).\n"
        "- Secondary: any KPI definition or alignment work with management.\n"
        "- KENNTNISSE ordering: stakeholder management, requirements analysis, process tools first.\n"
        "- CL framing: translating business needs into data/process solutions; structured thinking."
    ),
    "general": "",
}


# The writer used to work blind to how it would be judged, so it optimised for
# fluency and got sent back for missing specifics. Stating the reviewer's rubric
# up front lets the first draft aim at the actual bar instead of discovering it
# one rewrite loop later. Kept in sync with hr_agent._system_prompt by hand.
_CV_REVIEW_BAR = """HOW THIS WILL BE JUDGED — a domain reviewer scores the CV 0-10 and rejects below 7:
- Relevance (0-3): do the highlighted skills and projects match what THIS posting asks for?
- Presentation (0-2): structured, concise, error-free.
- Specificity (0-3): concrete tools, scope, outcomes. Vague competence claims score ZERO here.
- Language (0-2): grammar, register, no filler.
The reviewer also checks every claim against the candidate profile. One invented employer,
date, project, or metric caps the score at 3 — accuracy outranks impressiveness.
Write the draft that already clears this bar; do not rely on a later revision round."""

_CL_REVIEW_BAR = """HOW THIS WILL BE JUDGED — a domain reviewer scores the letter 0-10 and rejects below 7:
- Company-specific substance (0-3): a letter that would work for any employer in the industry scores ZERO here.
- Believable motivation (0-2): generic enthusiasm scores ZERO.
- Relevant experience, front-loaded (0-3): most relevant proof first, tied to the posting's requirements.
- Tone (0-2): confident, not arrogant; right register for this company.
The reviewer checks every claim against the candidate profile and every company fact against the
posting or research. One invented fact caps the score at 3.
Write the draft that already clears this bar; do not rely on a later revision round."""


def _research_block(research: str) -> str:
    """Render the company-research section of a prompt.

    An empty section reads as an omission the model is free to fill in; saying
    plainly that nothing was verified, and what to do instead, does not.
    """
    if research.strip():
        return research
    return (
        "(none — no verified web information about this company was found)\n"
        "Do NOT describe the company beyond what the job posting itself states. "
        "Build any hook from a concrete detail in the posting instead."
    )


class ApplicationWriter:
    def __init__(self, config: dict, profile_text: str, client: genai.Client, model: str = MODEL):
        self.config = config
        # Privacy invariant: contact PII renders locally (letterhead) only and must
        # never reach the model. Redact it once at the writer boundary.
        self.profile_text = redact_profile(profile_text)
        self.client = client
        self.model = model
        self.market = config.get("market") or "European"
        # Structured work experience (company/role/dates from the profile, never invented).
        self.experiences = parse_experiences(self.profile_text)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def generate(
        self,
        job: JobListing,
        feedback: HRReview | None = None,
        user_comment: str = "",
        research: str | None = None,
        previous: ApplicationDraft | None = None,
    ) -> ApplicationDraft:
        """Generate a complete application draft for a job listing.

        `research` lets the caller reuse an earlier company summary instead of
        re-fetching it: the review loop regenerates the draft, and repeating the
        web research each round costs calls and re-rolls the dice on grounding.

        `previous` supplies the draft being revised, so the model improves the
        text it already has rather than starting over and discarding whatever
        already scored well.
        """
        logger.info(f"Generating application for {job.company} — {job.title}")

        if research is None:
            research = self._research_company(job)

        feedback_text = ""
        if feedback:
            feedback_text = (
                "REVISION — a reviewer scored the previous draft and it did not pass.\n"
                f"CV scored {feedback.cv_score}/10: {feedback.cv_feedback}\n"
                f"Cover letter scored {feedback.cl_score}/10: {feedback.cl_feedback}\n"
                "Fix exactly what the feedback names. Keep every part it did not criticise —"
                " rewriting strong sections risks losing what already worked."
            )
            if previous:
                feedback_text += (
                    "\n\nPREVIOUS DRAFT — revise THIS text:\n"
                    f"CV sections:\n{json.dumps(previous.cv_sections, ensure_ascii=False, indent=2)}\n"
                    f"Cover letter:\n{json.dumps(previous.cover_letter, ensure_ascii=False, indent=2)}"
                )
        if user_comment:
            feedback_text += f"\nUser comment: {user_comment}"

        research_block = _research_block(research)
        cv_sections, experiences = self._generate_cv_sections(job, research_block, feedback_text)
        cover_letter = self._generate_cover_letter(job, research_block, cv_sections, feedback_text)

        return ApplicationDraft(
            job=job,
            cv_sections=cv_sections,
            cover_letter=cover_letter,
            company_research=research,
            language=job.language,
            experiences=experiences,
        )

    def save_posting(self, draft: ApplicationDraft, output_dir: str) -> str:
        """Save the source posting and company research alongside the application.

        The rendered .tex is the artefact under review, so intermediate cv_draft.md
        and cl_draft.md are no longer written — nothing read them back, and they
        only duplicated the .tex in a weaker form. The posting and research stay:
        they are the inputs, and they are what you check a suspect draft against.
        """
        folder = Path(output_dir) / draft.job.folder_name
        folder.mkdir(parents=True, exist_ok=True)

        # Job posting
        posting_path = folder / "job_posting.md"
        posting_content = (
            f"# {draft.job.title} — {draft.job.company}\n\n"
            f"**Platform:** {draft.job.platform}\n"
            f"**URL:** {draft.job.url}\n"
            f"**Posted:** {draft.job.posted_date or '—'}\n"
            f"**Location:** {draft.job.location}\n"
            f"**Type:** {draft.job.employment_type}\n"
            f"**Match Score:** {draft.job.match_score:.2f}\n\n"
            f"## Description\n{draft.job.description}\n\n"
            f"## Requirements\n"
            + "\n".join(f"- {r}" for r in draft.job.requirements)
        )
        posting_path.write_text(posting_content, encoding="utf-8")

        # Company research
        research_path = folder / "company_research.md"
        research_path.write_text(
            f"# Company Research: {draft.job.company}\n\n{draft.company_research}",
            encoding="utf-8",
        )

        logger.info(f"Saved posting + research to {folder}")
        return str(folder)

    # ------------------------------------------------------------------
    # Private — company research
    # ------------------------------------------------------------------

    def research_company(self, job: JobListing) -> str:
        """Public entry for callers that run research as its own step (see run.py)."""
        return self._research_company(job)

    def _research_company(self, job: JobListing) -> str:
        """Summarise the company from fetched web content, or return "" if there is none.

        There is deliberately no model-prior fallback. Most postings are from small
        firms the model knows nothing about, and asked to summarise one anyway it
        produces confident fiction — a real run relocated an Austrian asset manager
        to Berlin, and the cover letter was built on that. An empty research string
        makes the cover letter more generic; an invented one makes it wrong.
        """
        web_snippets = research_company_web(job.company, job.location)
        if not web_snippets:
            logger.warning(
                f"No web content for {job.company} — skipping research rather than "
                f"guessing; the cover letter will stay generic about the company."
            )
            return ""

        logger.info(f"Fetched {len(web_snippets)} chars of web content for {job.company}")
        response = generate_with_retry(
            self.client,
            self.model,
            contents=(
                f"Summarise the company '{job.company}'. They are hiring for: {job.title}.\n\n"
                "Base the summary STRICTLY on the web snippets below. Every statement must be "
                "traceable to them. Do not add context from prior knowledge about this company "
                "or its industry. Omit any topic the snippets do not cover — a short summary is "
                "correct, an padded one is not. If the snippets are about a different company, "
                "reply with exactly: NO RELIABLE INFORMATION\n\n"
                f"WEB SNIPPETS:\n{web_snippets}"
            ),
            config=types.GenerateContentConfig(
                system_instruction=(
                    "You are a company research assistant. Cover only what the sources support:\n"
                    "1. What the company does\n2. Culture and values\n3. Size and reputation\n\n"
                    "Keep it factual and concise (80-120 words). Write in the same language as the job posting."
                ),
                max_output_tokens=2500,  # includes Gemini 3.x thinking tokens
            ),
        )

        text = (response.text or "").strip()
        if "NO RELIABLE INFORMATION" in text.upper():
            logger.warning(f"Web content for {job.company} was off-target; discarding research.")
            return ""
        return text

    # ------------------------------------------------------------------
    # Private — CV generation
    # ------------------------------------------------------------------

    def _generate_cv_sections(
        self, job: JobListing, research: str, feedback: str
    ) -> dict[str, str]:
        """Generate tailored CV sections."""
        lang_instruction = (
            "LANGUAGE: Write the entire output in German. If the candidate profile is written in another "
            "language, translate it into natural professional German; keep facts as-is."
            if job.language == JobLanguage.DE
            else (
                "CRITICAL: The job posting is in English. You MUST output ALL JSON values "
                "(including ALL CV sections like PROFIL, KENNTNISSE, PROJEKTE, etc.) strictly in English. "
                "Do not leave a single word of German in the output.\n"
                "LANGUAGE: The candidate profile below may be written in German (or another language), but you MUST "
                "output EVERYTHING in ENGLISH. Translate every non-English phrase, job title, skill, and description "
                "into natural professional English. Do not leave any German words in the output. "
                "Example: 'Werkstudent' → 'Working student'; "
                "'Eigenständiger Aufbau' → 'Independently built'; 'Vertriebsabteilung' → 'Sales department'."
            )
        )

        tone_instruction = self._get_tone_instruction(job, research)
        archetype_instruction = self._get_archetype_instruction(job)

        feedback_block = ""
        if feedback:
            feedback_block = f"""
IMPORTANT — Address this feedback from the HR reviewer:
{feedback}
Improve the sections accordingly. Be specific in your improvements."""

        archetype_block = f"\n{archetype_instruction}\n" if archetype_instruction else ""

        # Indexed work-experience list the model selects from (it tailors bullets only;
        # company/role/dates come from the profile so they can't be invented).
        exp_lines: list[str] = []
        for i, exp in enumerate(self.experiences):
            head = f"[{i}] {exp['company']}"
            if exp.get("role"):
                head += f" — {exp['role']}"
            meta = " · ".join(x for x in (exp.get("location", ""), exp.get("dates", "")) if x)
            if meta:
                head += f" ({meta})"
            exp_lines.append(head)
            exp_lines.extend(f"    - {b}" for b in exp.get("bullets", []))
        experiences_block = "\n".join(exp_lines) if exp_lines else "(no work experience in profile)"

        system_prompt = f"""You are an expert CV writer specializing in the {self.market} job market.
You are adapting a candidate's CV sections for a specific job application.

{lang_instruction}
{tone_instruction}
{archetype_block}
RULES:
- Use ONLY facts from the candidate profile below. NEVER fabricate, invent, or embellish experience, skills, certifications, or achievements that are not explicitly stated in the profile.
- If the job requires a skill not in the profile, do NOT claim the candidate has it. Instead, highlight transferable skills.
- Do NOT invent metrics, numbers, or KPIs that are not in the profile (e.g., do not write "15 dashboards" unless the profile says so).
- Tailor content by reordering, emphasizing, and reframing existing facts — not by adding new ones.
- Keep each section concise and impactful.

STYLE — AVOID AI TELLS:
- BANNED words/phrases (never use): "leverage", "utilize", "cutting-edge", "seasoned", "spearheaded", "synergy", "ecosystem", "holistic", "paradigm", "robust", "scalable", "passionate about", "committed to excellence", "proven track record", "deep understanding", "I thrive", "I am eager", "I am excited", "I am confident", "dynamic environment", "fast-paced", "results-driven", "harnessing the power of", "at the forefront of", "strategic vision", "adept at", "well-versed", "well-positioned"
- Do NOT use em-dashes. Use commas, periods, or semicolons instead.
- Do NOT start 3+ bullet points with the same word pattern.
- Vary sentence length. Mix short declarative sentences with longer ones.
- Be concrete and specific, not abstract. "Built a Power BI dashboard tracking AuM and Netflows" beats "Leveraged BI tools to drive data-driven insights".
- Write like a real person, not a corporate brochure.

ATS OPTIMIZATION:
- Mirror exact keywords and phrases from the job requirements in your text. If the posting says "Power BI", write "Power BI" not "data visualization tools". If it says "Python", write "Python" not "programming languages".
- Spell out acronyms on first use if the job posting does (e.g., "Machine Learning (ML)").

{_CV_REVIEW_BAR}
{feedback_block}

CANDIDATE PROFILE:
{self.profile_text}

JOB POSTING:
Title: {job.title}
Company: {job.company}
Description: {job.description}
Requirements: {', '.join(job.requirements)}

COMPANY RESEARCH:
{research}

WORK EXPERIENCE — select the relevant entries by index (tailor the bullets only; never change company, role, or dates):
{experiences_block}

Generate the CV as a JSON object:

1. "PROFIL": MAX 80 WORDS. 3-4 punchy sentences. Lead with role fit (what you bring to THIS job), end with 2-3 target technologies. Recruiters scan this in 5 seconds — every word earns its place.

2. "KENNTNISSE": MAX 100 WORDS. 4-6 category lines, the category most relevant to THIS job first. Format per line: "Category: tool1, tool2, tool3". Derive the categories and tools ONLY from the candidate profile's skills/technical sections — keep the profile's own category names where it has them, and do NOT invent tools. Do NOT list spoken languages here.

3. "PROJEKTE": MAX 150 WORDS (the CV must stay on one page). 2-3 of the candidate's most relevant projects FROM THE PROFILE, each a single bullet on its own line starting with "- ". Format: "- ProjectName (context): one sentence — what + tech stack + why it matters for THIS role." Only use projects that appear in the profile.

4. "EXPERIENCES": a JSON array. From the WORK EXPERIENCE list above, select the entries relevant to THIS job (usually the 2-3 most recent/relevant; skip clearly irrelevant or minor older roles). For each selected entry return {{"index": <number from the list>, "bullets": "- ...\\n- ..."}} with tailored bullets (3-4 for the most relevant role, fewer for minor ones), each on its own line starting with "- ". You may prefix a bullet with a short category label (e.g. "Enterprise BI:"). Do NOT repeat the company, role, or dates in the bullets — those render separately. Order entries most-recent first.

5. "AUSBILDUNG": MAX 70 WORDS. The candidate's education FROM THE PROFILE, each entry on its own line starting with "- ", most recent first. Put any thesis on its own line so the topic is visible at a glance. Do not invent degrees or schools.

6. "SPRACHEN": MAX 30 WORDS. Spoken languages with proficiency, one per line starting with "- ", from the profile's languages. Do NOT invent levels.

Return ONLY valid JSON with keys: PROFIL, KENNTNISSE, PROJEKTE, EXPERIENCES, AUSBILDUNG, SPRACHEN
No text outside the JSON."""

        response = generate_with_retry(
            self.client,
            self.model,
            contents="Generate the tailored CV as JSON.",
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=8000,  # includes Gemini 3.x thinking tokens
                response_mime_type="application/json",
            ),
        )
        text_keys = ["PROFIL", "KENNTNISSE", "PROJEKTE", "AUSBILDUNG", "SPRACHEN"]
        raw = self._parse_json_object(response.text or "", text_keys + ["EXPERIENCES"])
        sections = sanitize_sections({k: raw.get(k, "") for k in text_keys})
        experiences = self._assemble_experiences(raw.get("EXPERIENCES", []))

        remaining = find_violations_in_dict(sections)
        if remaining:
            logger.info(f"CV: residual style flags after local sanitize: {remaining}")
        return sections, experiences

    def _assemble_experiences(self, selected) -> list[dict]:
        """Combine the model's selected indices + tailored bullets with the profile's
        company/role/dates (taken by index so employers can't be invented). Falls back
        to the two most recent profile experiences if the model returns nothing usable."""
        out: list[dict] = []
        seen: set[int] = set()
        for item in selected if isinstance(selected, list) else []:
            try:
                idx = int(item.get("index"))
            except (TypeError, ValueError, AttributeError):
                continue
            if idx < 0 or idx >= len(self.experiences) or idx in seen:
                continue
            seen.add(idx)
            src = self.experiences[idx]
            out.append({
                "company": src["company"], "role": src["role"],
                "location": src.get("location", ""), "dates": src.get("dates", ""),
                "bullets": sanitize_text(str(item.get("bullets", ""))),
            })
        if not out:
            for src in self.experiences[:2]:
                out.append({
                    "company": src["company"], "role": src["role"],
                    "location": src.get("location", ""), "dates": src.get("dates", ""),
                    "bullets": "\n".join(f"- {b}" for b in src.get("bullets", [])),
                })
        return out

    # ------------------------------------------------------------------
    # Private — cover letter generation
    # ------------------------------------------------------------------

    def _generate_cover_letter(
        self,
        job: JobListing,
        research: str,
        cv_sections: dict[str, str],
        feedback: str,
    ) -> dict[str, str]:
        """Generate a tailored cover letter."""
        lang_instruction = (
            "LANGUAGE: Write the entire cover letter in German. The candidate profile is in German; keep facts as-is."
            if job.language == JobLanguage.DE
            else (
                "LANGUAGE: The candidate profile below is written in GERMAN, but you MUST output EVERYTHING in ENGLISH. "
                "Translate every German phrase, job title, skill, and project description into natural professional English. "
                "Do not leave any German words in the output."
            )
        )

        tone_instruction = self._get_tone_instruction(job, research)
        archetype_instruction = self._get_archetype_instruction(job)

        feedback_block = ""
        if feedback:
            feedback_block = f"""
IMPORTANT — Address this feedback from the HR reviewer:
{feedback}
Improve the cover letter accordingly."""

        archetype_block = f"\n{archetype_instruction}\n" if archetype_instruction else ""

        today_str = date.today().strftime("%d.%m.%Y")
        # The dateline city is the candidate's own (from ## Kontakt) and is prefixed
        # locally by the exporter, so the model only supplies the date.

        system_prompt = f"""You are an expert cover letter writer for the {self.market} job market.

{lang_instruction}
{tone_instruction}
{archetype_block}
RULES:
- Use ONLY facts from the candidate profile. NEVER fabricate, invent, or embellish experience, skills, or achievements not explicitly in the profile.
- Do NOT invent metrics, numbers, or KPIs not stated in the profile.
- Show genuine knowledge of the company, grounded in the COMPANY RESEARCH and JOB POSTING below.
- GROUNDING: only state company facts (products, values, projects, market position) that appear in the research or posting. NEVER invent awards, mission statements, client names, or product claims. If the research is thin, hook into a concrete detail from the job posting instead.
- NEVER change or add an attribute of something the profile describes. Keep format, schedule, location, delivery mode, scope and duration exactly as stated. If the profile says a degree runs in evening classes on site, do not call it hybrid, online or flexible; if a project is private, do not imply it was commercial.
- Where the posting demands something the profile does not support (travel, years of experience, a tool, on-site presence), say what is true and stop. Do NOT invent a detail that closes the gap — a claim that dissolves in the interview costs more than the requirement it papers over. Naming a genuine strength instead is always better.
- Be specific; mention actual projects, skills, and achievements FROM THE PROFILE ONLY.
- Each paragraph should have a clear purpose.
- STRICT WORD LIMIT: The ENTIRE body (para1+para2+para3+para4) must be 280-360 words. AIM FOR ~320 WORDS. Each paragraph should be 70-90 words. The HARD UPPER CEILING is 360 words. Count carefully.
- FORBIDDEN OPENINGS (these are AI/template tells, instant recruiter discount): never start para1 with "Hiermit bewerbe ich mich", "Mit großem Interesse habe ich Ihre Stellenanzeige", "Sehr geehrte Damen und Herren, hiermit", "I am writing to express my interest", "I am excited to apply". Open with a specific company hook: a value, a recent project, an industry shift, a product detail — something that proves you know them.

STYLE — AVOID AI TELLS:
- BANNED words/phrases (never use): "leverage", "utilize", "cutting-edge", "seasoned", "spearheaded", "synergy", "ecosystem", "holistic", "paradigm", "robust", "scalable", "passionate about", "committed to excellence", "proven track record", "deep understanding", "I thrive", "I am eager", "I am excited", "I am confident", "dynamic environment", "fast-paced", "results-driven", "harnessing the power of", "at the forefront of", "strategic vision", "adept at", "well-versed", "well-positioned", "particularly compelling", "strongly resonates", "aligns well"
- Do NOT use em-dashes. Use commas, periods, or semicolons instead.
- Do NOT start 2+ paragraphs with "I am" or "My".
- Vary sentence length. Mix short declarative sentences with longer ones.
- Be concrete and specific, not abstract. Name actual projects, tools, and outcomes.
- Write like a real person. Avoid corporate buzzwords and generic enthusiasm.

{_CL_REVIEW_BAR}
{feedback_block}

CANDIDATE PROFILE:
{self.profile_text}

CV SECTIONS (already tailored for this role):
{json.dumps(cv_sections, ensure_ascii=False, indent=2)}

JOB POSTING:
Title: {job.title}
Company: {job.company}
Description: {job.description}
Requirements: {', '.join(job.requirements)}

COMPANY RESEARCH:
{research}

Generate a cover letter as a JSON object with these keys:

1. "recipient": Street address block ONLY — do NOT repeat the company name (it is printed
   separately above this block). Put EACH address line on its own line, separated by a real
   newline character (e.g. "Musterstraße 1\\n12345 Musterstadt" → two lines). The renderer converts
   newlines into proper LaTeX line breaks, so the address stacks correctly.
   If you don't know the exact address, return an empty string "".

2. "date": "{today_str}" — use EXACTLY this value. Do NOT add a city or country; the
   renderer prefixes the candidate's city locally.

3. "subject": "Bewerbung als {job.title}" (or English equivalent if EN)

4. "salutation": the greeting line, ending with a comma.
   Use a personal form ONLY if the posting itself supplies the form of address
   (e.g. it writes "z.H. Herrn Mag. Huber" -> "Sehr geehrter Herr Mag. Huber,").
   NEVER infer a form of address or a gender from a name alone — if the posting names a
   contact without one, or names nobody, use the neutral form:
   German: "Sehr geehrte Damen und Herren,"   English: "Dear Hiring Manager,"

5. "para1" (70-90 words): Opening hook — company-specific. Reference a concrete fact drawn from the COMPANY RESEARCH or JOB POSTING (a product, market move, value, recent project, or industry shift) — do not invent one. Connect that fact to your trajectory in ONE sentence. Do NOT use any forbidden opening listed above.

6. "para2" (70-90 words): Map 1-2 job requirements to 1-2 specific CV achievements. Quantify where the profile gives numbers; otherwise lead with the tool + outcome. Mirror keywords from the job posting (e.g., if posting says "Power BI", write "Power BI", not "BI tools").

7. "para3" (70-90 words): Proof through projects. Name actual projects and roles FROM THE PROFILE with their technical stack and business context. Show range — ideally one analytical and one technical example.

8. "para4" (70-90 words): Why this role + why this company + why now. Close with availability and a clear next step ("freue mich auf ein persönliches Gespräch"). Confident, not arrogant.

Return ONLY valid JSON with these 8 keys. No text outside the JSON."""

        return self._generate_with_style_filter(
            system_prompt=system_prompt,
            user_message="Generate the cover letter sections as JSON.",
            expected_keys=["recipient", "date", "subject", "salutation",
                           "para1", "para2", "para3", "para4"],
            max_output_tokens=8000,  # includes Gemini 3.x thinking tokens
            label="CL",
        )

    # ------------------------------------------------------------------
    # Private — tone & formatting helpers
    # ------------------------------------------------------------------

    def _get_archetype_instruction(self, job: JobListing) -> str:
        """Return strategic proof-point guidance for the detected role archetype."""
        return ARCHETYPE_INSTRUCTIONS.get(job.archetype, "")

    def _get_tone_instruction(self, job: JobListing, research: str) -> str:
        """Detect company type and return tone instructions."""
        text = f"{job.company} {job.description} {research}".lower()

        consulting_signals = ["deloitte", "pwc", "kpmg", "ey", "mckinsey", "bcg", "bain", "consulting", "beratung", "advisory"]
        startup_signals = ["startup", "start-up", "agil", "flat hierarch", "remote-first", "scale-up"]
        public_signals = ["stadtwerke", "gemeinde", "ministerium", "öffentlich", "public sector", "magistrat"]

        if any(s in text for s in consulting_signals):
            return """TONE: Consulting/Professional Services
- Use Sie-Form (formal)
- Be analytical and results-driven
- Emphasize frameworks, structured problem-solving, measurable impact
- Show intellectual curiosity and client-orientation
- Use precise, confident language"""

        if any(s in text for s in startup_signals):
            return """TONE: Startup / Modern Company
- Use du-Form if the job posting uses it, otherwise Sie-Form
- Be energetic, concise, and direct
- Emphasize initiative, side projects, hands-on attitude
- Show passion for technology and building things
- Less formal structure, more personality"""

        if any(s in text for s in public_signals):
            return """TONE: Public Sector / Government
- Use Sie-Form (formal)
- Emphasize reliability, compliance, structured processes
- Highlight relevant education and formal qualifications
- Show awareness of regulatory frameworks
- Be methodical and thorough"""

        return """TONE: Corporate / Traditional Company
- Use Sie-Form (formal)
- Emphasize reliability, structured work, and process adherence
- Show clear career motivation and long-term thinking
- Balance professionalism with genuine enthusiasm
- Highlight teamwork and cross-functional collaboration"""

    def _generate_with_style_filter(
        self,
        system_prompt: str,
        user_message: str,
        expected_keys: list[str],
        max_output_tokens: int,
        label: str,
    ) -> dict[str, str]:
        """Generate JSON content; banned phrases are stripped deterministically by
        `sanitize_sections` (no wasted re-prompt call)."""
        response = generate_with_retry(
            self.client,
            self.model,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=max_output_tokens,
                response_mime_type="application/json",
            ),
        )
        # A truncated response parses to empty strings and renders as a blank
        # document, so name the cause rather than letting it look like weak output.
        finish = ""
        try:
            finish = str(response.candidates[0].finish_reason)
        except (AttributeError, IndexError):
            pass
        if "MAX_TOKENS" in finish:
            logger.error(
                f"{label}: response hit the {max_output_tokens}-token output cap and was cut off. "
                f"Gemini 3.x spends part of that budget on thinking tokens; raise it."
            )

        sections = sanitize_sections(self._parse_json_object(response.text or "", expected_keys))

        remaining = find_violations_in_dict(sections)
        if remaining:
            logger.info(f"{label}: residual style flags after local sanitize: {remaining}")
        return sections

    # ------------------------------------------------------------------
    # Private — response parsing
    # ------------------------------------------------------------------


    def _parse_json_object(self, text: str, expected_keys: list[str]) -> dict[str, str]:
        """Parse a JSON object from text, with fallback extraction."""
        # Try ```json block first
        code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if code_block:
            try:
                return json.loads(code_block.group(1))
            except json.JSONDecodeError:
                pass

        # Try raw JSON object
        start = text.find("{")
        if start != -1:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start : i + 1])
                        except json.JSONDecodeError:
                            break

        # Fallback: return empty dict with expected keys
        logger.error("Failed to parse JSON object from response")
        return {k: "" for k in expected_keys}
