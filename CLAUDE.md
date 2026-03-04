# CBM Structured Database Toolø

## What this project does
Extracts structured data from BWC (Biological Weapons Convention) 
Confidence-Building Measure (CBM) PDF submissions, producing a queryable 
database of declared biological research facilities worldwide.

## Project structure
cbm-tool/
├── CLAUDE.md          (this file — project context for Claude Code)
├── .env               (API keys — not committed to git)
├── requirements.txt
├── scripts/
│   ├── 01_catalogue.py
│   ├── 02_extract_text.py
│   ├── 03_segment_forms.py
│   ├── 04_extract_structured.py
│   └── 05_assemble_output.py
├── data/
│   ├── raw_pdfs/
│   ├── extracted_text/
│   ├── segmented/
│   ├── structured/
│   └── output/
└── validation/

## Key technical facts about CBM documents

CBM PDFs are submitted annually by BWC states parties. The 2011 revised 
template (used from 2012 onwards) has these forms:
- Form 0: Cover page with "nothing to declare" table
- Form A Part 1: Research centres and laboratories (PRIORITY)
- Form A Part 2: National biological defence programmes
- Form B: Outbreaks
- Form C: Publications
- Form E: Legislation
- Form F: Past offensive/defensive programmes
- Form G: Vaccine production facilities (PRIORITY)

Form A Part 1 facility entries use numbered fields:
1. Name(s) of facility
2. Responsible public or private organization or company
3. Location and postal address
4. Source(s) of financing (including whether Ministry of Defence)
5. Number of maximum containment units and their size (m²)
6. If no maximum containment unit, highest level of protection
7. Scope and general description of activities, including micro-organisms

Multiple facilities appear sequentially, each restarting at field 1.

Primary PDF source: bwc-cbm.un.org — public JSON search API (no auth required)
  - Enumerate: POST https://bwc-cbm.un.org/api/search/ with {from, size, search:"", filter:{country:[]}}
  - Download: POST https://cms-bwc-cbm.un.org/api/getDocument with {reportId: <int>, language: null}
  - 517 public records available; China/France/Russia/India absent (restricted, not a pipeline failure)
  - bwcimplementation.org now returns empty HTML (fully JS-rendered) — no longer usable

## Development approach
All 517 publicly available CBM submissions downloaded and processed.
Scripts process all languages (en, fr, es, ru) with Claude-side translation for non-English.
Form A Part 1 (research facilities) and Form G (vaccine facilities) both extracted.
Use Claude Sonnet API (claude-sonnet-4-20250514) for extraction calls.
Load API key from .env using python-dotenv.
