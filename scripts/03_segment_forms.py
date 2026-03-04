#!/usr/bin/env python3
"""
03_segment_forms.py — Segment extracted CBM text into individual form sections.

Reads .txt files from data/extracted_text/ and splits each document into its
constituent CBM forms (0, A1, A2, B, C, E, F, G).

Primary method: regex against the first line of each page.
Fallback method: Claude API (claude-sonnet-4-20250514) when regex finds < 2 forms.

Outputs per document (data/segmented/{id}/):
  form_0.txt, form_a1.txt, form_a2.txt, form_b.txt, form_c.txt,
  form_e.txt, form_f.txt, form_g.txt
  manifest.json

Usage:
    python scripts/03_segment_forms.py
    python scripts/03_segment_forms.py --single USA_2023
    python scripts/03_segment_forms.py --dry-run
"""

import argparse
import json
import logging
import os
import re
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CATALOGUE_PATH = PROJECT_ROOT / "data" / "catalogue.json"
EXTRACTED_DIR = PROJECT_ROOT / "data" / "extracted_text"
SEGMENTED_DIR = PROJECT_ROOT / "data" / "segmented"

# ── Constants ────────────────────────────────────────────────────────────────

MODEL = "claude-sonnet-4-20250514"
REGEX_MIN_FORMS = 2          # fall back to LLM if fewer than this many forms found
NTD_MAX_CHARS = 2000         # forms shorter than this may be "nothing to declare"

# Form keys in canonical order (determines section boundary priority)
FORM_ORDER = ["0", "A1", "A2", "B", "C", "E", "F", "G"]

# Output filename for each form key
FORM_FILENAMES: dict[str, str] = {
    "0":  "form_0.txt",
    "A1": "form_a1.txt",
    "A2": "form_a2.txt",
    "B":  "form_b.txt",
    "C":  "form_c.txt",
    "E":  "form_e.txt",
    "F":  "form_f.txt",
    "G":  "form_g.txt",
}

# ── Form anchor patterns ──────────────────────────────────────────────────────
# Each entry: (form_key, compiled_regex)
#
# Matched against the first 3 non-empty lines of each page (individually).
# If no single-line match, also tried against adjacent line-pairs to handle
# documents where form letter and part number fall on consecutive lines.
#
# Covers:
#   • Standard template: "Form A, Part 1 (i)", "Form B", etc.
#   • Hyphenated/quoted: "Confidence-Building Measure "A"", "CBM «A»"
#   • French: "Formulaire A - Partie 1", "MESURE DE CONFIANCE « A », Partie 1"
#   • Pre-2011 descriptive names: "Exchange of data on research centres…"
#   • Running-header docs (DEU, NLD, BEL, CAN): form ID is on line 2 or 3
#
# Q = quote characters (straight, curly, French guillemets)
_Q = r"""["«»\u201c\u201d\u2018\u2019]"""

FORM_ANCHORS: list[tuple[str, re.Pattern]] = [
    # ── Form 0 — Nothing to Declare cover sheet ──────────────────────────────
    ("0", re.compile(r"^Declaration\s+form\s+on\s+Nothing", re.I)),
    ("0", re.compile(r"^Nothing\s+to\s+Declare\s+or\s+Nothing\s+New\s+to\s+Declare", re.I)),

    # ── Form A Part 1 — Research centres and laboratories ────────────────────
    # Standard: "Form A, Part 1", "Form A Part 1", "Form A, part 1 (i)"
    ("A1", re.compile(r"^Form\s+A,?\s+[Pp]art\s+1\b", re.I)),
    # No-hyphen CBM: "Confidence Building Measure A, Part 1"
    ("A1", re.compile(r"^Confidence\s+Building\s+Measure\s+A,?\s+Part\s+1\b", re.I)),
    # Hyphenated/quoted: "Confidence-Building Measure "A", Part 1"
    # \s* inside quotes handles "« A »" style (guillemets with spaces around letter)
    ("A1", re.compile(
        rf"^Confidence[-\s]Building\s+Measure\s*{_Q}?\s*A\s*{_Q}?\s*[,:]?\s*Part\s*1\b", re.I)),
    # French: "MESURE DE CONFIANCE « A », Partie 1"
    ("A1", re.compile(
        rf"^MESURE\s+DE\s+CONFIANCE\s*{_Q}?\s*A\s*{_Q}?\s*[,:]?\s*Partie\s*1\b", re.I)),
    # French form name: "Formulaire A - Partie 1"
    ("A1", re.compile(r"^Formulaire\s+A\s*[-–]\s*Partie\s*1\b", re.I)),
    # Spanish: "Medida de fomento de la confianza "A", Parte 1"
    ("A1", re.compile(
        rf"^Medida\s+de\s+fomento\s+de\s+la\s+confianza\s*{_Q}?\s*A\s*{_Q}?\s*[,:]?\s*Parte\s*1\b", re.I)),
    # Pre-2011 / AUS-style: "Part 1 Exchange of data on research centres"
    ("A1", re.compile(r"^Part\s*1\s+Exchange\s+of\s+data\s+on\s+research", re.I)),
    # Pre-2011: bare section title
    ("A1", re.compile(r"^Exchange\s+of\s+data\s+on\s+research\s+centres", re.I)),

    # ── Form A Part 2 — National biological defence programmes ───────────────
    ("A2", re.compile(r"^Form\s+A,?\s+[Pp]art\s+2\b", re.I)),
    ("A2", re.compile(r"^Confidence\s+Building\s+Measure\s+A,?\s+Part\s+2\b", re.I)),
    ("A2", re.compile(
        rf"^Confidence[-\s]Building\s+Measure\s*{_Q}?\s*A\s*{_Q}?\s*[,:]?\s*Part\s*2\b", re.I)),
    ("A2", re.compile(
        rf"^MESURE\s+DE\s+CONFIANCE\s*{_Q}?\s*A\s*{_Q}?\s*[,:]?\s*Partie\s*2\b", re.I)),
    ("A2", re.compile(r"^Formulaire\s+A\s*[-–]\s*Partie\s*2\b", re.I)),
    ("A2", re.compile(
        rf"^Medida\s+de\s+fomento\s+de\s+la\s+confianza\s*{_Q}?\s*A\s*{_Q}?\s*[,:]?\s*Parte\s*2\b", re.I)),
    # NLD style: "Part2 Exchange of information on national biological defence"
    ("A2", re.compile(r"^Part\s*2\s+Exchange\s+of\s+information\s+on\s+national", re.I)),
    # Pre-2011 descriptive title
    ("A2", re.compile(r"^National\s+biological\s+de[fe]ence\s+research\s+and\s+development", re.I)),

    # ── Form B — Outbreaks ───────────────────────────────────────────────────
    ("B", re.compile(r"^Form\s+B\b", re.I)),
    ("B", re.compile(r"^Confidence\s+Building\s+Measure\s+B\b", re.I)),
    ("B", re.compile(
        rf"^Confidence[-\s]Building\s+Measure\s*{_Q}?\s*B\s*{_Q}?\b", re.I)),
    ("B", re.compile(rf"^MESURE\s+DE\s+CONFIANCE\s*{_Q}?\s*B\s*{_Q}?\b", re.I)),
    ("B", re.compile(rf"^Medida\s+de\s+fomento\s+de\s+la\s+confianza\s*{_Q}?\s*B\s*{_Q}?\b", re.I)),
    ("B", re.compile(r"^Formulaire\s+B\b", re.I)),
    ("B", re.compile(r"^Exchange\s+of\s+information\s+on\s+outbreaks", re.I)),
    ("B", re.compile(r"^Information\s+on\s+outbreaks\s+of\s+infectious", re.I)),
    ("B", re.compile(r"^Background\s+information\s+on\s+outbreaks", re.I)),

    # ── Form C — Publications ────────────────────────────────────────────────
    ("C", re.compile(r"^Form\s+C\b", re.I)),
    ("C", re.compile(r"^Confidence\s+Building\s+Measure\s+C\b", re.I)),
    ("C", re.compile(
        rf"^Confidence[-\s]Building\s+Measure\s*{_Q}?\s*C\s*{_Q}?\b", re.I)),
    ("C", re.compile(rf"^MESURE\s+DE\s+CONFIANCE\s*{_Q}?\s*C\s*{_Q}?\b", re.I)),
    ("C", re.compile(rf"^Medida\s+de\s+fomento\s+de\s+la\s+confianza\s*{_Q}?\s*C\s*{_Q}?\b", re.I)),
    ("C", re.compile(r"^Formulaire\s+C\b", re.I)),
    ("C", re.compile(r"^Encouragement\s+of\s+publication\s+of\s+results", re.I)),

    # ── Form E — Legislation ─────────────────────────────────────────────────
    ("E", re.compile(r"^Form\s+E\b", re.I)),
    ("E", re.compile(r"^Confidence\s+Building\s+Measure\s+E\b", re.I)),
    ("E", re.compile(
        rf"^Confidence[-\s]Building\s+Measure\s*{_Q}?\s*E\s*{_Q}?\b", re.I)),
    ("E", re.compile(rf"^MESURE\s+DE\s+CONFIANCE\s*{_Q}?\s*E\s*{_Q}?\b", re.I)),
    ("E", re.compile(rf"^Medida\s+de\s+fomento\s+de\s+la\s+confianza\s*{_Q}?\s*E\s*{_Q}?\b", re.I)),
    ("E", re.compile(r"^Formulaire\s+E\b", re.I)),
    ("E", re.compile(r"^Declaration\s+of\s+legislation[,\s]", re.I)),

    # ── Form F — Past offensive/defensive programmes ─────────────────────────
    ("F", re.compile(r"^Form\s+F\b", re.I)),
    ("F", re.compile(r"^Confidence\s+Building\s+Measure\s+F\b", re.I)),
    ("F", re.compile(
        rf"^Confidence[-\s]Building\s+Measure\s*{_Q}?\s*F\s*{_Q}?\b", re.I)),
    ("F", re.compile(rf"^MESURE\s+DE\s+CONFIANCE\s*{_Q}?\s*F\s*{_Q}?\b", re.I)),
    ("F", re.compile(rf"^Medida\s+de\s+fomento\s+de\s+la\s+confianza\s*{_Q}?\s*F\s*{_Q}?\b", re.I)),
    ("F", re.compile(r"^Formulaire\s+F\b", re.I)),
    ("F", re.compile(r"^Declaration\s+of\s+past\s+activities\s+in\s+offensive", re.I)),

    # ── Form G — Vaccine production facilities ───────────────────────────────
    ("G", re.compile(r"^Form\s+G\b", re.I)),
    ("G", re.compile(r"^Confidence\s+Building\s+Measure\s+G\b", re.I)),
    ("G", re.compile(
        rf"^Confidence[-\s]Building\s+Measure\s*{_Q}?\s*G\s*{_Q}?\b", re.I)),
    ("G", re.compile(rf"^MESURE\s+DE\s+CONFIANCE\s*{_Q}?\s*G\s*{_Q}?\b", re.I)),
    ("G", re.compile(rf"^Medida\s+de\s+fomento\s+de\s+la\s+confianza\s*{_Q}?\s*G\s*{_Q}?\b", re.I)),
    ("G", re.compile(r"^Formulaire\s+G\b", re.I)),
    ("G", re.compile(r"^Declaration\s+of\s+vaccine\s+production\s+facilities", re.I)),

    # ── Russian/Cyrillic forms (UKR and other post-Soviet submissions) ────────
    # Форма = Form, часть = Part
    ("A1", re.compile(r"^Форма\s+[Aa],?\s+часть\s+1\b", re.I)),
    ("A2", re.compile(r"^Форма\s+[Aa],?\s+часть\s+2\b", re.I)),
    ("B",  re.compile(r"^Форма\s+[Bb]\b", re.I)),
    ("C",  re.compile(r"^Форма\s+[Cc]\b", re.I)),
    ("E",  re.compile(r"^Форма\s+[Ee]\b", re.I)),
    ("F",  re.compile(r"^Форма\s+[Ff]\b", re.I)),
    ("G",  re.compile(r"^Форма\s+[Gg]\b", re.I)),

    # ── Short "Measure X" format (IRL and similar compact submissions) ────────
    ("A1", re.compile(r"^Measure\s+A,?\s+Part\s+1\b", re.I)),
    ("A2", re.compile(r"^Measure\s+A,?\s+Part\s+2\b", re.I)),
    ("B",  re.compile(r"^Measure\s*B\b", re.I)),
    ("C",  re.compile(r"^Measure\s*C\b", re.I)),
    ("E",  re.compile(r"^Measure\s*E\b", re.I)),
    ("F",  re.compile(r"^Measure\s*F\b", re.I)),
    ("G",  re.compile(r"^Measure\s*G\b", re.I)),

    # ── Numbered-list format (NOR pre-2011 and similar) ───────────────────────
    # "2. CONFIDENCE-BUILDING MEASURE "A":" — A1/A2 distinction handled by
    # FORM_ANCHOR_PAIRS below; bare A header defaults to A1 as fallback.
    ("A1", re.compile(rf"^\d+\.\s+CONFIDENCE[-\s]BUILDING\s+MEASURE\s*{_Q}?\s*A\s*{_Q}?", re.I)),
    ("B",  re.compile(rf"^\d+\.\s+CONFIDENCE[-\s]BUILDING\s+MEASURE\s*{_Q}?\s*B\s*{_Q}?\b", re.I)),
    ("C",  re.compile(rf"^\d+\.\s+CONFIDENCE[-\s]BUILDING\s+MEASURE\s*{_Q}?\s*C\s*{_Q}?\b", re.I)),
    ("E",  re.compile(rf"^\d+\.\s+CONFIDENCE[-\s]BUILDING\s+MEASURE\s*{_Q}?\s*E\s*{_Q}?\b", re.I)),
    ("F",  re.compile(rf"^\d+\.\s+CONFIDENCE[-\s]BUILDING\s+MEASURE\s*{_Q}?\s*F\s*{_Q}?\b", re.I)),
    ("G",  re.compile(rf"^\d+\.\s+CONFIDENCE[-\s]BUILDING\s+MEASURE\s*{_Q}?\s*G\s*{_Q}?\b", re.I)),
]

# ── Line-pair patterns ────────────────────────────────────────────────────────
# Tried against "line[i] + ' ' + line[i+1]" for i in 0..2.
# Handles documents where the form letter and part number fall on
# consecutive lines (e.g. NLD: line 2 = 'Confidence-Building Measure "A"',
# line 3 = 'Part1 Exchange of data…'; MEX: "Medida A" + "Parte 1").

FORM_ANCHOR_PAIRS: list[tuple[str, re.Pattern]] = [
    ("A1", re.compile(
        rf"Confidence[-\s]Building\s+Measure\s*{_Q}?\s*A\s*{_Q}?\s+Part\s*1\b", re.I)),
    ("A2", re.compile(
        rf"Confidence[-\s]Building\s+Measure\s*{_Q}?\s*A\s*{_Q}?\s+Part\s*2\b", re.I)),
    ("A1", re.compile(
        rf"MESURE\s+DE\s+CONFIANCE\s*{_Q}?\s*A\s*{_Q}?\s+Partie\s*1\b", re.I)),
    ("A2", re.compile(
        rf"MESURE\s+DE\s+CONFIANCE\s*{_Q}?\s*A\s*{_Q}?\s+Partie\s*2\b", re.I)),
    ("A1", re.compile(
        rf"Medida\s+de\s+fomento\s+de\s+la\s+confianza\s*{_Q}?\s*A\s*{_Q}?\s+Parte\s*1\b", re.I)),
    ("A2", re.compile(
        rf"Medida\s+de\s+fomento\s+de\s+la\s+confianza\s*{_Q}?\s*A\s*{_Q}?\s+Parte\s*2\b", re.I)),
    # Numbered-list format: the A header and Part 1/2 may fall on consecutive lines
    # (e.g. NOR pre-2011: line N = '2. CONFIDENCE-BUILDING MEASURE "A":'
    #                      line N+1 = 'Part 1: Exchange of data on research centers')
    # These override the bare A1 fallback in FORM_ANCHORS when Part 2 is present.
    ("A2", re.compile(
        rf"CONFIDENCE[-\s]BUILDING\s+MEASURE\s*{_Q}?\s*A\s*{_Q}?.*Part\s*2\b", re.I)),
    ("A1", re.compile(
        rf"CONFIDENCE[-\s]BUILDING\s+MEASURE\s*{_Q}?\s*A\s*{_Q}?.*Part\s*1\b", re.I)),
]

# Nothing-to-declare phrases
NTD_PATTERN = re.compile(
    r"nothing\s+(new\s+)?to\s+declare|nothing\s+to\s+report|"
    r"no\s+declaration\s+(is\s+)?required|no\s+activities\s+to\s+report",
    re.I,
)

PAGE_SEP = re.compile(r"^--- PAGE \d+ ---$", re.M)


# ── Page utilities ────────────────────────────────────────────────────────────


def split_pages(text: str) -> list[tuple[int, str]]:
    """Split extracted text into (page_number, page_text) pairs."""
    parts = re.split(r"--- PAGE (\d+) ---\n?", text)
    # parts[0] = pre-page preamble (usually empty)
    # parts[1::2] = page numbers, parts[2::2] = page content
    pages: list[tuple[int, str]] = []
    for i in range(1, len(parts), 2):
        pnum = int(parts[i])
        ptext = parts[i + 1] if i + 1 < len(parts) else ""
        pages.append((pnum, ptext))
    return pages


def classify_page(page_text: str) -> str | None:
    """Return the form key if the page starts a new form section, else None.

    Checks the first 3 non-empty lines individually (catches running-header
    documents where the form identifier is on line 2 or 3), then checks
    adjacent line-pairs (catches documents where the form letter and part
    number fall on consecutive lines).
    """
    lines = [l.strip() for l in page_text.split("\n") if l.strip()][:6]

    # Pass 1: check the first 5 non-empty lines against single-line patterns.
    # 5 (not 3) because some pre-2011 formats (e.g. NOR) prepend a 4-line block
    # of document reference / page number before the form identifier on line 5.
    for line in lines[:5]:
        for form_key, pattern in FORM_ANCHORS:
            if pattern.match(line):
                return form_key

    # Pass 2: consecutive pairs against multi-line patterns (up to pair 4+5)
    for i in range(min(5, len(lines) - 1)):
        combined = lines[i] + " " + lines[i + 1]
        for form_key, pattern in FORM_ANCHOR_PAIRS:
            if pattern.search(combined):
                return form_key

    return None


# ── Regex segmentation ────────────────────────────────────────────────────────


def segment_regex(
    pages: list[tuple[int, str]],
) -> tuple[dict[str, str], str, str]:
    """
    Segment pages into form sections using regex page-start matching.

    Returns:
        sections: dict mapping form_key → concatenated page text
        method: "regex"
        confidence: "high" or "medium"
    """
    current_form: str | None = None
    section_pages: dict[str, list[str]] = {}

    for page_num, page_text in pages:
        form_key = classify_page(page_text)
        if form_key is not None:
            current_form = form_key
        if current_form is not None:
            section_pages.setdefault(current_form, [])
            section_pages[current_form].append(f"--- PAGE {page_num} ---\n{page_text}")

    sections = {k: "".join(v) for k, v in section_pages.items()}

    # Confidence: high if we found >= 3 forms, medium if 2
    n = len(sections)
    confidence = "high" if n >= 3 else "medium"
    return sections, "regex", confidence


# ── LLM fallback ─────────────────────────────────────────────────────────────


def segment_llm(
    entry_id: str,
    pages: list[tuple[int, str]],
    client: anthropic.Anthropic,
) -> tuple[dict[str, str], str, str]:
    """
    Ask Claude to identify form boundaries and return segmented text.

    Returns:
        sections: dict mapping form_key → concatenated page text
        method: "llm"
        confidence: "low"
    """
    # Build a compact page index (first 200 chars per page, limit to 150 pages)
    index_lines = []
    for page_num, page_text in pages[:150]:
        snippet = page_text.strip()[:200].replace("\n", " ")
        index_lines.append(f"Page {page_num}: {snippet}")
    page_index = "\n".join(index_lines)

    prompt = f"""\
You are analysing a BWC CBM (Biological Weapons Convention Confidence-Building Measures) PDF document (id: {entry_id}).

Below is a compact index of page numbers and their opening text.

Identify the FIRST page number where each of the following form sections begins (if present):
- "0"  : Form 0 / Nothing to Declare cover sheet
- "A1" : Form A Part 1 — Research centres and laboratories
- "A2" : Form A Part 2 — National biological defence programmes
- "B"  : Form B — Outbreaks of infectious diseases
- "C"  : Form C — Publications
- "E"  : Form E — Legislation / regulations
- "F"  : Form F — Past offensive/defensive programmes
- "G"  : Form G — Vaccine production facilities

Return ONLY valid JSON in this exact format (omit forms not present):
{{
  "sections": {{
    "0":  <page_number>,
    "A1": <page_number>,
    "A2": <page_number>,
    "B":  <page_number>,
    "C":  <page_number>,
    "E":  <page_number>,
    "F":  <page_number>,
    "G":  <page_number>
  }}
}}

PAGE INDEX:
{page_index}
"""

    log.info("[%s] Calling Claude API for LLM segmentation fallback", entry_id)
    time.sleep(8)   # respect rate limits when multiple docs need LLM fallback
    message = client.messages.create(
        model=MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()

    # Extract JSON from response (Claude may wrap it in markdown)
    json_match = re.search(r"\{.*\}", raw, re.S)
    if not json_match:
        log.error("[%s] LLM returned no parseable JSON: %s", entry_id, raw[:200])
        return {}, "llm", "low"

    try:
        data = json.loads(json_match.group())
        start_pages: dict[str, int] = {
            k: int(v) for k, v in data.get("sections", {}).items()
        }
    except (json.JSONDecodeError, ValueError) as exc:
        log.error("[%s] Failed to parse LLM JSON: %s", entry_id, exc)
        return {}, "llm", "low"

    if not start_pages:
        log.warning("[%s] LLM found no form sections", entry_id)
        return {}, "llm", "low"

    # Sort forms by their start page
    ordered = sorted(start_pages.items(), key=lambda x: x[1])
    page_map = {pnum: ptext for pnum, ptext in pages}
    all_page_nums = [pnum for pnum, _ in pages]

    sections: dict[str, str] = {}
    for i, (form_key, start_p) in enumerate(ordered):
        end_p = ordered[i + 1][1] - 1 if i + 1 < len(ordered) else max(all_page_nums)
        chunk_parts = []
        for pnum, ptext in pages:
            if start_p <= pnum <= end_p:
                chunk_parts.append(f"--- PAGE {pnum} ---\n{ptext}")
        if chunk_parts:
            sections[form_key] = "".join(chunk_parts)

    log.info("[%s] LLM identified %d form sections", entry_id, len(sections))
    return sections, "llm", "low"


# ── Nothing-to-declare detection ─────────────────────────────────────────────


def is_nothing_to_declare(form_text: str) -> bool:
    """True if the form text is a short NTD statement with minimal substantive content."""
    # Strip page separators and form header lines to get content length
    content = PAGE_SEP.sub("", form_text).strip()
    return bool(NTD_PATTERN.search(content)) and len(content) < NTD_MAX_CHARS


# ── Per-document processing ───────────────────────────────────────────────────


def process_entry(
    entry: dict,
    client: anthropic.Anthropic | None,
    *,
    dry_run: bool,
) -> dict:
    """
    Segment one document.  Returns a result dict with stats for the summary.
    """
    entry_id = entry["id"]
    txt_path = EXTRACTED_DIR / f"{entry_id}.txt"

    if not txt_path.exists():
        log.warning("[%s] No extracted text file found — skipping", entry_id)
        return {"id": entry_id, "status": "missing_txt"}

    text = txt_path.read_text(encoding="utf-8")
    pages = split_pages(text)

    if not pages:
        log.warning("[%s] Could not parse any pages from text file", entry_id)
        return {"id": entry_id, "status": "no_pages"}

    # ── Primary: regex ────────────────────────────────────────────────────────
    sections, method, confidence = segment_regex(pages)

    # ── Fallback: LLM ─────────────────────────────────────────────────────────
    if len(sections) < REGEX_MIN_FORMS:
        if client is None:
            log.warning(
                "[%s] Regex found %d form(s) but no Anthropic client available — skipping LLM",
                entry_id,
                len(sections),
            )
        else:
            log.info(
                "[%s] Regex found only %d form(s) — trying LLM fallback",
                entry_id,
                len(sections),
            )
            sections, method, confidence = segment_llm(entry_id, pages, client)

    if not sections:
        log.warning("[%s] No form sections identified", entry_id)
        return {"id": entry_id, "status": "no_sections", "method": method}

    # ── Build manifest ────────────────────────────────────────────────────────
    forms_present = [k for k in FORM_ORDER if k in sections]
    forms_ntd = [k for k in forms_present if is_nothing_to_declare(sections[k])]

    manifest = {
        "id": entry_id,
        "forms_present": forms_present,
        "forms_nothing_to_declare": forms_ntd,
        "segmentation_method": method,
        "confidence": confidence,
    }

    log.info(
        "[%s] %d forms [%s] via %s (%s)%s",
        entry_id,
        len(forms_present),
        ", ".join(forms_present),
        method,
        confidence,
        f" | NTD: {forms_ntd}" if forms_ntd else "",
    )

    # ── Write outputs ─────────────────────────────────────────────────────────
    out_dir = SEGMENTED_DIR / entry_id

    if dry_run:
        log.info("[%s] --dry-run: would write to %s", entry_id, out_dir)
    else:
        out_dir.mkdir(parents=True, exist_ok=True)
        for form_key, form_text in sections.items():
            filename = FORM_FILENAMES.get(form_key, f"form_{form_key.lower()}.txt")
            (out_dir / filename).write_text(form_text, encoding="utf-8")
        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    return {
        "id": entry_id,
        "status": "ok",
        "method": method,
        "forms": forms_present,
        "ntd": forms_ntd,
    }


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--single", metavar="ID", help="Process only this document id.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and log results without writing any files.",
    )
    args = parser.parse_args()

    if not CATALOGUE_PATH.exists():
        log.error("Catalogue not found: %s", CATALOGUE_PATH)
        raise SystemExit(1)

    catalogue: list[dict] = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))

    # Only process entries that have been extracted, don't need OCR, and aren't amendments
    eligible = [
        e for e in catalogue
        if e.get("downloaded") and not e.get("needs_ocr")
        and not e.get("is_amendment")
        and (EXTRACTED_DIR / f"{e['id']}.txt").exists()
    ]

    if args.single:
        targets = [e for e in eligible if e["id"] == args.single]
        if not targets:
            log.error("No eligible entry with id=%r", args.single)
            raise SystemExit(1)
        log.info("--single mode: processing %s", args.single)
    else:
        targets = eligible
        log.info("Processing %d eligible documents", len(targets))

    # Initialise Anthropic client (needed only for LLM fallback)
    api_key = os.getenv("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=api_key) if api_key else None
    if not client:
        log.warning("ANTHROPIC_API_KEY not set — LLM fallback disabled")

    results: list[dict] = []
    for entry in targets:
        result = process_entry(entry, client, dry_run=args.dry_run)
        results.append(result)

    # ── Summary ───────────────────────────────────────────────────────────────
    ok = [r for r in results if r.get("status") == "ok"]
    n_regex = sum(1 for r in ok if r.get("method") == "regex")
    n_llm = sum(1 for r in ok if r.get("method") == "llm")
    n_failed = len(results) - len(ok)

    print("\n── Summary ──────────────────────────────────────────────────")
    print(f"  Documents segmented:    {len(ok)}")
    print(f"    via regex:            {n_regex}")
    print(f"    via LLM fallback:     {n_llm}")
    if n_failed:
        print(f"  Failed / skipped:       {n_failed}")
    if ok:
        all_forms: list[str] = []
        for r in ok:
            all_forms.extend(r.get("forms", []))
        from collections import Counter
        form_counts = Counter(all_forms)
        print("\n  Form coverage (across all documents):")
        for fk in FORM_ORDER:
            if fk in form_counts:
                print(f"    Form {fk}: {form_counts[fk]} documents")
    print("─────────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
