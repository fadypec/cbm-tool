#!/usr/bin/env python3
"""
02_extract_text.py — Extract text from CBM PDFs using pdfplumber.

Reads data/catalogue.json and processes each downloaded PDF:
  - Extracts text page by page (with TSV-formatted tables where detected)
  - Saves plain text to data/extracted_text/{id}.txt
  - Saves per-page JSON to data/extracted_text/{id}_pages.json
  - Updates catalogue.json with: page_count, total_char_count,
    avg_chars_per_page, needs_ocr

PDFs with avg_chars_per_page < 100 are flagged as needs_ocr=True and skipped
(no text output written).

Usage:
    python scripts/02_extract_text.py
    python scripts/02_extract_text.py --single USA_2023
"""

import argparse
import json
import logging
from pathlib import Path

import pdfplumber

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

# ── Constants ────────────────────────────────────────────────────────────────

OCR_CHAR_THRESHOLD = 100  # avg chars/page below this → needs_ocr=True


# ── Page extraction ───────────────────────────────────────────────────────────


def extract_page_content(page) -> tuple[str, bool]:
    """Extract text and tables from a single pdfplumber page.

    Tables are formatted as TSV blocks and appended after the non-table text.
    Returns (text, has_tables).
    """
    try:
        tables = page.find_tables()
    except Exception:
        tables = []

    if not tables:
        return page.extract_text() or "", False

    # Strip table regions from the page before extracting prose text
    remaining = page
    for table in tables:
        try:
            remaining = remaining.outside_bbox(table.bbox)
        except Exception:
            pass

    parts: list[str] = []
    prose = remaining.extract_text() or ""
    if prose.strip():
        parts.append(prose)

    for table in tables:
        rows = table.extract()
        if rows:
            tsv_lines = [
                "\t".join(str(cell or "").replace("\n", " ") for cell in row)
                for row in rows
            ]
            parts.append("[TABLE]\n" + "\n".join(tsv_lines) + "\n[/TABLE]")

    return "\n".join(parts), True


# ── Per-document processing ───────────────────────────────────────────────────


def process_pdf(entry: dict) -> dict | None:
    """Process a single catalogue entry.

    Returns an updated copy of the entry dict, or None on unrecoverable error.
    """
    entry_id = entry["id"]
    pdf_path = PROJECT_ROOT / entry["local_path"]

    if not pdf_path.exists():
        log.warning("[%s] PDF not found: %s", entry_id, pdf_path)
        return None

    pages_data: list[dict] = []
    page_texts: list[str] = []
    total_chars = 0

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                page_text, has_tables = extract_page_content(page)
                char_count = len(page_text)
                total_chars += char_count
                pages_data.append(
                    {
                        "page": page_num,
                        "text": page_text,
                        "char_count": char_count,
                        "has_tables": has_tables,
                    }
                )
                page_texts.append(f"--- PAGE {page_num} ---\n{page_text}")
    except Exception as exc:
        log.error("[%s] Extraction failed: %s", entry_id, exc)
        return None

    page_count = len(pages_data)
    if page_count == 0:
        log.warning("[%s] PDF has no pages", entry_id)
        return None

    avg_chars = total_chars / page_count
    needs_ocr = avg_chars < OCR_CHAR_THRESHOLD

    updated = {
        **entry,
        "page_count": page_count,
        "total_char_count": total_chars,
        "avg_chars_per_page": round(avg_chars, 1),
        "needs_ocr": needs_ocr,
    }

    if needs_ocr:
        log.warning(
            "[%s] Flagged needs_ocr (avg %.1f chars/page < %d) — skipping text output",
            entry_id,
            avg_chars,
            OCR_CHAR_THRESHOLD,
        )
        return updated

    # Write outputs
    EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
    txt_path = EXTRACTED_DIR / f"{entry_id}.txt"
    pages_path = EXTRACTED_DIR / f"{entry_id}_pages.json"

    txt_path.write_text("\n\n".join(page_texts), encoding="utf-8")
    pages_path.write_text(
        json.dumps(pages_data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    n_table_pages = sum(1 for p in pages_data if p["has_tables"])
    log.info(
        "[%s] %d pages | %d chars | avg %.0f/page%s",
        entry_id,
        page_count,
        total_chars,
        avg_chars,
        f" | {n_table_pages} pages with tables" if n_table_pages else "",
    )
    return updated


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--single",
        metavar="ID",
        help="Process only the entry with this id (e.g. USA_2023).",
    )
    args = parser.parse_args()

    if not CATALOGUE_PATH.exists():
        log.error("Catalogue not found: %s", CATALOGUE_PATH)
        raise SystemExit(1)

    catalogue: list[dict] = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))

    if args.single:
        targets = [e for e in catalogue if e["id"] == args.single]
        if not targets:
            log.error("No entry with id=%r in catalogue", args.single)
            raise SystemExit(1)
        log.info("--single mode: processing %s", args.single)
    else:
        targets = [e for e in catalogue if e.get("downloaded")]
        log.info("Processing %d downloaded PDFs", len(targets))

    # Build a mutable index of the full catalogue so we can patch entries in place
    catalogue_index: dict[str, dict] = {e["id"]: e for e in catalogue}

    n_processed = 0
    n_ocr = 0
    n_failed = 0
    total_pages = 0
    ocr_ids: list[str] = []

    for entry in targets:
        result = process_pdf(entry)
        if result is None:
            n_failed += 1
            continue

        catalogue_index[result["id"]] = result

        if result.get("needs_ocr"):
            n_ocr += 1
            ocr_ids.append(result["id"])
        else:
            n_processed += 1
            total_pages += result.get("page_count", 0)

    # Persist catalogue updates (preserving original ordering)
    updated_list = [catalogue_index[e["id"]] for e in catalogue]
    CATALOGUE_PATH.write_text(
        json.dumps(updated_list, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    log.info("Catalogue updated: %s", CATALOGUE_PATH)

    # ── Summary ──────────────────────────────────────────────────────────────
    print("\n── Summary ──────────────────────────────────────────────────")
    print(f"  Documents processed:    {n_processed}")
    print(f"  Total pages extracted:  {total_pages}")
    print(f"  Documents flagged OCR:  {n_ocr}")
    if n_failed:
        print(f"  Errors / not found:     {n_failed}")
    if ocr_ids:
        print(f"\n  OCR-needed IDs ({len(ocr_ids)}):")
        for oid in ocr_ids:
            print(f"    {oid}")
    print("─────────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
