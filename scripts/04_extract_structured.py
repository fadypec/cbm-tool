#!/usr/bin/env python3
"""
04_extract_structured.py — Extract structured facility data from CBM form segments.

Default (Form A Part 1):
  Reads data/segmented/{id}/form_a1.txt → data/structured/{id}_form_a1.json

--form-g (Form G: vaccine production facilities):
  Reads data/segmented/{id}/form_g.txt → data/structured/{id}_form_g.json

--form-a2 (Form A Part 2: national biological defence R&D programmes):
  Reads data/segmented/{id}/form_a2.txt → data/structured/{id}_form_a2.json
  Extracts defence_programmes (from Part 2ii) and defence_facilities (from Part 2iii).

Usage:
    python scripts/04_extract_structured.py
    python scripts/04_extract_structured.py --single USA_2023
    python scripts/04_extract_structured.py --form-g
    python scripts/04_extract_structured.py --form-g --single DEU_2023
    python scripts/04_extract_structured.py --form-a2
    python scripts/04_extract_structured.py --form-a2 --single GBR_2023
"""

import argparse
import base64
import io
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CATALOGUE_PATH = PROJECT_ROOT / "data" / "catalogue.json"
SEGMENTED_DIR  = PROJECT_ROOT / "data" / "segmented"
STRUCTURED_DIR = PROJECT_ROOT / "data" / "structured"
RAW_PDFS_DIR   = PROJECT_ROOT / "data" / "raw_pdfs"

# ── Constants ────────────────────────────────────────────────────────────────

from model_config import MODEL
MAX_TOKENS       = 8192
CHUNK_MAX_CHARS  = 4_000    # max chars per API call (keeps output well within 8192 token limit after translation)
RATE_LIMIT_DELAY = 10.0     # seconds between calls  →  ≤6 req/min

# Per-token pricing for cost estimation (Sonnet, as of 2025-05 pricing).
# Update these when switching models or if pricing changes.
COST_PER_INPUT_TOKEN  = 3e-6    # $3 / 1M input tokens
COST_PER_OUTPUT_TOKEN = 15e-6   # $15 / 1M output tokens

# Matches the start of each facility entry (field 1): English, French, Spanish, Russian/Ukrainian
# Russian 2015 format: "N. Наименование объекта: <name>" (sequential facility numbers)
# Russian 2016+ format: "1. Наименование(я) объекта" (template header only; actual entry is "1.1. ...")
# Pattern \d+\.\s+Наименование[^(] distinguishes "Наименование объекта:" (2015)
# from "Наименование(я) объекта" (2016+ header) by checking for absence of "("
FACILITY_RE = re.compile(r"(?m)^(?:1\.\s+(?:Name|Nom|Nombre)\b|\d+\.\s+Наименование\s+объекта)")

# Form G: field 1 boundary — English, French, Spanish
FORM_G_RE = re.compile(r"(?m)^1\.\s+(?:Name of facility|Nom de l'installation|Nombre de la instalaci[oó]n)\b")

# Form A Part 2: section boundary patterns (English only; non-English falls back to char-split)
# Part 2(ii) programme start: "1. State the objectives..."
# Part 2(iii) facility start: "1. What is the name of the facility?"
FORM_A2_PROG_RE = re.compile(r"(?m)^1\.\s+State the objectives")
FORM_A2_FAC_RE  = re.compile(r"(?m)^1\.\s+What is the name of the facility")
FORM_A2_CHUNK_MAX_CHARS = 12_000   # larger than A1 — each section may be 4-20 KB
FORM_A2_PROG_LOOKBACK   = 400      # chars before "1. State the objectives" to include programme heading

# Pre-truncation: cut the parts of each section that contain no extractable data
# Part 2(ii): field 1 (objectives narrative) is often 5-10 pages; fields 2-7 hold the key data
FORM_A2_PROG_FIELD2_RE   = re.compile(r"(?m)^2\.\s+State the total funding")
FORM_A2_PROG_FIELD1_MAX  = 2_000   # keep at most 2000 chars of objectives before field 2

# Part 2(iii): field 4(viii)/(ix) is a publications list — can be 50K+ chars; not extracted
# Some docs label it (viii), others (ix) depending on whether policy is a separate sub-field
FORM_A2_FAC_PUBLIST_RE   = re.compile(r"(?m)^\((?:viii|ix)\)\s+Provide a list of publicly")
FORM_A2_FAC_FIELD5_RE    = re.compile(r"(?m)^5\.\s+Briefly describe the biological defence work")
FORM_A2_FAC_FIELD5_MAX   = 1_500   # keep at most 1500 chars of field 5 work description

PROGRESS_INTERVAL = 10             # emit a log.info progress line every N documents

# Form E: national biosafety/biosecurity legislation
# No truncation: the Yes/No table appears early, but many docs (e.g. IRL) use
# "Yes/No" template text without typographic distinction — Claude needs the full
# law listings to infer which option applies. Max doc is ~29KB, cost is trivial.
FORM_E_MAX_CHARS = None            # no truncation

# ── Form E Vision helpers ────────────────────────────────────────────────
# Form E rarely exceeds 14 pages; guard against segmentation bugs.
MAX_VISION_PAGES = 20

_PAGE_MARKER_RE = re.compile(r"^--- PAGE (\d+) ---$", re.MULTILINE)


def _form_e_page_numbers(form_text_path: Path) -> list[int]:
    """Extract 1-indexed PDF page numbers from segmented form_e.txt markers.

    The segmentation script inserts '--- PAGE N ---' headers.  Returns a
    sorted, deduplicated list of page numbers found in the file.
    """
    text = form_text_path.read_text(encoding="utf-8")
    return sorted(set(int(m.group(1)) for m in _PAGE_MARKER_RE.finditer(text)))


def _render_pages_as_base64(
    pdf_path: Path,
    pages: list[int],
    dpi: int = 300,
) -> list[dict]:
    """Render specific PDF pages to base64-encoded PNG content blocks.

    Returns a list of Anthropic Vision content blocks ready for insertion
    into a messages list:
        [{"type": "image", "source": {"type": "base64",
          "media_type": "image/png", "data": "..."}}, ...]

    Uses 300 DPI for sharp rendering of thin formatting marks (strikethrough
    lines, underlines).  Each page is typically ~200 KB as PNG, well within
    Anthropic's 5 MB per-image limit.
    """
    from pdf2image import convert_from_path

    blocks: list[dict] = []
    for page_num in pages:
        imgs = convert_from_path(
            str(pdf_path), dpi=dpi,
            first_page=page_num, last_page=page_num,
        )
        buf = io.BytesIO()
        imgs[0].save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": b64,
            },
        })
    return blocks


# ── Form E table layout ─────────────────────────────────────────────────

_FORM_E_ROW_NAMES = ("(a) Prohibitions", "(b) Exports",
                     "(c) Imports", "(d) Biosafety")
_FORM_E_COL_NAMES = ("Legislation", "Regulations",
                     "Other measures", "Amended")


def _detect_form_e_formatting(
    pdf_path: Path,
    page_nums: list[int],
) -> str | None:
    """Detect strikethrough/underline/color on Form E Yes/No values via pdfplumber.

    The Form E table has 4 rows × 4 columns of Yes/No cells.  Many countries
    use strikethrough to reject an option (thin ~0.5pt rects drawn over text),
    underline to select one, or accent color (e.g. red font) to highlight the
    selected option.  These formatting marks are invisible in extracted text but
    detectable as PDF geometry/color objects via pdfplumber.

    Handles two word formats:
      - Combined "Yes/No" tokens: rect position (left vs right half of word)
        determines which option is struck through; char color on left half = Yes.
      - Separate "Yes" and "No" words: direct rect-to-word overlap matching.

    Returns a formatted annotation string for prepending to the extracted
    text, or None if no relevant formatting marks are found.
    """
    import pdfplumber

    def _is_accent_color(color) -> bool:
        """Return True if color is a clear accent (e.g. red), not black or scan-gray.

        Scanned documents render black ink as dark gray (non_stroking_color ~0.13–0.25
        in grayscale).  We only want to flag intentional color marks such as red font,
        so grayscale values must exceed 0.5 (clearly mid-gray or lighter) to count.
        RGB/CMYK values are checked for non-blackness at the standard threshold.
        """
        if color is None:
            return False
        if isinstance(color, (int, float)):        # grayscale scalar
            return float(color) > 0.5             # exclude scan-dark gray (< 0.5)
        if isinstance(color, (list, tuple)):
            if len(color) == 1:                    # grayscale 1-tuple
                return float(color[0]) > 0.5
            if len(color) == 3:                    # RGB
                r, g, b = color
                return not (r < 0.1 and g < 0.1 and b < 0.1)  # not black
            if len(color) == 4:                    # CMYK
                return color[3] < 0.9              # not full-key (black)
        return False

    # Geometry thresholds (PDF points)
    RECT_MAX_HEIGHT = 2.0     # strikethrough/underline rects are ~0.5pt tall
    RECT_MIN_WIDTH = 5.0      # must span at least part of a word
    VERT_TOLERANCE = 4.0      # rect-to-word vertical proximity
    ROW_CLUSTER = 12.0        # words within 12pt vertically → same table row

    # Each cell gets a resolved value: Yes / No / unknown
    cells: list[dict] = []    # {top, x0, value, page}

    with pdfplumber.open(str(pdf_path)) as pdf:
        for pn in page_nums:
            if pn < 1 or pn > len(pdf.pages):
                continue
            page = pdf.pages[pn - 1]
            words = page.extract_words() or []
            rects = (page.rects or []) + (page.lines or [])
            chars = page.chars or []

            # Thin horizontal rects/lines = strikethrough or underline candidates.
            # Lines have height=0 so always pass the height check; rects are filtered
            # to those thin enough to be decoration marks (not table borders).
            thin_rects = [
                r for r in rects
                if abs(r["bottom"] - r["top"]) < RECT_MAX_HEIGHT
                and (r["x1"] - r["x0"]) > RECT_MIN_WIDTH
            ]
            if not thin_rects:
                continue

            # Detect word format: combined "Yes/No" vs separate "Yes" + "No"
            # Include Cyrillic Да/Нет for Russian/Ukrainian submissions.
            combined = [w for w in words
                        if w["text"].strip().lower() in ("yes/no", "oui/non")
                        or w["text"].strip() in ("Да/Нет", "да/нет")]
            separate_yes = [w for w in words
                           if w["text"].strip().lower() in ("yes", "oui")
                           or w["text"].strip() in ("Да", "да")]
            separate_no = [w for w in words
                          if w["text"].strip().lower() in ("no", "non")
                          or w["text"].strip() in ("Нет", "нет")]

            if combined:
                # Combined "Yes/No" — each word is one cell.
                # A rect on the left half strikes through "Yes" (→ value=No);
                # a rect on the right half strikes through "No" (→ value=Yes).
                for w in combined:
                    w_mid_x = (w["x0"] + w["x1"]) / 2
                    w_mid_y = (w["top"] + w["bottom"]) / 2
                    value = "unknown"

                    for r in thin_rects:
                        if r["x1"] < w["x0"] or r["x0"] > w["x1"]:
                            continue
                        r_mid_y = (r["top"] + r["bottom"]) / 2

                        # Strikethrough at word midline
                        if abs(r_mid_y - w_mid_y) < VERT_TOLERANCE:
                            r_mid_x = (r["x0"] + r["x1"]) / 2
                            # Left half = "Yes" struck through → No
                            # Right half = "No" struck through → Yes
                            value = "No" if r_mid_x < w_mid_x else "Yes"
                            break

                        # Underline at word bottom
                        if abs(r_mid_y - w["bottom"]) < VERT_TOLERANCE:
                            r_mid_x = (r["x0"] + r["x1"]) / 2
                            # Left half = "Yes" underlined → Yes
                            # Right half = "No" underlined → No
                            value = "Yes" if r_mid_x < w_mid_x else "No"
                            break

                    # Fallback: accent color (e.g. red font) on one half.
                    # Left-half "Yes" chars colored → Yes selected;
                    # Right-half "No" chars colored → No selected.
                    if value == "unknown":
                        for c in chars:
                            if (c["x1"] < w["x0"] or c["x0"] > w["x1"]
                                    or c["bottom"] < w["top"]
                                    or c["top"] > w["bottom"]):
                                continue
                            if not _is_accent_color(c.get("non_stroking_color")):
                                continue
                            c_mid_x = (c["x0"] + c["x1"]) / 2
                            value = "Yes" if c_mid_x < w_mid_x else "No"
                            break

                    cells.append({
                        "top": w["top"], "x0": w["x0"],
                        "value": value, "page": pn,
                    })

            elif separate_yes or separate_no:
                # Separate "Yes" and "No" words — check each for formatting,
                # then pair by proximity to resolve cell values.
                word_info: list[dict] = []
                for w in separate_yes + separate_no:
                    w_mid_y = (w["top"] + w["bottom"]) / 2
                    formatting = None

                    for r in thin_rects:
                        if r["x1"] < w["x0"] or r["x0"] > w["x1"]:
                            continue
                        r_mid_y = (r["top"] + r["bottom"]) / 2
                        if abs(r_mid_y - w_mid_y) < VERT_TOLERANCE:
                            formatting = "strikethrough"
                            break
                        if abs(r_mid_y - w["bottom"]) < VERT_TOLERANCE:
                            formatting = "underline"
                            break

                    # Fallback: accent color (e.g. red font) = selected option
                    if formatting is None:
                        for c in chars:
                            if (c["x1"] < w["x0"] or c["x0"] > w["x1"]
                                    or c["bottom"] < w["top"]
                                    or c["top"] > w["bottom"]):
                                continue
                            if _is_accent_color(c.get("non_stroking_color")):
                                formatting = "colored"
                                break

                    word_info.append({
                        "text": w["text"].strip().lower(),
                        "top": w["top"], "x0": w["x0"],
                        "formatting": formatting,
                    })

                # Group into row clusters then pair Yes+No within each row
                word_info.sort(key=lambda wi: (wi["top"], wi["x0"]))
                wi_rows: list[list[dict]] = []
                for wi in word_info:
                    if (not wi_rows
                            or abs(wi["top"] - wi_rows[-1][0]["top"])
                            > ROW_CLUSTER):
                        wi_rows.append([wi])
                    else:
                        wi_rows[-1].append(wi)

                for row in wi_rows:
                    row.sort(key=lambda wi: wi["x0"])
                    for ci in range(0, len(row), 2):
                        pair = row[ci : ci + 2]
                        if len(pair) == 2:
                            w1, w2 = pair
                            is_yes = w1["text"] in ("yes", "oui")
                            yes_w = w1 if is_yes else w2
                            no_w = w2 if is_yes else w1

                            if no_w["formatting"] == "strikethrough":
                                value = "Yes"
                            elif yes_w["formatting"] == "strikethrough":
                                value = "No"
                            elif yes_w["formatting"] == "underline":
                                value = "Yes"
                            elif no_w["formatting"] == "underline":
                                value = "No"
                            elif yes_w["formatting"] == "colored":
                                value = "Yes"
                            elif no_w["formatting"] == "colored":
                                value = "No"
                            else:
                                value = "unknown"
                        else:
                            value = "unknown"

                        cells.append({
                            "top": pair[0]["top"], "x0": pair[0]["x0"],
                            "value": value, "page": pn,
                        })

    if not cells or not any(c["value"] != "unknown" for c in cells):
        return None

    # ── Group cells into table rows by y-position ────────────────────
    cells.sort(key=lambda c: (c["top"], c["x0"]))
    rows: list[list[dict]] = []
    for c in cells:
        if not rows or abs(c["top"] - rows[-1][0]["top"]) > ROW_CLUSTER:
            rows.append([c])
        else:
            rows[-1].append(c)

    # ── Format annotation ────────────────────────────────────────────
    lines = [
        "[PDF FORMATTING ANALYSIS — Form E Table]",
        "Detected formatting marks on Yes/No values in the original PDF.",
        "Strikethrough = REJECTED option; underline = SELECTED option.",
        "",
    ]

    for row_idx, row_cells in enumerate(rows):
        row_cells.sort(key=lambda c: c["x0"])
        label = (_FORM_E_ROW_NAMES[row_idx]
                 if row_idx < len(_FORM_E_ROW_NAMES)
                 else f"Row {row_idx + 1}")

        parts: list[str] = []
        for col_idx, cell in enumerate(row_cells):
            col = (_FORM_E_COL_NAMES[col_idx]
                   if col_idx < len(_FORM_E_COL_NAMES)
                   else f"Col {col_idx + 1}")
            parts.append(f"{col}={cell['value']}")

        lines.append(f"  {label}: {', '.join(parts)}")

    lines.extend([
        "",
        "[END FORMATTING ANALYSIS]",
        "",
        "Use the values above for the Form E table. For any 'unknown' cells,",
        "infer from the narrative text below.",
        "",
    ])

    return "\n".join(lines)


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are extracting structured data from a BWC Confidence-Building Measure \
submission (Form A, Part 1: Research Centres and Laboratories).

The document contains one or more facility declarations. Each uses \
numbered fields 1-7 from the standard BWC template.

Extract ALL facilities. Return ONLY valid JSON, no preamble:
{
  "facilities": [
    {
      "facility_name": "string (in English)",
      "facility_name_original": "string — original non-English name, or null if already English",
      "responsible_organisation": "string (in English)",
      "responsible_organisation_original": "string — original non-English name, or null if already English",
      "translated": true/false,
      "location": {
        "address": "string",
        "city": "string",
        "country_iso3": "3-letter ISO code",
        "full_text": "complete location text as given"
      },
      "funding": {
        "sources": ["list of funding sources"],
        "mod_funded": true/false/null,
        "mod_funding_partial": true/false/null
      },
      "containment": {
        "has_bsl4": true/false,
        "bsl4_units": int or null,
        "bsl4_area_m2": float or null,
        "has_bsl3": true/false,
        "bsl3_area_m2": float or null,
        "highest_containment": "BSL-4/BSL-3/BSL-2/unknown",
        "raw_text": "exact text from fields 5 and 6"
      },
      "agents": {
        "listed": ["agent1", "agent2"],
        "description_only": true/false,
        "redacted": true/false,
        "raw_text": "exact text from field 7"
      },
      "confidence": 0.0-1.0,
      "notes": "any issues"
    }
  ]
}

Rules:
- Absent or blank fields: use null
- Convert ft\u00b2 to m\u00b2 if needed (1 ft\u00b2 = 0.0929 m\u00b2), note in "notes"
- Unicode \u33a1 = m\u00b2
- US "Select Agents and Toxins" without specifics: set redacted to true
- Set confidence lower for garbled text, ambiguous fields, or non-standard structure
- Yes/No containment tables: set has_bsl3 or has_bsl4 to true ONLY when an explicit \
affirmative ("Yes", "YES", "Oui", "oui", or equivalent) appears in the answer cell. \
An empty cell, or a cell containing only the template text "yes / no", means null — \
do not infer from the row label alone
- Funding sources: split on conjunctions including English "and", Portuguese "e", and \
Spanish "y" when they join distinct organisation names; each organisation should be a \
separate entry in the sources list
- If Form A part 1(i) fields 1–7 are all blank/NA but part 1(ii) includes an "additional \
relevant information" section that lists named facilities (e.g. Bolivia and Mexico \
voluntarily list BSL-3 labs there), extract EACH named facility as a separate record \
using whatever name, address, and containment data appear in that section. Set \
has_bsl3=true and highest_containment="BSL-3" for each unless otherwise specified. \
Only if NO named facilities can be found anywhere in the document should you create \
one record with facility_name="[Unnamed facility]"
- Translation: if the document language is not English (indicated in the context header), \
translate all extracted field values to English. Set translated=true. Preserve the \
original non-English text in facility_name_original and responsible_organisation_original. \
If the document is already in English, set translated=false
- Some Russian/Ukrainian submissions use sub-field numbering (1.1. Наименование, 1.2. …, \
1.3. …, 1.4. …, 1.5. …, 1.6. …) instead of separate fields 1–7. Treat these sub-fields \
as equivalent to fields 1–6 of the standard template\
"""

SYSTEM_PROMPT_G = """\
You are extracting structured data from a BWC Confidence-Building Measure \
submission (Form G: Declaration of Vaccine Production Facilities).

The document contains one or more vaccine facility declarations. Each uses \
three numbered fields from the standard BWC Form G template.

Extract ALL facilities. Return ONLY valid JSON, no preamble:
{
  "vaccine_facilities": [
    {
      "facility_name": "string (in English)",
      "facility_name_original": "string — original non-English name, or null if already English",
      "translated": true/false,
      "location": {
        "address": "string",
        "city": "string",
        "country_iso3": "3-letter ISO code",
        "full_text": "complete location text as given"
      },
      "diseases_covered": "free-text description of disease types",
      "vaccines": ["vaccine product name 1", "vaccine product name 2"],
      "confidence": 0.0-1.0,
      "notes": "any issues or observations"
    }
  ]
}

Rules:
- Absent or blank fields: use null
- vaccines: list individual product names; if only a general description is given with no \
named products, set vaccines to [] and put the description in diseases_covered
- If field 1 is blank (nothing to declare), return {"vaccine_facilities": []}
- Translation: if the document language is not English, translate field values to English. \
Set translated=true. Preserve original in facility_name_original. \
If already English, set translated=false\
"""


SYSTEM_PROMPT_F = """\
You are extracting structured data from a BWC Confidence-Building Measure \
submission (Form F: Declaration of Past Activities in Offensive and/or \
Defensive Biological R&D Programmes).

This form has three fields:
1. Date of entry into force of the Convention for the State Party
2. Past offensive biological R&D programme (yes/no, period, summary)
3. Past defensive biological R&D programme (yes/no, period, summary)

Return ONLY valid JSON, no preamble:
{
  "convention_entry_date": "YYYY-MM-DD or partial date string, or null",
  "has_offensive_programme": true/false/null,
  "offensive_period": "string describing the period(s) of activity, or null",
  "offensive_summary": "brief summary of offensive activities, or null",
  "has_defensive_programme": true/false/null,
  "defensive_period": "string describing the period(s) of activity, or null",
  "defensive_summary": "1-3 sentence summary of defensive programme, or null",
  "translated": true/false,
  "confidence": 0.0-1.0,
  "notes": "any issues or observations, or null"
}

Rules:
- has_offensive_programme / has_defensive_programme: true if the State Party \
declares it conducted such a programme; false if explicitly no; null if unclear
- "Nothing to declare" / "Rien à déclarer" / "Нет" → set both fields to false, \
summaries to null
- "Nothing new to declare" (USA offensive) → has_offensive_programme false, \
summary "Nothing new to declare"
- Summaries: keep brief (2-3 sentences max); omit publication lists and \
lengthy historical narration
- convention_entry_date: prefer ISO format; partial dates like "1975" are fine
- Translation: if document language is not English, translate field values; \
set translated=true\
"""


SYSTEM_PROMPT_E = """\
You are extracting structured data from a BWC Confidence-Building Measure \
submission (Form E: Declaration of Legislation, Regulations and Other Measures).

Form E contains a table with four rows and four columns:
  Rows (categories):
    (a) Prohibitions — development, production, stockpiling, acquisition/retention \
(Article I)
    (b) Exports of micro-organisms and toxins
    (c) Imports of micro-organisms and toxins
    (d) Biosafety and biosecurity
  Columns: Legislation | Regulations | Other measures | Amended since last year

After the table, many submissions list specific laws by name.

Return ONLY valid JSON, no preamble:
{
  "categories": {
    "prohibitions": {
      "legislation": true/false/null,
      "regulations": true/false/null,
      "other_measures": true/false/null,
      "amended": true/false/null
    },
    "exports": {
      "legislation": true/false/null,
      "regulations": true/false/null,
      "other_measures": true/false/null,
      "amended": true/false/null
    },
    "imports": {
      "legislation": true/false/null,
      "regulations": true/false/null,
      "other_measures": true/false/null,
      "amended": true/false/null
    },
    "biosafety": {
      "legislation": true/false/null,
      "regulations": true/false/null,
      "other_measures": true/false/null,
      "amended": true/false/null
    }
  },
  "key_laws": ["short name of law or regulation", ...],
  "translated": true/false,
  "confidence": 0.0-1.0,
  "notes": "any issues or observations, or null"
}

Rules:
- categories: read directly from the Yes/No table; use null if a cell is blank or \
illegible
- IMPORTANT — "Yes/No" template issue: some countries submit the form with typographic \
emphasis (underline, bold) to mark their selections; plain text extraction loses this \
formatting and the table appears to show only the unmodified template text "Yes/No" in \
every cell. When the table shows only this template text without any filled-in responses, \
do NOT default all values to null. Instead infer correct values from the surrounding \
narrative text and listed legislation: \
  (1) legislation=true for a category if a specific law or act addresses it; \
  (2) regulations=true if a government decree or regulation addresses it; \
  (3) other_measures=true if a standard, guideline, or other non-legislative measure does; \
  (4) category mapping: BWC/biological weapons prohibition laws → prohibitions; \
      export/import controls, dual-use trade laws → exports AND imports (unless context \
      indicates only one applies); biosafety standards, infectious disease laws, \
      biosecurity guidance → biosafety; \
  (5) amended=true only if text explicitly mentions a recent change or new amendment; \
  (6) if narrative sections are explicitly labelled "(a)", "(b)", "(c)", "(d)" matching \
      the form rows, use those labels directly; otherwise infer from keywords; \
  (7) if the narrative describes a comprehensive framework covering all four categories, \
      set all applicable fields to true; \
  (8) only leave a field null if genuinely no indication exists in either the table or narrative
- "Nothing to declare" → set all category fields to null, key_laws to []
- key_laws: list the short names (e.g. "Penal Code Art. 140-1", \
"Biosafety Act 2015") — omit URLs, omit the BWC itself
- Keep key_laws to the most specific implementing legislation (max ~15 entries); \
skip generic constitutional provisions unless they are the sole measure
- Translation: if the document is not in English, translate all string values; \
set translated=true\
"""

SYSTEM_PROMPT_E_VISION = """\
You are extracting structured data from a BWC Confidence-Building Measure \
submission (Form E: Declaration of Legislation, Regulations and Other Measures).

You are given one or more PAGE IMAGES from the original PDF.  Read the \
Form E table directly from the image.

The table has four rows and four columns:
  Rows (categories):
    (a) Prohibitions — development, production, stockpiling, acquisition/retention \
(Article I)
    (b) Exports of micro-organisms and toxins
    (c) Imports of micro-organisms and toxins
    (d) Biosafety and biosecurity
  Columns: Legislation | Regulations | Other measures | Amended since last year

Each cell should contain Yes or No.  Countries mark their selection in \
different ways:
  - STRIKETHROUGH on the rejected option (a thin horizontal line drawn \
through "Yes" or "No" — look very carefully, the line may be faint)
  - Bold or underline on the selected option
  - Circling or handwritten marks
The selected value is the one WITHOUT strikethrough / the one WITH \
bold/underline/circle.  Examine each cell closely for any visual \
difference between "Yes" and "No".

If the table shows only template text "Yes/No" with NO visual formatting \
at all (both options appear identical), infer values from any surrounding \
narrative text listing specific laws or regulations:
  (1) legislation=true for a category if a specific law or act addresses it; \
  (2) regulations=true if a government decree or regulation addresses it; \
  (3) other_measures=true if a standard, guideline, or other non-legislative \
measure does; \
  (4) category mapping: BWC/biological weapons prohibition laws → prohibitions; \
      export/import controls, dual-use trade laws → exports AND imports; \
      biosafety standards, infectious disease laws → biosafety; \
  (5) amended=true only if text explicitly mentions a recent amendment; \
  (6) if narrative sections are explicitly labelled "(a)", "(b)", "(c)", "(d)" \
      matching the form rows, use those labels directly; otherwise infer from \
      keywords; \
  (7) if the narrative describes a comprehensive framework covering all four \
      categories, set all applicable fields to true; \
  (8) only leave a field null if genuinely no indication exists in either the \
      table or narrative

Return ONLY valid JSON, no preamble:
{
  "categories": {
    "prohibitions": {
      "legislation": true/false/null,
      "regulations": true/false/null,
      "other_measures": true/false/null,
      "amended": true/false/null
    },
    "exports": { ... same fields ... },
    "imports": { ... same fields ... },
    "biosafety": { ... same fields ... }
  },
  "key_laws": ["short name of law or regulation", ...],
  "translated": true/false,
  "confidence": 0.0-1.0,
  "notes": "any issues or observations, or null"
}

Additional rules:
- "Nothing to declare" → all category fields null, key_laws []
- key_laws: list short names (max ~15 entries); omit URLs and the BWC itself
- Translation: if the document is not in English, translate all strings; \
set translated=true\
"""


SYSTEM_PROMPT_B = """\
Extract disease outbreak reports from this BWC CBM Form B. Return JSON array of objects with: \
pathogen (string), location (string), date_range (string), cases_estimate (string), \
deaths_estimate (string), suspected_source (string), notes (string)
If no outbreaks are declared or section says nothing to declare, return [].

Return ONLY valid JSON, no preamble. Example:
[{"pathogen": "...", "location": "...", "date_range": "...", "cases_estimate": "...",
  "deaths_estimate": "...", "suspected_source": "...", "notes": "..."}]
\
"""


# FEATURE 9: End of SYSTEM_PROMPT_B

SYSTEM_PROMPT_A2 = """\
You are extracting structured data from a BWC Confidence-Building Measure \
submission (Form A, Part 2: National Biological Defence R&D Programmes).

This form has three sub-sections:
- Part 2(i): Declaration — "Are there any national programmes?" (Yes/No)
- Part 2(ii): Programme description — objectives, funding, research areas (one per programme)
- Part 2(iii): Facility declaration — name, location, lab areas, personnel, funding (one per facility)

A chunk may contain any combination of these sub-sections. Extract whatever is present.
Return ONLY valid JSON, no preamble:
{
  "has_programme_declared": true/false/null,
  "defence_programmes": [
    {
      "programme_name": "string — infer from section heading above '1. State the objectives'",
      "responsible_org": "string or null",
      "objectives_summary": "1-3 sentence summary of programme objectives and scope",
      "research_areas": ["list from: prophylaxis, pathogenicity_virulence, diagnostics, aerobiology, detection, treatment, toxinology, physical_protection, decontamination, other"],
      "total_funding_amount": number or null,
      "total_funding_currency": "ISO currency code or null",
      "uses_contractors": true/false/null,
      "contractor_proportion_pct": number or null,
      "translated": true/false,
      "confidence": 0.0-1.0
    }
  ],
  "defence_facilities": [
    {
      "facility_name": "string (in English)",
      "facility_name_original": "string — original non-English name, or null if already English",
      "translated": true/false,
      "location": {
        "address": "string",
        "city": "string",
        "country_iso3": "3-letter ISO code",
        "full_text": "complete location text as given"
      },
      "bsl2_area_m2": number or null,
      "bsl3_area_m2": number or null,
      "bsl4_area_m2": number or null,
      "total_lab_area_m2": number or null,
      "personnel_total": integer or null,
      "personnel_military": integer or null,
      "personnel_civilian": integer or null,
      "personnel_scientists": integer or null,
      "personnel_engineers": integer or null,
      "personnel_technicians": integer or null,
      "personnel_admin": integer or null,
      "mod_funded": true/false/null,
      "funding_source": "string or null",
      "funding_research": number or null,
      "funding_development": number or null,
      "funding_te": number or null,
      "funding_currency": "ISO currency code or null",
      "work_description": "1-2 sentence summary of biological defence work at this facility",
      "confidence": 0.0-1.0
    }
  ]
}

Rules:
- has_programme_declared: set true/false only if Part 2(i) is present in this chunk; null otherwise
- defence_programmes: [] if no Part 2(ii) sections present
- defence_facilities: [] if no Part 2(iii) sections present
- Absent/blank fields: null; "N/A" or "Not applicable" → null
- Funding amounts: always store as full number (e.g. "£55 M" → 55000000, "3,198,752" → 3198752)
- Currency: use ISO codes (USD, GBP, EUR, CAD, AUD, DKK, SEK, NOK, CHF, etc.)
- Research areas: infer from objectives text; choose from the allowed list only
- Lab areas: convert ft² to m² if needed (1 ft² = 0.0929 m²), note in confidence
- Personnel "N/A" or blank for a category → null (do not infer)
- Translation: if document language is not English, translate field values to English; set translated=true; preserve original facility name in facility_name_original
- In some French submissions, Part 2(iii) field 1 contains the facility name directly (no question label)
- Confidence: lower for free-form narrative, ambiguous fields, or very long programme descriptions where key data may be buried\
"""


# ── Chunking ──────────────────────────────────────────────────────────────────


def split_into_chunks(text: str, max_chars: int = CHUNK_MAX_CHARS) -> list[str]:
    """
    Split form_a1 text into chunks at facility boundaries.

    Text before the first "1. Name" (preamble / section header) is discarded;
    only the facility entries are sent to the API.
    Each chunk is a concatenation of consecutive facility texts whose total
    length does not exceed max_chars.
    """
    positions = [m.start() for m in FACILITY_RE.finditer(text)]

    if not positions:
        # No facility boundaries: return the whole text trimmed of page markers
        stripped = text.strip()
        return [stripped] if stripped else []

    # Individual facility texts (from "1. Name…" to the next "1. Name…")
    facility_texts: list[str] = []
    for i, pos in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(text)
        facility_texts.append(text[pos:end].strip())

    # Group into chunks ≤ max_chars
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for fac in facility_texts:
        if current_len + len(fac) > max_chars and current:
            chunks.append("\n\n".join(current))
            current = [fac]
            current_len = len(fac)
        else:
            current.append(fac)
            current_len += len(fac)

    if current:
        chunks.append("\n\n".join(current))

    return chunks


def _truncate_a2_section(section: str, is_programme: bool) -> str:
    """
    Remove high-volume, low-value text from a Form A Part 2 section before
    sending it to Claude, dramatically reducing input tokens.

    For Part 2(ii) programme sections:
      Field 1 (objectives) is often 5-10 pages of narrative. We keep at most
      FORM_A2_PROG_FIELD1_MAX chars before field 2, which holds all the
      structured data we actually extract (funding, contractors, etc.).

    For Part 2(iii) facility sections:
      Field 4(viii)/(ix) is a publications list — potentially 50 KB of citations
      that we never extract. We truncate there and re-attach a capped version of
      field 5 (work description) if present.
    """
    if is_programme:
        m = FORM_A2_PROG_FIELD2_RE.search(section)
        if m and m.start() > FORM_A2_PROG_FIELD1_MAX:
            section = (
                section[:FORM_A2_PROG_FIELD1_MAX]
                + "\n[...objectives truncated...]\n\n"
                + section[m.start():]
            )
    else:
        m = FORM_A2_FAC_PUBLIST_RE.search(section)
        if m:
            truncated = section[:m.start()]
            m5 = FORM_A2_FAC_FIELD5_RE.search(section)
            if m5:
                field5 = section[m5.start():m5.start() + FORM_A2_FAC_FIELD5_MAX]
                truncated += "\n\n" + field5
            section = truncated
    return section


def split_into_chunks_a2(text: str) -> list[str]:
    """
    Split form_a2 text at programme (2ii) and facility (2iii) boundaries.

    Includes a lookback window before each programme section to capture the
    programme name heading that appears before "1. State the objectives".

    For non-English docs (no English boundaries found) falls back to char-split.
    Each resulting section is sent as its own chunk regardless of size; multiple
    small adjacent sections are grouped up to FORM_A2_CHUNK_MAX_CHARS.
    """
    prog_positions = [m.start() for m in FORM_A2_PROG_RE.finditer(text)]
    fac_positions  = [m.start() for m in FORM_A2_FAC_RE.finditer(text)]

    if not prog_positions and not fac_positions:
        # Non-English or unusual format: char-split
        stripped = text.strip()
        if not stripped:
            return []
        return [
            stripped[i:i + FORM_A2_CHUNK_MAX_CHARS]
            for i in range(0, len(stripped), FORM_A2_CHUNK_MAX_CHARS)
        ]

    # Build adjusted section start positions
    # Programme sections: include lookback to capture the programme name heading
    # Facility sections: start at the boundary itself
    adj_positions: list[int] = []
    for pos in prog_positions:
        adj_positions.append(max(0, pos - FORM_A2_PROG_LOOKBACK))
    for pos in fac_positions:
        adj_positions.append(pos)
    adj_positions = sorted(set(adj_positions))

    # Preamble (text before first section, contains Part 2(i) declaration)
    first_start = adj_positions[0]
    preamble = text[:first_start].strip()

    # Build sections, truncating boilerplate before chunking
    prog_pos_set = set(max(0, pos - FORM_A2_PROG_LOOKBACK) for pos in prog_positions)
    sections: list[str] = []
    for i, start in enumerate(adj_positions):
        end = adj_positions[i + 1] if i + 1 < len(adj_positions) else len(text)
        sec = text[start:end].strip()
        is_programme = start in prog_pos_set
        sections.append(_truncate_a2_section(sec, is_programme))

    # Chunk groups of sections that together fit within FORM_A2_CHUNK_MAX_CHARS.
    # A section larger than the limit is still sent as a single chunk.
    chunks: list[str] = []

    if preamble:
        # Trim preamble to last FORM_A2_CHUNK_MAX_CHARS (retains the declaration)
        chunks.append(
            preamble[-FORM_A2_CHUNK_MAX_CHARS:]
            if len(preamble) > FORM_A2_CHUNK_MAX_CHARS
            else preamble
        )

    current: list[str] = []
    current_len = 0
    for sec in sections:
        if current_len + len(sec) > FORM_A2_CHUNK_MAX_CHARS and current:
            chunks.append("\n\n".join(current))
            current = [sec]
            current_len = len(sec)
        else:
            current.append(sec)
            current_len += len(sec)
    if current:
        chunks.append("\n\n".join(current))

    return chunks


# ── JSON parsing ──────────────────────────────────────────────────────────────


def parse_json_response(text: str) -> dict | None:
    """Try several strategies to extract a JSON object from a Claude response."""
    # 1. Direct parse
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # 2. Inside a markdown code block
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 3. Outermost { … }
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass

    # 4. Repair doubled closing quotes.
    # Claude sometimes writes U+201D (curly quote from source) then also an extra ASCII "
    # producing e.g. `НАМН Украины""` — first " closes the JSON string, second is invalid.
    # Fix: replace "" that follow a non-structural character with a single ".
    if m:
        try:
            repaired = re.sub(r'(?<=[^\s:,\[\{])""', '"', m.group())
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    return None


# ── API call with rate-limiting and one retry ────────────────────────────────


def api_call(
    client: anthropic.Anthropic,
    messages: list[dict],
    last_t: list[float],
    system: str = SYSTEM_PROMPT,
) -> tuple[anthropic.types.Message, float]:
    """Fire one API call, honouring the rate limit. Returns (message, call_time)."""
    wait = RATE_LIMIT_DELAY - (time.time() - last_t[0])
    if wait > 0:
        time.sleep(wait)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=messages,
    )
    last_t[0] = time.time()
    return resp


def call_and_parse(
    client: anthropic.Anthropic,
    messages: list[dict],
    last_t: list[float],
    entry_id: str,
    system: str = SYSTEM_PROMPT,
    form_tag: str = "",
) -> tuple[dict | None, dict]:
    """
    Call the API, parse the JSON response, retry once if not valid JSON.
    Returns (parsed_data_or_None, usage_dict).
    """
    resp = api_call(client, messages, last_t, system=system)
    raw = resp.content[0].text
    usage = {
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
    }
    data = parse_json_response(raw)
    if data is not None:
        return data, usage

    tag = f"/{form_tag}" if form_tag else ""
    log.warning("[%s%s] Response not valid JSON — retrying once", entry_id, tag)
    messages += [
        {"role": "assistant", "content": raw},
        {"role": "user", "content":
         "Your response was not valid JSON. "
         "Return ONLY the JSON object — no markdown fences, no explanation."},
    ]
    resp2 = api_call(client, messages, last_t, system=system)
    raw2 = resp2.content[0].text
    usage["input_tokens"]  += resp2.usage.input_tokens
    usage["output_tokens"] += resp2.usage.output_tokens
    data = parse_json_response(raw2)
    if data is None:
        log.error("[%s%s] Retry also returned invalid JSON", entry_id, tag)
    return data, usage


def extract_chunk(
    client: anthropic.Anthropic,
    chunk_text: str,
    entry: dict,
    last_t: list[float],
) -> tuple[list[dict], dict]:
    """
    Send one chunk to Claude and return (facilities, usage).
    Retries once if the response is not valid JSON.
    """
    user_content = (
        f"State Party: {entry['country']} ({entry['country_iso3']})\n"
        f"Year: {entry['year']}\n"
        f"Document language: {entry.get('language', 'en')}\n\n"
        f"{chunk_text}"
    )
    messages: list[dict] = [{"role": "user", "content": user_content}]
    data, usage = call_and_parse(client, messages, last_t, entry["id"])
    log.debug("[%s] tokens in=%d out=%d", entry["id"],
              usage["input_tokens"], usage["output_tokens"])
    if data is None:
        return [], usage
    return data.get("facilities", []), usage


# ── Per-document processing ───────────────────────────────────────────────────


def process_entry(
    entry: dict,
    client: anthropic.Anthropic,
    last_t: list[float],
) -> dict:
    """
    Extract facilities from one document.
    Returns a result dict (status, counts, token usage).
    """
    entry_id   = entry["id"]
    form_path  = SEGMENTED_DIR / entry_id / "form_a1.txt"
    out_path   = STRUCTURED_DIR / f"{entry_id}_form_a1.json"

    if not form_path.exists():
        return {"id": entry_id, "status": "no_form_a1"}

    if out_path.exists():
        return {"id": entry_id, "status": "skipped"}

    text   = form_path.read_text(encoding="utf-8")
    chunks = split_into_chunks(text)

    if not chunks:
        log.info("[%s] form_a1 has no facility entries (NTD or empty)", entry_id)
        _write_output(out_path, entry, [], 0, {"input_tokens": 0, "output_tokens": 0})
        return {"id": entry_id, "status": "ok", "facilities": 0, "calls": 0,
                "input_tokens": 0, "output_tokens": 0}

    all_facilities: list[dict] = []
    total_usage = {"input_tokens": 0, "output_tokens": 0}

    for i, chunk in enumerate(chunks):
        log.debug("[%s] chunk %d/%d  (%d chars)", entry_id, i + 1, len(chunks), len(chunk))
        facilities, usage = extract_chunk(client, chunk, entry, last_t)

        for fac in facilities:
            fac["extraction_metadata"] = {
                "source_id":    entry_id,
                "country_iso3": entry["country_iso3"],
                "year":         entry["year"],
                "language":     entry.get("language", "en"),
                "chunk_index":  i,
            }

        all_facilities.extend(facilities)
        total_usage["input_tokens"]  += usage["input_tokens"]
        total_usage["output_tokens"] += usage["output_tokens"]

    _write_output(out_path, entry, all_facilities, len(chunks), total_usage)

    log.info(
        "[%s] %d facilities | %d chunk(s) | in=%d out=%d tokens",
        entry_id, len(all_facilities), len(chunks),
        total_usage["input_tokens"], total_usage["output_tokens"],
    )
    return {
        "id":            entry_id,
        "status":        "ok",
        "facilities":    len(all_facilities),
        "calls":         len(chunks),
        "input_tokens":  total_usage["input_tokens"],
        "output_tokens": total_usage["output_tokens"],
    }


def _write_output(
    out_path: Path,
    entry: dict,
    facilities: list[dict],
    n_calls: int,
    usage: dict,
) -> None:
    STRUCTURED_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "id":           entry["id"],
        "country":      entry["country"],
        "country_iso3": entry["country_iso3"],
        "year":         entry["year"],
        "total_api_calls": n_calls,
        "total_tokens": usage,
        "facility_count": len(facilities),
        "facilities":   facilities,
    }
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ── Form G extraction ────────────────────────────────────────────────────────


def extract_chunk_g(
    client: anthropic.Anthropic,
    chunk_text: str,
    entry: dict,
    last_t: list[float],
) -> tuple[list[dict], dict]:
    """Send one Form G chunk to Claude. Returns (vaccine_facilities, usage)."""
    user_content = (
        f"State Party: {entry['country']} ({entry['country_iso3']})\n"
        f"Year: {entry['year']}\n"
        f"Document language: {entry.get('language', 'en')}\n\n"
        f"{chunk_text}"
    )
    messages: list[dict] = [{"role": "user", "content": user_content}]
    data, usage = call_and_parse(client, messages, last_t, entry["id"],
                                 system=SYSTEM_PROMPT_G, form_tag="G")
    if data is None:
        return [], usage
    return data.get("vaccine_facilities", []), usage


def process_entry_g(
    entry: dict,
    client: anthropic.Anthropic,
    last_t: list[float],
) -> dict:
    """Extract vaccine facilities from Form G of one document."""
    entry_id  = entry["id"]
    form_path = SEGMENTED_DIR / entry_id / "form_g.txt"
    out_path  = STRUCTURED_DIR / f"{entry_id}_form_g.json"

    if not form_path.exists():
        return {"id": entry_id, "status": "no_form_g"}

    if out_path.exists():
        return {"id": entry_id, "status": "skipped"}

    text = form_path.read_text(encoding="utf-8")

    # Split at Form G facility boundaries
    positions = [m.start() for m in FORM_G_RE.finditer(text)]
    if not positions:
        log.info("[%s/G] form_g has no facility entries (NTD or empty)", entry_id)
        _write_output_g(out_path, entry, [], 0, {"input_tokens": 0, "output_tokens": 0})
        return {"id": entry_id, "status": "ok", "vaccine_facilities": 0, "calls": 0,
                "input_tokens": 0, "output_tokens": 0}

    # Build facility texts and chunks
    facility_texts: list[str] = []
    for i, pos in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(text)
        facility_texts.append(text[pos:end].strip())

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for fac in facility_texts:
        if current_len + len(fac) > CHUNK_MAX_CHARS and current:
            chunks.append("\n\n".join(current))
            current = [fac]
            current_len = len(fac)
        else:
            current.append(fac)
            current_len += len(fac)
    if current:
        chunks.append("\n\n".join(current))

    all_facilities: list[dict] = []
    total_usage = {"input_tokens": 0, "output_tokens": 0}

    for i, chunk in enumerate(chunks):
        facilities, usage = extract_chunk_g(client, chunk, entry, last_t)

        for fac in facilities:
            fac["extraction_metadata"] = {
                "source_id":    entry_id,
                "country_iso3": entry["country_iso3"],
                "year":         entry["year"],
                "language":     entry.get("language", "en"),
                "chunk_index":  i,
            }

        all_facilities.extend(facilities)
        total_usage["input_tokens"]  += usage["input_tokens"]
        total_usage["output_tokens"] += usage["output_tokens"]

    _write_output_g(out_path, entry, all_facilities, len(chunks), total_usage)

    log.info(
        "[%s/G] %d vaccine facilities | %d chunk(s) | in=%d out=%d tokens",
        entry_id, len(all_facilities), len(chunks),
        total_usage["input_tokens"], total_usage["output_tokens"],
    )
    return {
        "id":                entry_id,
        "status":            "ok",
        "vaccine_facilities": len(all_facilities),
        "calls":             len(chunks),
        "input_tokens":      total_usage["input_tokens"],
        "output_tokens":     total_usage["output_tokens"],
    }


def _write_output_g(
    out_path: Path,
    entry: dict,
    facilities: list[dict],
    n_calls: int,
    usage: dict,
) -> None:
    STRUCTURED_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "id":                entry["id"],
        "country":           entry["country"],
        "country_iso3":      entry["country_iso3"],
        "year":              entry["year"],
        "total_api_calls":   n_calls,
        "total_tokens":      usage,
        "vaccine_facility_count": len(facilities),
        "vaccine_facilities": facilities,
    }
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ── Form A Part 2 extraction ──────────────────────────────────────────────────


def extract_chunk_a2(
    client: anthropic.Anthropic,
    chunk_text: str,
    entry: dict,
    last_t: list[float],
) -> tuple[dict, dict]:
    """
    Send one Form A Part 2 chunk to Claude.
    Returns (result_dict, usage) where result_dict has keys:
        has_programme_declared, defence_programmes, defence_facilities
    """
    user_content = (
        f"State Party: {entry['country']} ({entry['country_iso3']})\n"
        f"Year: {entry['year']}\n"
        f"Document language: {entry.get('language', 'en')}\n\n"
        f"{chunk_text}"
    )
    messages: list[dict] = [{"role": "user", "content": user_content}]
    data, usage = call_and_parse(client, messages, last_t, entry["id"],
                                 system=SYSTEM_PROMPT_A2, form_tag="A2")
    if data is None:
        return {"has_programme_declared": None, "defence_programmes": [], "defence_facilities": []}, usage
    return {
        "has_programme_declared": data.get("has_programme_declared"),
        "defence_programmes":     data.get("defence_programmes") or [],
        "defence_facilities":     data.get("defence_facilities") or [],
    }, usage


def process_entry_a2(
    entry: dict,
    client: anthropic.Anthropic,
    last_t: list[float],
) -> dict:
    """Extract defence programmes and facilities from Form A Part 2 of one document."""
    entry_id  = entry["id"]
    form_path = SEGMENTED_DIR / entry_id / "form_a2.txt"
    out_path  = STRUCTURED_DIR / f"{entry_id}_form_a2.json"

    if not form_path.exists():
        return {"id": entry_id, "status": "no_form_a2"}

    if out_path.exists():
        return {"id": entry_id, "status": "skipped"}

    text   = form_path.read_text(encoding="utf-8")
    chunks = split_into_chunks_a2(text)

    if not chunks:
        _write_output_a2(out_path, entry, None, [], [], 0, {"input_tokens": 0, "output_tokens": 0})
        return {"id": entry_id, "status": "ok", "programmes": 0, "facilities": 0, "calls": 0,
                "input_tokens": 0, "output_tokens": 0}

    has_prog: bool | None   = None
    all_programmes: list[dict] = []
    all_facilities: list[dict] = []
    total_usage = {"input_tokens": 0, "output_tokens": 0}

    for i, chunk in enumerate(chunks):
        log.debug("[%s/A2] chunk %d/%d (%d chars)", entry_id, i + 1, len(chunks), len(chunk))
        result, usage = extract_chunk_a2(client, chunk, entry, last_t)

        if has_prog is None and result["has_programme_declared"] is not None:
            has_prog = result["has_programme_declared"]

        for prog in result["defence_programmes"]:
            prog["extraction_metadata"] = {
                "source_id":    entry_id,
                "country_iso3": entry["country_iso3"],
                "year":         entry["year"],
                "language":     entry.get("language", "en"),
                "chunk_index":  i,
            }
        for fac in result["defence_facilities"]:
            fac["extraction_metadata"] = {
                "source_id":    entry_id,
                "country_iso3": entry["country_iso3"],
                "year":         entry["year"],
                "language":     entry.get("language", "en"),
                "chunk_index":  i,
            }

        all_programmes.extend(result["defence_programmes"])
        all_facilities.extend(result["defence_facilities"])
        total_usage["input_tokens"]  += usage["input_tokens"]
        total_usage["output_tokens"] += usage["output_tokens"]

    _write_output_a2(out_path, entry, has_prog, all_programmes, all_facilities,
                     len(chunks), total_usage)

    log.info(
        "[%s/A2] declared=%s | %d programmes | %d facilities | %d chunk(s) | in=%d out=%d tokens",
        entry_id, has_prog, len(all_programmes), len(all_facilities), len(chunks),
        total_usage["input_tokens"], total_usage["output_tokens"],
    )
    return {
        "id":             entry_id,
        "status":         "ok",
        "programmes":     len(all_programmes),
        "facilities":     len(all_facilities),
        "calls":          len(chunks),
        "input_tokens":   total_usage["input_tokens"],
        "output_tokens":  total_usage["output_tokens"],
    }


def _write_output_a2(
    out_path: Path,
    entry: dict,
    has_programme_declared: bool | None,
    programmes: list[dict],
    facilities: list[dict],
    n_calls: int,
    usage: dict,
) -> None:
    STRUCTURED_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "id":                      entry["id"],
        "country":                 entry["country"],
        "country_iso3":            entry["country_iso3"],
        "year":                    entry["year"],
        "has_programme_declared":  has_programme_declared,
        "total_api_calls":         n_calls,
        "total_tokens":            usage,
        "programme_count":         len(programmes),
        "facility_count":          len(facilities),
        "defence_programmes":      programmes,
        "defence_facilities":      facilities,
    }
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ── Form F extraction ────────────────────────────────────────────────────────


def process_entry_f(
    entry: dict,
    client: anthropic.Anthropic,
    last_t: list[float],
) -> dict:
    """Extract past programme declaration from Form F of one document."""
    entry_id  = entry["id"]
    form_path = SEGMENTED_DIR / entry_id / "form_f.txt"
    out_path  = STRUCTURED_DIR / f"{entry_id}_form_f.json"

    if not form_path.exists():
        return {"id": entry_id, "status": "no_form_f"}

    if out_path.exists():
        return {"id": entry_id, "status": "skipped"}

    text = form_path.read_text(encoding="utf-8").strip()
    if not text:
        _write_output_f(out_path, entry, {}, {"input_tokens": 0, "output_tokens": 0})
        return {"id": entry_id, "status": "ok", "calls": 0,
                "input_tokens": 0, "output_tokens": 0}

    user_content = (
        f"State Party: {entry['country']} ({entry['country_iso3']})\n"
        f"Year: {entry['year']}\n"
        f"Document language: {entry.get('language', 'en')}\n\n"
        f"{text}"
    )
    messages: list[dict] = [{"role": "user", "content": user_content}]
    data, usage = call_and_parse(client, messages, last_t, entry_id,
                                 system=SYSTEM_PROMPT_F, form_tag="F")
    if data is None:
        data = {}

    data["extraction_metadata"] = {
        "source_id":    entry_id,
        "country_iso3": entry["country_iso3"],
        "year":         entry["year"],
        "language":     entry.get("language", "en"),
    }

    _write_output_f(out_path, entry, data, usage)

    log.info(
        "[%s/F] off=%s def=%s | in=%d out=%d tokens",
        entry_id,
        data.get("has_offensive_programme"),
        data.get("has_defensive_programme"),
        usage["input_tokens"], usage["output_tokens"],
    )
    return {
        "id":           entry_id,
        "status":       "ok",
        "calls":        1,
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
    }


def _write_output_f(
    out_path: Path,
    entry: dict,
    declaration: dict,
    usage: dict,
) -> None:
    STRUCTURED_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "id":           entry["id"],
        "country":      entry["country"],
        "country_iso3": entry["country_iso3"],
        "year":         entry["year"],
        "total_api_calls": 1,
        "total_tokens": usage,
        **declaration,
    }
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def process_entry_e(
    entry: dict, client, last_t: list[float],
    max_chars: int | None = FORM_E_MAX_CHARS,
) -> dict:
    """Extract Form E (legislation) for one document.

    Three extraction tiers, tried in order:
      1. pdfplumber geometry — detects strikethrough/underline rects that
         overlay Yes/No text in the Form E table.  Most reliable for PDFs
         that use formatting marks invisible in extracted text.
      2. Claude Vision — renders pages as images; can read bold/circled
         formatting that pdfplumber cannot detect.
      3. Text-only — narrative inference from extracted text when no PDF
         is available or no formatting is detectable.
    """
    entry_id  = entry["id"]
    form_path = SEGMENTED_DIR / entry_id / "form_e.txt"
    out_path  = STRUCTURED_DIR / f"{entry_id}_form_e.json"

    if out_path.exists():
        return {"id": entry_id, "status": "skipped"}

    if not form_path.exists():
        return {"id": entry_id, "status": "no_form_e"}

    text = form_path.read_text(encoding="utf-8")
    original_len = len(text)

    mode = "text"  # default; upgraded if a higher tier succeeds
    pdf_path = RAW_PDFS_DIR / f"{entry_id}.pdf"
    page_nums = _form_e_page_numbers(form_path) if pdf_path.exists() else []
    if len(page_nums) > MAX_VISION_PAGES:
        log.warning("[%s/E] %d pages exceeds limit, using first %d",
                    entry_id, len(page_nums), MAX_VISION_PAGES)
        page_nums = page_nums[:MAX_VISION_PAGES]

    # ── Tier 1: pdfplumber formatting detection ──────────────────────
    if page_nums:
        try:
            annotation = _detect_form_e_formatting(pdf_path, page_nums)
        except Exception as exc:
            log.warning("[%s/E] pdfplumber detection failed: %s",
                        entry_id, exc)
            annotation = None

        if annotation:
            messages = [{"role": "user", "content": annotation + text}]
            system = SYSTEM_PROMPT_E
            mode = "pdfplumber"

    # ── Tier 2: Claude Vision ────────────────────────────────────────
    if mode == "text" and page_nums:
        try:
            image_blocks = _render_pages_as_base64(pdf_path, page_nums)
            content: list[dict] = image_blocks + [
                {"type": "text", "text": (
                    f"State Party: {entry['country']} ({entry['country_iso3']})\n"
                    f"Year: {entry['year']}\n"
                    f"Document language: {entry.get('language', 'en')}\n\n"
                    "Extract the Form E legislation table from the page "
                    "image(s) above."
                )},
            ]
            messages = [{"role": "user", "content": content}]
            system = SYSTEM_PROMPT_E_VISION
            mode = "vision"
        except Exception as exc:
            log.warning("[%s/E] Vision rendering failed, falling back to "
                        "text: %s", entry_id, exc)

    # ── Tier 3: text-only extraction ─────────────────────────────────
    if mode == "text":
        if max_chars and original_len > max_chars:
            text = text[:max_chars]
        messages = [{"role": "user", "content": text}]
        system = SYSTEM_PROMPT_E

    try:
        data, usage = call_and_parse(client, messages, last_t, entry_id,
                                     system=system, form_tag="E")
    except Exception as exc:
        log.error("[%s/E] API error: %s", entry_id, exc)
        return {"id": entry_id, "status": "error", "error": str(exc)}
    if data is None:
        return {"id": entry_id, "status": "error", "calls": 2, **usage}

    cats = data.get("categories") or {}
    log.info("[%s/E] (%s) prohibitions=%s exports=%s | in=%d out=%d tokens",
             entry_id, mode,
             (cats.get("prohibitions") or {}).get("legislation"),
             (cats.get("exports") or {}).get("legislation"),
             usage["input_tokens"], usage["output_tokens"])

    truncated = (mode == "text" and max_chars is not None
                 and original_len > (max_chars or 0))
    _write_output_e(out_path, entry, data, usage, truncated=truncated,
                    extraction_mode=mode)
    return {"id": entry_id, "status": "ok", "calls": 1, **usage}


def _write_output_e(
    out_path: Path,
    entry: dict,
    declaration: dict,
    usage: dict,
    truncated: bool = False,
    extraction_mode: str = "text",
) -> None:
    STRUCTURED_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "id":           entry["id"],
        "country":      entry["country"],
        "country_iso3": entry["country_iso3"],
        "year":         entry["year"],
        "total_api_calls": 1,
        "total_tokens": usage,
        "input_truncated": truncated,
        **declaration,
        "extraction_metadata": {
            "source_id":       entry["id"],
            "country_iso3":    entry["country_iso3"],
            "year":            entry["year"],
            "language":        entry.get("language", "en"),
            "extraction_mode": extraction_mode,
        },
    }
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def process_entry_b(
    entry: dict,
    client: anthropic.Anthropic,
    last_t: list[float],
) -> dict:
    """
    FEATURE 9: Extract outbreak declarations from Form B of one document.
    Output goes to data/structured/{id}_form_b.json.
    """
    entry_id  = entry["id"]
    form_path = SEGMENTED_DIR / entry_id / "form_b.txt"
    out_path  = STRUCTURED_DIR / f"{entry_id}_form_b.json"

    if not form_path.exists():
        return {"id": entry_id, "status": "no_form_b"}

    if out_path.exists():
        return {"id": entry_id, "status": "skipped"}

    text = form_path.read_text(encoding="utf-8").strip()
    if not text:
        _write_output_b(out_path, entry, [], {"input_tokens": 0, "output_tokens": 0})
        return {"id": entry_id, "status": "ok", "outbreaks": 0, "calls": 0,
                "input_tokens": 0, "output_tokens": 0}

    user_content = (
        f"State Party: {entry['country']} ({entry['country_iso3']})\n"
        f"Year: {entry['year']}\n"
        f"Document language: {entry.get('language', 'en')}\n\n"
        f"{text}"
    )
    messages: list[dict] = [{"role": "user", "content": user_content}]

    resp = api_call(client, messages, last_t, system=SYSTEM_PROMPT_B)
    raw  = resp.content[0].text.strip()
    usage = {
        "input_tokens":  resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
    }

    # Parse JSON array response
    outbreaks: list[dict] = []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            outbreaks = data
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", raw, re.S)
        if m:
            try:
                data = json.loads(m.group())
                if isinstance(data, list):
                    outbreaks = data
            except json.JSONDecodeError:
                pass

    if not outbreaks and raw:
        log.warning("[%s/B] Could not parse response as JSON array — retrying once", entry_id)
        messages += [
            {"role": "assistant", "content": raw},
            {"role": "user", "content":
             "Your response was not a valid JSON array. "
             "Return ONLY a JSON array — no markdown fences, no explanation."},
        ]
        resp2 = api_call(client, messages, last_t, system=SYSTEM_PROMPT_B)
        raw2  = resp2.content[0].text.strip()
        usage["input_tokens"]  += resp2.usage.input_tokens
        usage["output_tokens"] += resp2.usage.output_tokens
        try:
            data2 = json.loads(raw2)
            if isinstance(data2, list):
                outbreaks = data2
        except json.JSONDecodeError:
            log.error("[%s/B] Retry also returned invalid JSON", entry_id)

    _write_output_b(out_path, entry, outbreaks, usage)

    log.info(
        "[%s/B] %d outbreaks | in=%d out=%d tokens",
        entry_id, len(outbreaks),
        usage["input_tokens"], usage["output_tokens"],
    )
    return {
        "id":           entry_id,
        "status":       "ok",
        "outbreaks":    len(outbreaks),
        "calls":        1,
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
    }


def _write_output_b(
    out_path: Path,
    entry: dict,
    outbreaks: list[dict],
    usage: dict,
) -> None:
    STRUCTURED_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "id":              entry["id"],
        "country":         entry["country"],
        "country_iso3":    entry["country_iso3"],
        "year":            entry["year"],
        "total_api_calls": 1,
        "total_tokens":    usage,
        "outbreak_count":  len(outbreaks),
        "outbreaks":       outbreaks,
    }
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ── Helpers ──────────────────────────────────────────────────────────────────


def _fmt_duration(seconds: float) -> str:
    """Format a duration in seconds as h:mm:ss or m:ss."""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s   = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--single", metavar="ID",
                        help="Process only this document id.")
    parser.add_argument("--form-g", action="store_true",
                        help="Extract Form G (vaccine facilities).")
    parser.add_argument("--form-a2", action="store_true",
                        help="Extract Form A Part 2 (national biological defence programmes).")
    parser.add_argument("--form-f", action="store_true",
                        help="Extract Form F (past offensive/defensive programmes).")
    parser.add_argument("--form-e", action="store_true",
                        help="Extract Form E (national biosafety/biosecurity legislation).")
    # FEATURE 9: Form B — disease outbreak declarations
    parser.add_argument("--form-b", action="store_true",
                        help="Extract Form B (disease outbreak declarations).")
    args = parser.parse_args()

    if sum([args.form_g, args.form_a2, args.form_f, args.form_e, args.form_b]) > 1:
        log.error("--form-g, --form-a2, --form-f, --form-e, and --form-b are mutually exclusive")
        raise SystemExit(1)

    if not CATALOGUE_PATH.exists():
        log.error("Catalogue not found: %s", CATALOGUE_PATH)
        raise SystemExit(1)

    catalogue: list[dict] = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))

    if args.form_a2:
        form_file = "form_a2.txt"
        suffix    = "_form_a2.json"
    elif args.form_g:
        form_file = "form_g.txt"
        suffix    = "_form_g.json"
    elif args.form_f:
        form_file = "form_f.txt"
        suffix    = "_form_f.json"
    elif args.form_e:
        form_file = "form_e.txt"
        suffix    = "_form_e.json"
    elif args.form_b:
        # FEATURE 9: Form B — disease outbreak declarations
        form_file = "form_b.txt"
        suffix    = "_form_b.json"
    else:
        form_file = "form_a1.txt"
        suffix    = "_form_a1.json"

    eligible = [
        e for e in catalogue
        if e.get("downloaded")
        and not e.get("needs_ocr")
        and not e.get("is_amendment")
        and (SEGMENTED_DIR / e["id"] / form_file).exists()
    ]

    if args.single:
        targets = [e for e in eligible if e["id"] == args.single]
        if not targets:
            log.error("No eligible entry with id=%r", args.single)
            raise SystemExit(1)
        log.info("--single mode: processing %s", args.single)
    else:
        targets = [
            e for e in eligible
            if not (STRUCTURED_DIR / f"{e['id']}{suffix}").exists()
        ]
        log.info("%d documents to process (%d already done)",
                 len(targets), len(eligible) - len(targets))

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY not set in .env")
        raise SystemExit(1)

    client = anthropic.Anthropic(api_key=api_key)
    last_t: list[float] = [0.0]

    n_ok = n_skipped = n_failed = 0
    total_items = total_calls = total_in = total_out = 0
    results: list[dict] = []
    n_total = len(targets)
    run_start = time.time()

    if args.form_a2:
        desc = "Extracting Form A Part 2"
    elif args.form_g:
        desc = "Extracting Form G"
    elif args.form_f:
        desc = "Extracting Form F"
    elif args.form_e:
        desc = "Extracting Form E"
    elif args.form_b:
        # FEATURE 9
        desc = "Extracting Form B"
    else:
        desc = "Extracting Form A Part 1"

    for entry in tqdm(targets, desc=desc, unit="doc", dynamic_ncols=True, file=sys.stderr):
        if args.form_a2:
            result = process_entry_a2(entry, client, last_t)
            item_key = None   # sum programmes + facilities
        elif args.form_g:
            result = process_entry_g(entry, client, last_t)
            item_key = "vaccine_facilities"
        elif args.form_f:
            result = process_entry_f(entry, client, last_t)
            item_key = None
        elif args.form_e:
            result = process_entry_e(entry, client, last_t)
            item_key = None
        elif args.form_b:
            # FEATURE 9: Form B dispatch
            result = process_entry_b(entry, client, last_t)
            item_key = "outbreaks"
        else:
            result = process_entry(entry, client, last_t)
            item_key = "facilities"
        results.append(result)

        status = result.get("status", "")
        if status == "ok":
            n_ok += 1
            if args.form_a2:
                total_items += result.get("programmes", 0) + result.get("facilities", 0)
            elif args.form_f or args.form_e:
                total_items += 1
            else:
                total_items += result.get(item_key, 0)
            total_calls += result.get("calls", 0)
            total_in    += result.get("input_tokens", 0)
            total_out   += result.get("output_tokens", 0)
        elif status == "skipped":
            n_skipped += 1
        else:
            n_failed += 1

        n_done = n_ok + n_skipped + n_failed
        if n_total > 1 and n_done % PROGRESS_INTERVAL == 0:
            elapsed  = time.time() - run_start
            rate     = elapsed / n_done          # seconds per doc
            remaining = rate * (n_total - n_done)
            cost_so_far = total_in * COST_PER_INPUT_TOKEN + total_out * COST_PER_OUTPUT_TOKEN
            log.info(
                "PROGRESS %d/%d (%.0f%%) — elapsed %s, remaining ~%s, %.1fs/doc, $%.2f spent",
                n_done, n_total, 100 * n_done / n_total,
                _fmt_duration(elapsed), _fmt_duration(remaining),
                rate, cost_so_far,
            )

    # ── Summary ───────────────────────────────────────────────────────────────
    item_label = ("programmes+facilities" if args.form_a2
                  else "declarations" if args.form_f
                  else "legislation records" if args.form_e
                  else "vaccine facilities" if args.form_g
                  else "facilities")
    print("\n── Summary ──────────────────────────────────────────────────")
    print(f"  Documents processed:  {n_ok}")
    if n_skipped:
        print(f"  Already done:         {n_skipped}")
    if n_failed:
        print(f"  Errors / no form:     {n_failed}")
    print(f"  Total {item_label}:  {total_items}")
    print(f"  Total API calls:      {total_calls}")
    print(f"  Input tokens:         {total_in:,}")
    print(f"  Output tokens:        {total_out:,}")
    cost = total_in * COST_PER_INPUT_TOKEN + total_out * COST_PER_OUTPUT_TOKEN
    print(f"  Est. cost (USD):      ${cost:.2f}")
    print("─────────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
