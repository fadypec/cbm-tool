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
import json
import logging
import os
import re
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
log = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CATALOGUE_PATH = PROJECT_ROOT / "data" / "catalogue.json"
SEGMENTED_DIR  = PROJECT_ROOT / "data" / "segmented"
STRUCTURED_DIR = PROJECT_ROOT / "data" / "structured"

# ── Constants ────────────────────────────────────────────────────────────────

MODEL            = "claude-sonnet-4-20250514"
MAX_TOKENS       = 8192
CHUNK_MAX_CHARS  = 4_000    # max chars per API call (keeps output well within 8192 token limit after translation)
RATE_LIMIT_DELAY = 10.0     # seconds between calls  →  ≤6 req/min

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
- If Form A part 1(i) fields 1–7 are all blank but part 1(ii) declares a containment \
level, create one facility record with facility_name="[Unnamed facility]", populate \
has_bsl3/highest_containment from the declared level, and note in "notes" that the \
facility name was not declared
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

    # Build sections
    sections: list[str] = []
    for i, start in enumerate(adj_positions):
        end = adj_positions[i + 1] if i + 1 < len(adj_positions) else len(text)
        sections.append(text[start:end].strip())

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

    resp = api_call(client, messages, last_t)
    raw = resp.content[0].text
    usage = {
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
    }
    log.debug("[%s] tokens in=%d out=%d", entry["id"],
              usage["input_tokens"], usage["output_tokens"])

    data = parse_json_response(raw)

    if data is None:
        log.warning("[%s] Response not valid JSON — retrying once", entry["id"])
        messages += [
            {"role": "assistant", "content": raw},
            {"role": "user", "content":
             "Your response was not valid JSON. "
             "Return ONLY the JSON object — no markdown fences, no explanation."},
        ]
        resp2 = api_call(client, messages, last_t)
        raw2 = resp2.content[0].text
        usage["input_tokens"]  += resp2.usage.input_tokens
        usage["output_tokens"] += resp2.usage.output_tokens
        data = parse_json_response(raw2)

        if data is None:
            log.error("[%s] Retry also returned invalid JSON", entry["id"])
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

    resp = api_call(client, messages, last_t, system=SYSTEM_PROMPT_G)
    raw = resp.content[0].text
    usage = {
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
    }

    data = parse_json_response(raw)

    if data is None:
        log.warning("[%s/G] Response not valid JSON — retrying once", entry["id"])
        messages += [
            {"role": "assistant", "content": raw},
            {"role": "user", "content":
             "Your response was not valid JSON. "
             "Return ONLY the JSON object — no markdown fences, no explanation."},
        ]
        resp2 = api_call(client, messages, last_t, system=SYSTEM_PROMPT_G)
        raw2 = resp2.content[0].text
        usage["input_tokens"]  += resp2.usage.input_tokens
        usage["output_tokens"] += resp2.usage.output_tokens
        data = parse_json_response(raw2)

        if data is None:
            log.error("[%s/G] Retry also returned invalid JSON", entry["id"])
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

    resp = api_call(client, messages, last_t, system=SYSTEM_PROMPT_A2)
    raw = resp.content[0].text
    usage = {
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
    }

    data = parse_json_response(raw)

    if data is None:
        log.warning("[%s/A2] Response not valid JSON — retrying once", entry["id"])
        messages += [
            {"role": "assistant", "content": raw},
            {"role": "user", "content":
             "Your response was not valid JSON. "
             "Return ONLY the JSON object — no markdown fences, no explanation."},
        ]
        resp2 = api_call(client, messages, last_t, system=SYSTEM_PROMPT_A2)
        raw2 = resp2.content[0].text
        usage["input_tokens"]  += resp2.usage.input_tokens
        usage["output_tokens"] += resp2.usage.output_tokens
        data = parse_json_response(raw2)

        if data is None:
            log.error("[%s/A2] Retry also returned invalid JSON", entry["id"])
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


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--single", metavar="ID",
                        help="Process only this document id.")
    parser.add_argument("--form-g", action="store_true",
                        help="Extract Form G (vaccine facilities).")
    parser.add_argument("--form-a2", action="store_true",
                        help="Extract Form A Part 2 (national biological defence programmes).")
    args = parser.parse_args()

    if args.form_g and args.form_a2:
        log.error("--form-g and --form-a2 are mutually exclusive")
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

    if args.form_a2:
        desc = "Extracting Form A Part 2"
    elif args.form_g:
        desc = "Extracting Form G"
    else:
        desc = "Extracting Form A Part 1"

    for entry in tqdm(targets, desc=desc, unit="doc", dynamic_ncols=True):
        if args.form_a2:
            result = process_entry_a2(entry, client, last_t)
            item_key = None   # sum programmes + facilities
        elif args.form_g:
            result = process_entry_g(entry, client, last_t)
            item_key = "vaccine_facilities"
        else:
            result = process_entry(entry, client, last_t)
            item_key = "facilities"
        results.append(result)

        status = result.get("status", "")
        if status == "ok":
            n_ok += 1
            if args.form_a2:
                total_items += result.get("programmes", 0) + result.get("facilities", 0)
            else:
                total_items += result.get(item_key, 0)
            total_calls += result.get("calls", 0)
            total_in    += result.get("input_tokens", 0)
            total_out   += result.get("output_tokens", 0)
        elif status == "skipped":
            n_skipped += 1
        else:
            n_failed += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    item_label = "programmes+facilities" if args.form_a2 else ("vaccine facilities" if args.form_g else "facilities")
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
    cost = total_in * 3e-6 + total_out * 15e-6
    print(f"  Est. cost (USD):      ${cost:.2f}")
    print("─────────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
