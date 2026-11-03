"""HR review agent — strict senior reviewer that scores and provides actionable feedback."""

from __future__ import annotations

import json
import logging
import re

from google import genai
from google.genai import types

from agents.privacy import redact_profile
from agents.utils import generate_with_retry
from models import ApplicationDraft, HRReview, JobLanguage

logger = logging.getLogger(__name__)

MODEL = "gemini-3.5-flash"

# What a domain reviewer actually looks for per role family. Mirrors the writer's
# ARCHETYPE_INSTRUCTIONS, but phrased as tests to apply rather than content to produce,
# so the reviewer judges craft instead of general polish.
_ARCHETYPE_REVIEW_CRITERIA: dict[str, str] = {
    "data_analyst": (
        "- Does it show the analysis was USED — a decision, a stakeholder, a recurring report — "
        "or only that a tool was operated?\n"
        "- Are data volumes, refresh cadence, or user counts named, or is it 'various dashboards'?\n"
        "- SQL and BI claims must be backed by a concrete artefact, not listed as keywords."
    ),
    "bi_developer": (
        "- Is there evidence of data MODELLING (schema, grain, consistency), not just report building?\n"
        "- Are ETL/pipeline claims tied to a real source system and a refresh mechanism?\n"
        "- Reusability and maintainability should appear; one-off reports are weaker evidence."
    ),
    "risk_analyst": (
        "- Is quantitative rigour demonstrated (validation, backtesting, statistical method named) "
        "rather than asserted?\n"
        "- Is there awareness of regulatory or model-governance context where the posting implies it?\n"
        "- Vague 'risk analysis' with no method or metric named is weak."
    ),
    "data_science_ml": (
        "- Is the full loop present — data, model choice, EVALUATION method, and what the result meant?\n"
        "- Are metrics and validation named (holdout, cross-validation, baseline compared against)?\n"
        "- A model with no stated evaluation is a red flag, not a credential."
    ),
    "business_analyst": (
        "- Is there evidence of working WITH stakeholders — requirements, trade-offs, decisions enabled?\n"
        "- Are process or documentation artefacts concrete (what was produced, for whom)?\n"
        "- Pure tool lists without a business outcome score low on relevance."
    ),
    "general": "",
}


class HRAgent:
    def __init__(
        self,
        client: genai.Client,
        candidate_context: str = "",
        model: str = MODEL,
        max_loops: int = 2,
        pass_score: int = 7,
        profile_text: str = "",
        market: str = "European",
    ):
        self.client = client
        self.market = market
        self.candidate_context = candidate_context
        self.model = model
        self.max_loops = max_loops
        self.pass_score = pass_score
        # Ground truth for the fabrication check. Redacted at the writer boundary
        # already; redact again here so the privacy invariant holds no matter who
        # constructs the agent.
        self.profile_text = redact_profile(profile_text) if profile_text else ""

    def _passed(self, review: HRReview) -> bool:
        """Both CV and CL meet the configured pass threshold."""
        return review.cv_score >= self.pass_score and review.cl_score >= self.pass_score

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def review(self, draft: ApplicationDraft) -> HRReview:
        """Review a single application draft and return scores + feedback."""
        system_prompt = self._system_prompt(draft)

        cv_text = json.dumps(draft.cv_sections, ensure_ascii=False, indent=2)
        cl_text = json.dumps(draft.cover_letter, ensure_ascii=False, indent=2)

        response = generate_with_retry(
            self.client,
            self.model,
            contents=(
                f"Review this application:\n\n"
                f"=== CV SECTIONS ===\n{cv_text}\n\n"
                f"=== COVER LETTER ===\n{cl_text}\n\n"
                f"Score and provide feedback as JSON."
            ),
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=4000,  # includes Gemini 3.x thinking tokens
                response_mime_type="application/json",
            ),
        )

        return self._parse_review(response)

    def review_loop(
        self,
        writer,  # ApplicationWriter — not type-hinted to avoid circular import
        job,
        initial_draft: ApplicationDraft,
        on_review=None,
    ) -> tuple[ApplicationDraft, HRReview]:
        """Run review → rewrite loop until scores >= 7 or max loops reached.

        `on_review(round, review, passed)` is called after every review, so a UI can
        show each round while it happens.
        """
        draft = initial_draft
        review = None
        # Keep the best draft seen, not the last one. A revision can come back worse
        # — in one run a truncated response replaced an 8/10 cover letter with an
        # empty one — and shipping a regression because it happened to be newest is
        # never right.
        best_draft, best_review, best_total = None, None, -1

        # The company summary is job-scoped, not draft-scoped: reuse it across
        # revisions instead of re-running the web research every round.
        research = initial_draft.company_research

        for i in range(self.max_loops):
            review = self.review(draft)
            draft.revision = i + 1
            logger.info(
                f"  HR Review #{i + 1}: CV={review.cv_score}/10, CL={review.cl_score}/10"
            )

            if on_review:
                on_review(i + 1, review, self._passed(review))

            total = review.cv_score + review.cl_score
            if total > best_total:
                best_draft, best_review, best_total = draft, review, total

            if self._passed(review):
                logger.info("  HR Review: PASSED")
                break

            if i < self.max_loops - 1:
                logger.info("  HR Review: FAILED — sending back to writer...")
                draft = writer.generate(
                    job, feedback=review, research=research, previous=draft
                )
                draft.revision = i + 2

        if best_review is not None and best_review is not review:
            logger.info(
                f"  HR Review: revision scored worse "
                f"(CV={review.cv_score}, CL={review.cl_score}) — keeping the better draft "
                f"(CV={best_review.cv_score}, CL={best_review.cl_score})."
            )
        draft, review = best_draft or draft, best_review or review

        if review and not self._passed(review):
            logger.info(
                f"  HR Review: Max loops reached. Final scores: CV={review.cv_score}, CL={review.cl_score}"
            )

        return draft, review

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _system_prompt(self, draft: ApplicationDraft) -> str:
        """Build the reviewer system prompt, calibrated to the candidate's level
        (from `candidate_context`) rather than a hardcoded persona."""
        lang = "German" if draft.language == JobLanguage.DE else "English"

        if self.candidate_context:
            context_block = (
                f"CANDIDATE LEVEL — {self.candidate_context}.\n"
                "Set the bar at what a strong applicant AT THIS LEVEL would submit for THIS role. "
                "Do not apply a senior bar to a junior candidate, and do not soften the bar because "
                "the candidate is junior: within their level, judge hard."
            )
        else:
            context_block = (
                "Set the bar at the seniority implied by the candidate's own experience — "
                "neither a senior standard nor a lenient one."
            )

        if self.profile_text:
            truth_block = f"""CANDIDATE PROFILE — THE ONLY SOURCE OF TRUTH ABOUT THIS CANDIDATE:
{self.profile_text}

FABRICATION CHECK — do this FIRST, before scoring anything:
Every employer, job title, date, project name, technology, metric, and claim in the CV and
cover letter must be traceable to the profile above, and every company fact must be traceable
to the job posting or company research. Flag anything that is not.
- An invented employer, role, date, degree, project, or number is a CRITICAL defect.
- An inflated claim (profile says "coursework", application says "years of experience") is a
  CRITICAL defect.
- Company facts not present in the posting or research — invented awards, mission statements,
  client names, product claims — are a CRITICAL defect.
- ALTERED ATTRIBUTES are a CRITICAL defect and the easiest to miss: the entity is real but a
  detail about it has been changed or added. Check HOW each thing is described — format,
  schedule, location, delivery mode, scope, duration, seniority, team size, outcome. If the
  profile says a degree runs in evening classes on site, the letter may not call it hybrid or
  online; if the profile says a project was personal, the letter may not imply it was
  commercial. Compare attribute by attribute, not just name by name.
- Look hardest wherever the posting creates pressure the profile does not satisfy — travel
  requirements, years of experience, a specific tool, on-site presence. That is exactly where
  a plausible-sounding embellishment gets inserted to close the gap, and it is the kind that
  collapses in an interview.
Any critical defect caps the affected score at 3 and must be named explicitly in the feedback,
quoting the offending text. A fluent, well-structured application built on invented facts is
worse than a plain honest one; score it accordingly."""
        else:
            truth_block = ""

        archetype_block = _ARCHETYPE_REVIEW_CRITERIA.get(getattr(draft.job, "archetype", ""), "")
        if archetype_block:
            archetype_block = f"ROLE-SPECIFIC CRITERIA — apply these when judging relevance:\n{archetype_block}"

        return f"""You are a demanding hiring manager for this specific role in the {self.market} market. You have reviewed thousands of applications and you reject most of them. You are not an HR generalist: you understand the craft this role requires and you can tell real substance from fluent filler.

You are reviewing an application for: {draft.job.title} at {draft.job.company}

The application is written in {lang}.

{context_block}

{truth_block}

{archetype_block}

SCORING RUBRIC — CV (0-10):
- Relevance to job posting (0-3): Do the highlighted skills, projects, and experience match what THIS job requires? Coursework and projects count, but only where they demonstrate the actual required capability.
- Professional presentation (0-2): Well-structured, concise, error-free?
- Specificity and concreteness (0-3): Concrete tools, outcomes, scope, numbers? Vague competence claims score 0 here.
- Language quality (0-2): Grammar, style, register appropriate to the company type?

SCORING RUBRIC — Cover Letter (0-10):
- Company-specific substance (0-3): Does it prove knowledge of THIS company from the posting or research? A letter that would work for any employer in the industry scores 0 here.
- Clear, believable motivation (0-2): Why THIS role at THIS company? Generic enthusiasm scores 0.
- Relevant experience, front-loaded (0-3): Are the most relevant proof points first, and tied to the posting's actual requirements?
- Professional tone (0-2): Confident without arrogance, appropriate register?

SCORING DISCIPLINE — use the full range:
- 9-10: exceptional. Would be shortlisted ahead of strong competition.
- 7-8: strong. Clearly worth an interview; specific, well-targeted, no filler.
- 5-6: competent but forgettable. Real content, weak targeting or generic phrasing.
- 3-4: weak. Vague claims, poor fit to the posting, or padding.
- 0-2: unusable, or built on fabricated facts.
Most first drafts are NOT 7+. Do not award 7 for an application that is merely
inoffensive — 7 means you would genuinely advance this candidate. If you find
yourself scoring everything 7-8, you are not reading critically enough.

FEEDBACK RULES:
- Quote the exact sentence or section that is weak.
- Explain WHY it is weak, then give a concrete replacement.
- Lead with the single highest-impact fix.
- If score >= 7: name what works and the one change that would raise it further.

JOB POSTING:
Title: {draft.job.title}
Company: {draft.job.company}
Description: {draft.job.description}
Requirements: {', '.join(draft.job.requirements)}

Return ONLY valid JSON:
{{"cv_score": <int 0-10>, "cl_score": <int 0-10>, "cv_feedback": "<detailed feedback>", "cl_feedback": "<detailed feedback>"}}"""

    def _parse_review(self, response) -> HRReview:
        """Parse HR review from Gemini response."""
        text = response.text or ""

        # Try ```json block
        code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if code_block:
            try:
                data = json.loads(code_block.group(1))
                return HRReview.from_dict(data)
            except (json.JSONDecodeError, KeyError):
                pass

        # Try raw JSON
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
                            data = json.loads(text[start : i + 1])
                            return HRReview.from_dict(data)
                        except (json.JSONDecodeError, KeyError):
                            break

        # Fallback — conservative scores to trigger rewrite.
        # Name the likely cause: a truncated response is an output-budget problem,
        # not a weak draft, and it used to surface as an unexplained 4/10.
        finish = None
        try:
            finish = str(response.candidates[0].finish_reason)
        except (AttributeError, IndexError):
            pass
        if finish and "MAX_TOKENS" in finish:
            logger.error(
                "HR review hit the output cap and was cut off mid-JSON — the 4/10 below is a "
                "parse failure, NOT a verdict on the draft. Raise max_output_tokens: Gemini 3.x "
                "spends part of that budget on thinking tokens before it emits any text."
            )
        else:
            logger.error(f"Failed to parse HR review (finish_reason={finish}), returning conservative scores")
        return HRReview(
            cv_score=4,
            cl_score=4,
            cv_feedback="Could not parse review. Please improve overall quality.",
            cl_feedback="Could not parse review. Please improve overall quality.",
        )

    def save_review(self, review: HRReview, output_dir: str) -> str:
        """Save the final HR review to the application folder.

        Only the final review is kept: per-revision files piled up without
        anything reading them, and it is the last verdict that explains why a
        rendered draft looks the way it does.
        """
        from pathlib import Path

        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)
        filename = path / "hr_review.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(review.to_dict(), f, ensure_ascii=False, indent=2)
        return str(filename)
