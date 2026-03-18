#!/usr/bin/env bash
# =============================================================================
# annual_update.sh — Full CBM pipeline runner for annual data updates
#
# FEATURE 6: Annual update pipeline script
#
# Runs the complete extraction pipeline for new submissions:
#   01 → catalogue (check for new entries, no download yet)
#   02 → extract text from new PDFs
#   03 → segment form sections
#   04 → extract structured data (all forms)
#   05 → assemble output CSVs/JSON
#   06 → load into PostgreSQL
#   07 → geocode new facilities
#
# Usage:
#   bash scripts/annual_update.sh
#
# Prerequisites:
#   - Python virtual environment at .venv/
#   - PostgreSQL running (Homebrew pg17)
#   - DATABASE_URL set in .env or as environment variable
#   - ANTHROPIC_API_KEY set in .env or as environment variable
# =============================================================================

set -euo pipefail

# ── Resolve project root (directory of this script's parent) ─────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "=== CBM Annual Update Pipeline ==="
echo "Project root: ${PROJECT_ROOT}"
echo ""

# ── PostgreSQL 17 path (Homebrew) ─────────────────────────────────────────────
# FEATURE 6: Ensure pg17 binaries are on PATH for psycopg2 / migration scripts
export PATH="/opt/homebrew/opt/postgresql@17/bin:${PATH}"

# ── Check required environment variables ──────────────────────────────────────
# FEATURE 6: Warn (do not abort) if DB credentials or API key appear missing
if [ -z "${DATABASE_URL:-}" ] && [ ! -f "${PROJECT_ROOT}/.env" ]; then
    echo "[WARN] DATABASE_URL is not set and no .env file found."
    echo "       Set DATABASE_URL or create .env before running the DB steps."
    echo ""
fi

if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ ! -f "${PROJECT_ROOT}/.env" ]; then
    echo "[WARN] ANTHROPIC_API_KEY is not set and no .env file found."
    echo "       Set ANTHROPIC_API_KEY or create .env before running extraction steps."
    echo ""
fi

# Check that .env exists (soft warning only — dotenv will handle it)
if [ -f "${PROJECT_ROOT}/.env" ]; then
    echo "[OK] .env file found at ${PROJECT_ROOT}/.env"
fi

# ── Activate virtual environment ──────────────────────────────────────────────
VENV="${PROJECT_ROOT}/.venv"
if [ ! -f "${VENV}/bin/activate" ]; then
    echo "[ERROR] Virtual environment not found at ${VENV}"
    echo "        Run: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi
# shellcheck source=/dev/null
source "${VENV}/bin/activate"
echo "[OK] Activated virtual environment: ${VENV}"
echo ""

# ── Step 1: Catalogue — check for new entries (no download) ──────────────────
echo "--- Step 1: Catalogue (check for new entries, skip download) ---"
python3 "${PROJECT_ROOT}/scripts/01_catalogue.py" --skip-download
echo ""

# ── Prompt before downloading new PDFs ───────────────────────────────────────
echo "Review the catalogue above for new entries."
read -rp "Proceed to download new PDFs? [y/N] " CONFIRM
if [[ ! "${CONFIRM}" =~ ^[Yy]$ ]]; then
    echo "[ABORT] User cancelled before download step."
    exit 0
fi
echo ""

# ── Step 1 (download): Re-run catalogue to download any new PDFs ─────────────
echo "--- Step 1 (download): Downloading new PDFs ---"
python3 "${PROJECT_ROOT}/scripts/01_catalogue.py"
echo ""

# ── Step 2: Extract text from new PDFs ───────────────────────────────────────
# Incremental: skips entries whose extracted text already exists.
# Use --force to re-extract everything.
echo "--- Step 2: Extract text (OCR if needed) ---"
python3 "${PROJECT_ROOT}/scripts/02_extract_text.py"
echo ""

# ── Step 3: Segment form sections ────────────────────────────────────────────
# Incremental: skips entries whose segmented output directory already exists.
# Use --force to re-segment everything.
echo "--- Step 3: Segment form sections ---"
python3 "${PROJECT_ROOT}/scripts/03_segment_forms.py"
echo ""

# ── Step 4: Extract structured data — all forms ───────────────────────────────
# FEATURE 6: Run all form extractors sequentially
# Note: Script 04 skips documents whose output JSON already exists (incremental).
echo "--- Step 4a: Extract Form A1 (research facilities) ---"
python3 "${PROJECT_ROOT}/scripts/04_extract_structured.py"
echo ""

echo "--- Step 4b: Extract Form G (vaccine facilities) ---"
python3 "${PROJECT_ROOT}/scripts/04_extract_structured.py" --form-g
echo ""

echo "--- Step 4c: Extract Form A2 (defence programmes) ---"
python3 "${PROJECT_ROOT}/scripts/04_extract_structured.py" --form-a2
echo ""

echo "--- Step 4d: Extract Form F (past programmes) ---"
python3 "${PROJECT_ROOT}/scripts/04_extract_structured.py" --form-f
echo ""

echo "--- Step 4e: Extract Form E (legislation) ---"
python3 "${PROJECT_ROOT}/scripts/04_extract_structured.py" --form-e
echo ""

# ── Step 5: Assemble output CSVs / JSON ──────────────────────────────────────
echo "--- Step 5: Assemble output files ---"
python3 "${PROJECT_ROOT}/scripts/05_assemble_output.py"
echo ""

# ── Step 6: Load into PostgreSQL ──────────────────────────────────────────────
echo "--- Step 6: Load database ---"
echo "[NOTE] This truncates and reloads all tables. Geocoded coordinates are preserved."
python3 "${PROJECT_ROOT}/scripts/06_load_database.py"
echo ""

# ── Step 7: Geocode new facilities ────────────────────────────────────────────
echo "--- Step 7: Geocode new facilities (may take ~2 hours for a full run) ---"
echo "[NOTE] Only un-geocoded rows are processed; previously geocoded rows are skipped."
python3 "${PROJECT_ROOT}/scripts/07_geocode.py"
echo ""

echo "=== Annual update complete. ==="
echo "Restart the API server to serve updated data:"
echo "  uvicorn api.main:app --port 8000 --reload"
