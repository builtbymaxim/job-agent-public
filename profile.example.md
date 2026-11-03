# Example — Master Profile (CV / Applications Source)

# ─────────────────────────────────────────────────────────────────────────────
# HOW TO USE
#   1. Copy this file to a personal one, e.g.  profile_me.md
#        cp profile.example.md profile_me.md
#      (any name except profile.example.md is gitignored — your PII stays local)
#   2. Point config.yaml at it:   profile: "profile_me.md"
#   3. Fill in the ## Kontakt keys exactly (they feed the LaTeX letterhead) and
#      replace the prose below with your own.
#
# NOTE: contact data here is rendered into the PDF letterhead ONLY and is stripped
# before any LLM prompt. Keep the ## Kontakt key names unchanged.
#
# NOTE on ## Berufserfahrung: list newest first, "### Company — Role" per entry.
# Never put comment text between the "## Berufserfahrung" heading and the first
# "### " entry — parse_experiences() splits on "### ", so anything sitting there
# gets parsed as a bogus first experience.
# For a signed-but-not-started role: say what's true when the profile is next READ,
# not signed. If you'll rerun the pipeline before the start date, use "ab MM.YYYY";
# if the role will already be underway by then, "seit MM.YYYY" is accurate now.
# Either way, describe the role's defined scope from the job description/contract
# rather than results you haven't had the chance to produce yet — rewrite with real
# outcomes once you've actually started.
# ─────────────────────────────────────────────────────────────────────────────

## Kontakt
name: Max Mustermann
surname_display: MUSTERMANN
headline: B.Sc. Field · Skill A · Skill B · Skill C
dob: 01.01.1990
address: Musterstraße 1, 1010 Wien
email: max.mustermann@example.com
phone: +43 660 000 00 00
github: yourhandle
github_url: https://github.com/yourhandle
linkedin: your-linkedin-slug
linkedin_url: https://www.linkedin.com/in/your-linkedin-slug/

## Eckdaten
# Machine-parsed facts used to score jobs against you. Keep the key names; edit values.
level: Graduating B.Sc. Field student, University Name, final semesters
target_roles: Role A, Role B, Role C
location: City only (no relocation)
languages: Language A (native), Language B (fluent)
salary: 45.000€ - 55.000€
availability: e.g. immediately / from MM.YYYY

## Kurzprofil
One short paragraph: who you are professionally, your focus area, and the value you
bring. Keep it factual — this is consumed by the writer to ground every application.

---

## Persönliches Profil & Arbeitsweise
A paragraph on your working style, interests, and what motivates you. Then a short list:

Arbeitsweise:
- trait one
- trait two
- trait three

---

## Ausbildung
# List degrees newest first. A second, currently-running degree (e.g. a part-time
# Master alongside full-time work) is a common and worthwhile pattern — say so
# explicitly if the schedule is what makes you fully available (evening classes,
# distance format, etc.), since that's exactly the kind of thing an application
# should state rather than leave for the reader to guess.

### University Name (current)
**Degree / Programme (M.Sc.)**
Since term/year · duration · format (e.g. part-time, evening classes)

- what it covers, briefly
- why it's relevant to your target roles

---

### University Name (completed)
**Degree / Programme (B.Sc.)**
Years (completed — or "thesis submitted", "graded, certificate pending", whichever
is actually true today. Don't round up to "completed" before it is.)

**Specializations:**
- area one
- area two

**Thesis (optional):**
*Title* — one or two lines on scope and method.

---

### Prior School / Programme
Short line on focus and any notable project.

---

## Berufserfahrung

### Company One — Role
**City | ab/seit MM.YYYY** (whichever is true when this is read — scope only, no invented outcomes yet)

- responsibility one, as defined in the job description / contract
- responsibility two
- team / context you'll be working in

---

### Company Two — Role
**City | Years**

- accomplishment with a concrete result
- responsibility with scope
- tools / stack used

**Mehrwert:** one line on the impact you delivered.

---

### Company Three — Role
**City | Years**

- accomplishment
- responsibility

**Mehrwert:** impact line.

---

## Projekte & Repositories (Auswahl)

### Project Name (privat)
One-line description.

- **Stack:** languages, frameworks, tools
- **Highlight:** the hard/interesting part
- **Fokus:** what it demonstrates about you
- **Status/Grenzen (empfohlen):** what's actually finished vs. still open, and any
  limitation you'd rather state yourself than have surface as a question in an
  interview. A precisely-scoped project reads as more credible than an unqualified
  one — this is worth the extra line.

---

## Technische Kenntnisse

### Category A (e.g. Data Science & Analytics)
- skill, skill, skill

### Category B (e.g. BI & Reporting)
- skill, skill

### Category C (e.g. Daten & Systeme)
- skill, skill

### Tools & Methoden
- tool, tool, tool

---

## Sprachen
- Language A (native)
- Language B (fluent)
- Language C (intermediate)

---

## Differenzierungsmerkmale
- what makes your profile unusual or strong
- a second differentiator

---

## Verfügbarkeit & Standort
- Standort: City
- constraint (e.g. no relocation)

---

## Interessen & Ausrichtung
- target industry / domain one
- target domain two

## Gehaltsvorstellung
- 45.000€ - 55.000€
