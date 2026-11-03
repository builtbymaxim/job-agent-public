"""Interview prep agent — generates likely questions, STAR answers, and company insights."""

from __future__ import annotations

import logging
from datetime import date as date_cls
from pathlib import Path

from google import genai
from google.genai import types

from agents.privacy import redact_profile
from agents.utils import generate_with_retry
from models import TrackedApplication

logger = logging.getLogger(__name__)

MODEL = "gemini-3.5-flash"


class InterviewAgent:
    def __init__(self, profile_text: str, client: genai.Client, market: str = "European"):
        # Privacy invariant: contact PII never reaches the model.
        self.profile_text = redact_profile(profile_text)
        self.client = client
        self.market = market

    def prepare(self, app: TrackedApplication) -> str:
        """Generate interview prep document. Returns path to saved file."""
        job_posting = self._load_file(app.job_posting_path)
        company_research = self._load_file(app.company_research_path)

        system_prompt = f"""You are a senior career coach specializing in the {self.market} job market.
You are preparing a candidate for an interview.

CRITICAL RULES:
- Use ONLY real experience from the candidate's profile below. NEVER fabricate.
- STAR answers must reference actual projects, employers, theses and skills named in
  the candidate's profile.
- If the candidate lacks direct experience for a question, acknowledge it honestly
  and pivot to transferable skills.
- Write in the same language as the job posting (German or English).

CANDIDATE PROFILE:
{self.profile_text}

JOB POSTING:
{job_posting}

COMPANY RESEARCH:
{company_research}

FORMATTING: Heavily structured Markdown for fast scanning — clear `##`/`###` headers,
bullet points, and **bold** on the key phrase of every bullet. No long prose paragraphs.

Generate a comprehensive interview preparation document with this EXACT structure:

# Interview Prep: {app.position} at {app.company}

## 60-Second Pitch
The candidate's spoken answer to "Tell me about yourself" / "Erzählen Sie von sich".
- 3-4 bullets, each a beat of the pitch, **bold** the hook word.
- Beats in order: **who/positioning** → **proof** (a real project or employer) → **why THIS role/company**.
- Drawn ONLY from the real profile. Tight enough to say aloud in ~60 seconds.

## Salary Strategy
- **Stated minimum:** Extract the salary minimum from the JOB POSTING if stated (e.g. an
  Austrian Kollektivvertrag/KV figure: "KV", "Mindestgehalt", "€ ... brutto/Monat", "x14";
  or any stated range). Quote the exact figure with its period and number of payments
  (e.g. brutto/month ×14). If the posting does not state one,
  write **"not stated in posting"** — do NOT invent a number.
- **Target range:** the higher of the stated minimum and a realistic market target for this role/level.
- **Anchor:** one concrete brutto/month number to state first.
- **Counter-script:** one short sentence the candidate can say to counter a low offer.
- Use only the posting + profile. Do NOT fabricate external market data.

## Likely Questions (10-15)

### Technical Questions (4-5)
For each question:
- **Q:** The question
- **STAR Answer:**
  - **Situation:** Specific context from real experience
  - **Task:** What needed to be done
  - **Action:** What the candidate specifically did
  - **Result:** Concrete outcome or learning

### Behavioral Questions (4-5)
Same STAR format. Cover: teamwork, conflict, initiative, failure/learning, time management.

### Company-Specific Questions (3-4)
Questions about why this company, this role, their industry. Provide suggested answers
that reference the company research.

## Questions to Ask Them (5-7, posting-targeted)
Each question MUST reference a concrete detail from THIS job posting — a named tool,
a specific responsibility, a stated requirement, or a team/structure mention.
- Format each as a **bold one-line question** followed by a short "why this lands" note.
- No generic questions — if it could be asked at any company, rewrite it to cite this posting.

## Red Flags to Watch For
3-5 potential concerns based on the job posting and company research.
Things like: vague job descriptions, unusually broad requirements,
high turnover signals, unclear reporting structure.

## Company Quick Facts
- Industry position and recent developments
- Key values and culture signals
- Glassdoor/Kununu themes (if found in research)
- Competitors

Write the complete document as markdown."""

        response = generate_with_retry(
            self.client,
            MODEL,
            contents="Generate the complete interview preparation document.",
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=5500,
            ),
        )

        prep_content = response.text or ""

        # Save to application folder
        output_path = self._save_prep(app, prep_content)
        logger.info("Interview prep saved to %s", output_path)
        return output_path

    def generate_followup(self, app: TrackedApplication, language: str = "de") -> str:
        """Generate a follow-up email draft. Returns path to saved file."""
        days = app.days_since_sent

        lang_instruction = (
            "Write in German (formal Sie-Form)."
            if language == "de"
            else "Write in English."
        )

        tone_guidance = ""
        if days <= 7:
            tone_guidance = "It's been less than a week — this is a brief, polite check-in."
        elif days <= 14:
            tone_guidance = "It's been about two weeks — a standard professional follow-up."
        elif days <= 21:
            tone_guidance = "It's been three weeks — a firmer but still polite follow-up expressing continued interest."
        else:
            tone_guidance = "It's been over three weeks — a final follow-up, professional and gracious regardless of outcome."

        system_prompt = f"""You are a professional email writer. Generate a follow-up email for a job application.

{lang_instruction}
{tone_guidance}

Application details:
- Position: {app.position}
- Company: {app.company}
- Sent: {app.date_sent} ({days} days ago)
- Current status: {app.status.value}
- Contact person: {app.contact_person or 'Unknown'}

Rules:
- Keep it short (3-5 sentences max)
- Be professional but not desperate
- Reference the specific position
- If contact person is known, address them directly
- End with a clear but soft call to action

Write ONLY the email body (no subject line, no metadata). Start with the salutation."""

        response = generate_with_retry(
            self.client,
            MODEL,
            contents="Generate the follow-up email.",
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=500,
            ),
        )

        email_content = response.text or ""

        folder = Path(app.job_posting_path).parent if app.job_posting_path else None
        if folder and Path(folder).exists():
            path = Path(folder) / f"follow_up_{date_cls.today().isoformat()}.md"
            path.write_text(
                f"# Follow-Up Email — {app.position} at {app.company}\n\n{email_content}",
                encoding="utf-8",
            )
            return str(path)

        return email_content

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _load_file(self, path: str) -> str:
        """Load a text file, return empty string if not found."""
        if not path:
            return "(no data available)"
        p = Path(path)
        if p.exists():
            return p.read_text(encoding="utf-8")
        return f"(file not found: {path})"

    def _save_prep(self, app: TrackedApplication, content: str) -> str:
        """Save interview prep to the application folder."""
        if app.job_posting_path:
            folder = Path(app.job_posting_path).parent
        else:
            folder = Path(f"applications/{app.id}")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "interview_prep.md"
        path.write_text(content, encoding="utf-8")
        return str(path)
