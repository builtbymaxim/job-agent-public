"""LaTeX export agent — renders Jinja2 .tex templates, compiles with xelatex.

Takes an `ApplicationDraft`, writes CV + CL PDFs into the job folder, and
returns a manifest dict. Templates are selected per draft language via the
`templates` routing block in config.yaml.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from models import ApplicationDraft, JobLanguage

from .latex_utils import jinja_env, parse_kontakt

logger = logging.getLogger(__name__)


class LatexError(RuntimeError):
    """Raised when xelatex returns non-zero. Carries the tail of the .log."""


# Layout fit levels, tried in order until a document fits on one page. Each level
# tightens the type a little (see the `fit` variable in the templates); the cover
# letter never goes below 10 pt, the CV body never below 9.5 pt.
FIT_LEVELS = 4
MAX_PAGES = 1

_PAGES_RE = re.compile(r"Output written on .*? \((\d+) pages?\)")


@dataclass
class ExportedDocument:
    pdf_path: str
    tex_path: str
    pages: int = 0

    def to_dict(self) -> dict:
        return {"pdf_path": self.pdf_path, "tex_path": self.tex_path, "pages": self.pages}


class LatexExporter:
    def __init__(self, config: dict, profile_text: str, project_root: Path):
        self.config = config
        self.root = project_root
        self.env = jinja_env(project_root / "templates")
        self.kontakt = parse_kontakt(profile_text)
        # Main font: env override (containers/non-macOS) > config > macOS default.
        self.font = (
            os.getenv("JOB_AGENT_FONT")
            or (config.get("latex") or {}).get("font")
            or "Helvetica Neue"
        )

    def export(self, draft: ApplicationDraft, output_dir: str) -> dict:
        folder = Path(output_dir) / draft.job.folder_name
        folder.mkdir(parents=True, exist_ok=True)

        cv_tpl, cl_tpl = self._pick_templates(draft)
        surname_camel = "".join(self.kontakt.get("surname_display", "").title().split())
        ym = date.today().strftime("%Y-%m")
        cv_stem = f"Lebenslauf_{surname_camel}_{draft.job.company_slug}_{ym}"
        cl_stem = f"Anschreiben_{surname_camel}_{draft.job.company_slug}_{ym}"
        cv = self._render_and_compile(cv_tpl, self._cv_context(draft), folder, cv_stem)
        cl = self._render_and_compile(cl_tpl, self._cl_context(draft), folder, cl_stem)

        manifest = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "job": f"{draft.job.title} at {draft.job.company}",
            "language": draft.language.value,
            "cv": cv.to_dict(),
            "cl": cl.to_dict(),
        }
        (folder / "latex_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"LaTeX manifest saved to {folder / 'latex_manifest.json'}")
        return manifest

    def _pick_templates(self, draft: ApplicationDraft) -> tuple[str, str]:
        lang = draft.language.value
        routing = self.config.get("templates", {})
        entry = routing.get(lang)
        if not entry or "cv" not in entry or "cl" not in entry:
            raise LatexError(
                f"No template mapping for language '{lang}' in config[templates]. "
                f"Known languages: {sorted(routing.keys())}"
            )
        return entry["cv"], entry["cl"]

    def _cv_context(self, draft: ApplicationDraft) -> dict:
        cv = draft.cv_sections
        return {
            "kontakt": self.kontakt,
            "project_root": self.root.resolve().as_posix(),  # forward slashes: LaTeX-safe on Windows
            "font": self.font,
            "has_photo": (self.root / "assets" / "photo.png").exists(),
            "profil": cv.get("PROFIL", ""),
            "kenntnisse": cv.get("KENNTNISSE", ""),
            "projekte": cv.get("PROJEKTE", ""),
            "experiences": draft.experiences,
            "ausbildung": cv.get("AUSBILDUNG", ""),
            "sprachen": cv.get("SPRACHEN", ""),
        }

    def _cl_context(self, draft: ApplicationDraft) -> dict:
        cl = draft.cover_letter
        return {
            "kontakt": self.kontakt,
            "project_root": self.root.resolve().as_posix(),  # forward slashes: LaTeX-safe on Windows
            "font": self.font,
            "has_signature": (self.root / "assets" / "signature.png").exists(),
            "company": draft.job.company,
            "title": draft.job.title,
            "recipient": cl.get("recipient", ""),
            "date": self._dateline(cl.get("date", ""), draft.language),
            "subject": cl.get("subject", f"Bewerbung als {draft.job.title}"),
            # Never leave the greeting to chance: a letter without one reads as broken,
            # and the neutral form is always safe.
            "salutation": cl.get("salutation") or (
                "Sehr geehrte Damen und Herren,"
                if draft.language == JobLanguage.DE
                else "Dear Hiring Manager,"
            ),
            "para1": cl.get("para1", ""),
            "para2": cl.get("para2", ""),
            "para3": cl.get("para3", ""),
            "para4": cl.get("para4", ""),
        }

    def _dateline(self, model_date: str, language: JobLanguage) -> str:
        """`<city>, <date>` with the candidate's city from ## Kontakt (never from the model).

        `city_en` overrides `city` for English letters (e.g. Wien -> Vienna). A dateline
        that already carries a city (e.g. hand-edited) is kept as-is.
        """
        when = (model_date or "").strip() or date.today().strftime("%d.%m.%Y")
        if not when[:1].isdigit():
            return when
        city = (language == JobLanguage.EN and self.kontakt.get("city_en")) or self.kontakt.get("city", "")
        return f"{city}, {when}" if city else when

    def _render_and_compile(
        self, template_name: str, context: dict, folder: Path, stem: str
    ) -> ExportedDocument:
        """Render + compile, tightening the layout until the document fits one page."""
        doc = None
        for fit in range(FIT_LEVELS):
            doc = self._compile_once(template_name, {**context, "fit": fit}, folder, stem)
            if doc.pages <= MAX_PAGES:
                break
        if doc.pages > MAX_PAGES:
            logger.warning(
                f"{stem}.pdf has {doc.pages} pages even at the tightest layout; "
                "shorten the text before sending."
            )
        return doc

    def _compile_once(
        self, template_name: str, context: dict, folder: Path, stem: str
    ) -> ExportedDocument:
        tex_source = self.env.get_template(template_name).render(**context)
        tex_path = folder / f"{stem}.tex"
        tex_path.write_text(tex_source, encoding="utf-8")

        logger.info(f"Compiling {stem}.tex with xelatex")
        # Two passes: the header photo is placed with tikz `remember picture, overlay`
        # (absolute page anchor), which only resolves on the second run; a single pass
        # drops the portrait and leaves hyperref "Rerun to get…" warnings.
        result = None
        for _ in range(2):
            result = subprocess.run(
                [
                    "xelatex",
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    "-output-directory",
                    str(folder),
                    str(tex_path),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                # No line wrapping in the log, so the page count line parses reliably.
                env={**os.environ, "max_print_line": "10000"},
            )
            if result.returncode != 0:
                break  # no point running the second pass if the first failed
        if result.returncode != 0:
            log_path = folder / f"{stem}.log"
            log_tail = ""
            if log_path.exists():
                log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            raise LatexError(
                f"xelatex failed for {stem} (exit {result.returncode}):\n{log_tail}"
            )

        pdf_path = folder / f"{stem}.pdf"
        if not pdf_path.exists():
            raise LatexError(f"xelatex returned 0 but {pdf_path} not created")

        for ext in (".aux", ".log", ".out"):
            (folder / f"{stem}{ext}").unlink(missing_ok=True)

        m = _PAGES_RE.search(result.stdout or "")
        pages = int(m.group(1)) if m else 0
        return ExportedDocument(pdf_path=str(pdf_path), tex_path=str(tex_path), pages=pages)
