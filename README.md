# CBM Facility Explorer

Structured data from BWC Confidence-Building Measure submissions, served via REST API and interactive dashboard.

[![Tests](https://github.com/fadypec/cbm-tool/actions/workflows/test.yml/badge.svg)](https://github.com/fadypec/cbm-tool/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## Using the Tool

The [Biological Weapons Convention (BWC)](https://www.un.org/disarmament/wmd/bio/) requires states parties to submit annual Confidence-Building Measure (CBM) declarations covering research facilities, vaccine production, national defence programmes, past offensive programmes, and legislation. These submissions are public but distributed as PDFs, making systematic analysis difficult.

This tool extracts and structures that data, covering 517 public submissions from 45 countries (2000–2025).

**Live dashboard:** [cbm.fady.phd](https://cbm.fady.phd)

### Data Coverage

| Form | Description | Records |
|------|-------------|---------|
| A Part 1 | Research centres and laboratories | 1,597 facility-years · 306 unique facilities · 45 countries |
| A Part 2 | National biological defence programmes | 560 programmes · 969 facilities |
| E | Legislation and regulations | 352 records · 43 countries |
| F | Past offensive/defensive programmes | 354 records |
| G | Vaccine production facilities | 599 facility-years |

> **Note:** China, France, Russia, and India have no public CBM data — this is a restriction on the UN portal, not a pipeline gap.

---

## Self-Hosting / Development

### Prerequisites

- Python 3.12+
- PostgreSQL 17 + PostGIS 3.x (macOS: `brew install postgresql@17 postgis`)
- An [Anthropic API key](https://console.anthropic.com/) (used by the extraction pipeline and natural-language search)

### Installation

```bash
git clone https://github.com/pecaf/cbm-tool.git
cd cbm-tool
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in the required values:

```
DATABASE_URL=postgresql://user:password@localhost:5432/cbm
ANTHROPIC_API_KEY=...
REVIEW_API_KEY=...
```

### Extraction Pipeline

Run scripts in order after activating the venv:

```bash
# 1. Catalogue available submissions (downloads new PDFs)
python3 scripts/01_catalogue.py

# 2. OCR + text extraction (with Claude error correction)
python3 scripts/02_extract_text.py

# 3. Split documents into form sections
python3 scripts/03_segment_forms.py

# 4. Extract structured data per form
python3 scripts/04_extract_structured.py           # Form A1 (research facilities)
python3 scripts/04_extract_structured.py --form-a2 # Form A2 (defence programmes)
python3 scripts/04_extract_structured.py --form-e  # Form E (legislation)
python3 scripts/04_extract_structured.py --form-f  # Form F (past programmes)
python3 scripts/04_extract_structured.py --form-g  # Form G (vaccine facilities)

# 5. Assemble final CSVs and JSON
python3 scripts/05_assemble_output.py

# 6. Load into PostgreSQL
python3 scripts/06_load_database.py

# 7. Geocode facility addresses (~2 hours, 97% match rate)
python3 scripts/07_geocode.py
```

For annual updates, an interactive runner is available:

```bash
bash scripts/annual_update.sh
```

### Running the API

```bash
export PATH="/opt/homebrew/opt/postgresql@17/bin:$PATH"
uvicorn api.main:app --port 8000 --reload
```

Open [http://localhost:8000](http://localhost:8000) for the dashboard, or browse the API at [http://localhost:8000/api/docs](http://localhost:8000/api/docs) (disabled in production).

### Tests

```bash
pytest tests/test_api.py -v
```

76 tests, 93% coverage. Coverage threshold enforced at 80%.

### Deployment

The production instance runs on [Railway](https://railway.app/) (Amsterdam) with a [Supabase](https://supabase.com/) PostgreSQL database (EU West). Auto-deploys from the `main` branch. See `CLAUDE.md` for the full deployment workflow, including how to apply database migrations to production.

---

## License

[MIT](LICENSE)
