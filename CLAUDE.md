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

Primary PDF source: bwcimplementation.org (static URLs, no JS rendering)
Secondary source: bwc-cbm.un.org (JS-rendered, needs Playwright — Phase 2)

Example PDF URLs:
- https://bwcimplementation.org/sites/default/files/resource/US_CBM_2023.pdf
- https://bwcimplementation.org/sites/default/files/resource/bwc_cbm_2010_sweden.pdf
- https://bwcimplementation.org/sites/default/files/resource/bwc_cbm_2018_japan.pdf

## Development approach
Build and validate one script at a time, sequentially (01 through 05).
Each script reads from the previous script's output directory.
Start with English-language born-digital PDFs only.
Use Claude Sonnet API (claude-sonnet-4-20250514) for extraction calls.
Load API key from .env using python-dotenv.
