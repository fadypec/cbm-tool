#!/usr/bin/env python3
"""
02_extract_text.py — Extract text from CBM PDFs using pdfplumber, with OCR fallback.

Reads data/catalogue.json and processes each downloaded PDF:
  - Extracts text page by page (with TSV-formatted tables where detected)
  - Saves plain text to data/extracted_text/{id}.txt
  - Saves per-page JSON to data/extracted_text/{id}_pages.json
  - Updates catalogue.json with: page_count, total_char_count,
    avg_chars_per_page, needs_ocr, ocr_engine (if OCR was used)

PDFs with avg_chars_per_page < 100 are passed to the OCR stage:
  - Arabic / Chinese → Google Cloud Vision API
    (requires GOOGLE_APPLICATION_CREDENTIALS env var; falls back to Tesseract)
  - All other languages → Tesseract (requires system tesseract + tesseract-lang)

After successful OCR, needs_ocr is set to False in the catalogue so that
downstream scripts (03, 04) process the document normally.

OCR output is automatically corrected by a Claude LLM call (one call per page)
to fix misread characters and OCR artifacts. Set ocr_corrected=True in catalogue.

Usage:
    python scripts/02_extract_text.py
    python scripts/02_extract_text.py --single USA_2023
    python scripts/02_extract_text.py --ocr-only    # reprocess only needs_ocr docs
    python scripts/02_extract_text.py --correct-ocr # LLM-correct already-OCR'd docs
"""

import argparse
import io
import json
import logging
import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

import pdfplumber

# Optional OCR dependencies — import lazily to give clear error messages
try:
    from pdf2image import convert_from_path as _pdf2images
    _PDF2IMAGE_OK = True
except ImportError:
    _PDF2IMAGE_OK = False

try:
    import pytesseract
    _TESSERACT_OK = True
except ImportError:
    _TESSERACT_OK = False

try:
    from google.cloud import vision as _gvision
    _VISION_OK = True
except ImportError:
    _VISION_OK = False

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────────

PROJECT_ROOT   = Path(__file__).resolve().parent.parent
CATALOGUE_PATH = PROJECT_ROOT / "data" / "catalogue.json"
EXTRACTED_DIR  = PROJECT_ROOT / "data" / "extracted_text"

# ── Constants ────────────────────────────────────────────────────────────────

OCR_CHAR_THRESHOLD = 100   # avg chars/page below this → attempt OCR
OCR_DPI            = 300   # resolution for PDF→image conversion

# ISO 639-1 → Tesseract language code
TESSERACT_LANG_MAP: dict[str, str] = {
    "en": "eng", "fr": "fra", "es": "spa", "pt": "por",
    "de": "deu", "nl": "nld", "ru": "rus", "uk": "ukr",
    "ar": "ara", "zh": "chi_sim", "ja": "jpn", "ko": "kor",
    "da": "dan", "sv": "swe", "fi": "fin", "no": "nor",
    "pl": "pol", "cs": "ces", "ro": "ron", "hu": "hun",
}

# Languages routed to Google Cloud Vision when credentials are available
VISION_LANGS: set[str] = {"ar", "zh"}

# ── OCR LLM correction ────────────────────────────────────────────────────────

from model_config import MODEL as OCR_CORRECTION_MODEL
OCR_PAGE_MAX_CHARS   = 6_000   # pages larger than this are not sent for correction

_CORRECTION_PROMPT = (
    "Below is raw OCR output from a scanned page of a BWC (Biological Weapons Convention) "
    "Confidence-Building Measure submission. Correct obvious OCR errors "
    "(misread characters, spurious hyphens, line-break artifacts, garbled punctuation) "
    "while preserving all content faithfully. Preserve the original language — "
    "do NOT translate, paraphrase, add explanations, or remove any content. "
    "Return only the corrected text, nothing else."
)


# ── pdfplumber extraction ──────────────────────────────────────────────────────


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


# ── OCR ───────────────────────────────────────────────────────────────────────


def _ocr_engine_for(language: str) -> str:
    """Return 'google-vision' or 'tesseract' for the given ISO 639-1 language."""
    if language in VISION_LANGS and _VISION_OK and os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        return "google-vision"
    return "tesseract"


def _ocr_page_tesseract(image, language: str) -> str:
    lang_code = TESSERACT_LANG_MAP.get(language, "eng")
    # Use automatic page segmentation with orientation detection
    return pytesseract.image_to_string(image, lang=lang_code, config="--psm 1")


def _ocr_page_vision(image) -> str:
    client = _gvision.ImageAnnotatorClient()
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    vision_image = _gvision.Image(content=buf.getvalue())
    response = client.document_text_detection(image=vision_image)
    return response.full_text_annotation.text if response.full_text_annotation else ""


def ocr_pdf(
    pdf_path: Path,
    language: str,
    entry_id: str,
) -> tuple[list[dict], list[str], int, str] | None:
    """
    Convert a PDF to images and OCR each page.

    Returns (pages_data, page_texts, total_chars, engine_name) or None on failure.
    Engine is 'google-vision' or 'tesseract'.
    """
    if not _PDF2IMAGE_OK:
        log.error("[%s] pdf2image not installed; cannot OCR", entry_id)
        return None
    if not _TESSERACT_OK:
        log.error("[%s] pytesseract not installed; cannot OCR", entry_id)
        return None

    engine = _ocr_engine_for(language)
    log.info("[%s] Running OCR with %s (lang=%s)…", entry_id, engine, language)

    try:
        images = _pdf2images(str(pdf_path), dpi=OCR_DPI)
    except Exception as exc:
        log.error("[%s] pdf2image failed: %s", entry_id, exc)
        return None

    pages_data: list[dict] = []
    page_texts: list[str] = []
    total_chars = 0

    for page_num, image in enumerate(images, start=1):
        try:
            if engine == "google-vision":
                text = _ocr_page_vision(image)
            else:
                text = _ocr_page_tesseract(image, language)
        except Exception as exc:
            log.warning("[%s] OCR failed on page %d: %s", entry_id, page_num, exc)
            text = ""

        char_count = len(text)
        total_chars += char_count
        pages_data.append({
            "page":       page_num,
            "text":       text,
            "char_count": char_count,
            "has_tables": False,  # OCR does not produce TSV tables
            "ocr":        True,
            "ocr_engine": engine,
        })
        page_texts.append(f"--- PAGE {page_num} ---\n{text}")

    return pages_data, page_texts, total_chars, engine


# ── OCR LLM correction ────────────────────────────────────────────────────────


def correct_ocr_pages(
    pages_data: list[dict],
    language: str,
    entry_id: str,
    client: anthropic.Anthropic,
) -> tuple[list[dict], list[str]]:
    """Send each OCR page to Claude for error correction.

    Pages that are empty or exceed OCR_PAGE_MAX_CHARS are returned unchanged.
    Returns (corrected_pages_data, corrected_page_texts).
    """
    corrected_pages: list[dict] = []
    corrected_texts: list[str] = []

    for page in pages_data:
        original = page.get("text", "")
        page_num = page["page"]

        if not original.strip():
            corrected_pages.append(page)
            corrected_texts.append(f"--- PAGE {page_num} ---\n{original}")
            continue

        if len(original) > OCR_PAGE_MAX_CHARS:
            log.warning(
                "[%s] Page %d too large for OCR correction (%d chars) — skipping",
                entry_id, page_num, len(original),
            )
            corrected_pages.append(page)
            corrected_texts.append(f"--- PAGE {page_num} ---\n{original}")
            continue

        try:
            resp = client.messages.create(
                model=OCR_CORRECTION_MODEL,
                max_tokens=4096,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"{_CORRECTION_PROMPT}\n\n"
                            f"Language: {language}\n\n"
                            f"--- OCR TEXT ---\n{original}\n--- END OCR TEXT ---"
                        ),
                    }
                ],
            )
            corrected = resp.content[0].text.strip()
        except Exception as exc:
            log.warning(
                "[%s] OCR correction failed on page %d: %s — keeping original",
                entry_id, page_num, exc,
            )
            corrected = original

        corrected_pages.append({
            **page,
            "text":          corrected,
            "char_count":    len(corrected),
            "ocr_corrected": True,
        })
        corrected_texts.append(f"--- PAGE {page_num} ---\n{corrected}")

    return corrected_pages, corrected_texts


def correct_existing_ocr(entry: dict, client: anthropic.Anthropic) -> dict | None:
    """Apply LLM OCR correction to an already-OCR'd document.

    Reads the existing _pages.json, corrects each page, rewrites .txt and
    _pages.json, and returns an updated entry dict (with ocr_corrected=True).
    Returns None on error.
    """
    entry_id   = entry["id"]
    pages_path = EXTRACTED_DIR / f"{entry_id}_pages.json"
    txt_path   = EXTRACTED_DIR / f"{entry_id}.txt"

    if not pages_path.exists():
        log.error("[%s] _pages.json not found; cannot apply OCR correction", entry_id)
        return None

    pages_data = json.loads(pages_path.read_text(encoding="utf-8"))
    language   = entry.get("language", "en")

    log.info("[%s] Applying OCR correction (%d pages, lang=%s)…", entry_id, len(pages_data), language)

    corrected_pages, corrected_texts = correct_ocr_pages(pages_data, language, entry_id, client)

    total_chars = sum(p["char_count"] for p in corrected_pages)
    page_count  = len(corrected_pages)
    avg_chars   = total_chars / page_count if page_count else 0

    txt_path.write_text("\n\n".join(corrected_texts), encoding="utf-8")
    pages_path.write_text(
        json.dumps(corrected_pages, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    log.info(
        "[%s] OCR correction done: %d pages | %d chars | avg %.0f/page",
        entry_id, page_count, total_chars, avg_chars,
    )

    return {
        **entry,
        "total_char_count":   total_chars,
        "avg_chars_per_page": round(avg_chars, 1),
        "ocr_corrected":      True,
    }


# ── Per-document processing ───────────────────────────────────────────────────


def process_pdf(entry: dict, *, force: bool = False) -> dict | None:
    """Process a single catalogue entry.

    If OCR is used, LLM correction is applied automatically via _get_anthropic_client().
    Returns an updated copy of the entry dict, or None on unrecoverable error.
    Skips entries whose extracted text already exists unless *force* is True.
    """
    entry_id = entry["id"]
    pdf_path = PROJECT_ROOT / entry["local_path"]

    if not pdf_path.exists():
        log.warning("[%s] PDF not found: %s", entry_id, pdf_path)
        return None

    # Skip if already extracted (incremental mode)
    txt_path = EXTRACTED_DIR / f"{entry_id}.txt"
    if not force and txt_path.exists() and txt_path.stat().st_size > 0:
        return entry  # already processed

    # ── pdfplumber pass ───────────────────────────────────────────────────────
    pages_data: list[dict] = []
    page_texts: list[str] = []
    total_chars = 0

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                page_text, has_tables = extract_page_content(page)
                char_count = len(page_text)
                total_chars += char_count
                pages_data.append({
                    "page":       page_num,
                    "text":       page_text,
                    "char_count": char_count,
                    "has_tables": has_tables,
                })
                page_texts.append(f"--- PAGE {page_num} ---\n{page_text}")
    except Exception as exc:
        log.error("[%s] pdfplumber extraction failed: %s", entry_id, exc)
        return None

    page_count = len(pages_data)
    if page_count == 0:
        log.warning("[%s] PDF has no pages", entry_id)
        return None

    avg_chars = total_chars / page_count
    needs_ocr = avg_chars < OCR_CHAR_THRESHOLD

    updated = {
        **entry,
        "page_count":        page_count,
        "total_char_count":  total_chars,
        "avg_chars_per_page": round(avg_chars, 1),
        "needs_ocr":         needs_ocr,
    }

    # ── OCR pass (if needed) ──────────────────────────────────────────────────
    if needs_ocr:
        log.warning(
            "[%s] Low text density (avg %.1f chars/page) — attempting OCR",
            entry_id, avg_chars,
        )
        language = entry.get("language", "en")
        ocr_result = ocr_pdf(pdf_path, language, entry_id)

        if ocr_result is None:
            log.error("[%s] OCR failed; document remains flagged needs_ocr", entry_id)
            return updated  # still needs_ocr=True

        pages_data, page_texts, total_chars, ocr_engine = ocr_result
        page_count = len(pages_data)
        avg_chars  = total_chars / page_count if page_count else 0

        updated.update({
            "page_count":        page_count,
            "total_char_count":  total_chars,
            "avg_chars_per_page": round(avg_chars, 1),
            "needs_ocr":         False,
            "ocr_engine":        ocr_engine,
        })
        log.info(
            "[%s] OCR complete: %d pages | %d chars | avg %.0f/page (engine=%s)",
            entry_id, page_count, total_chars, avg_chars, ocr_engine,
        )

        # ── LLM correction pass ───────────────────────────────────────────────
        client = _get_anthropic_client()
        pages_data, page_texts = correct_ocr_pages(pages_data, language, entry_id, client)
        total_chars = sum(p["char_count"] for p in pages_data)
        avg_chars   = total_chars / len(pages_data) if pages_data else 0
        updated.update({
            "total_char_count":   total_chars,
            "avg_chars_per_page": round(avg_chars, 1),
            "ocr_corrected":      True,
        })

    # ── Write outputs ─────────────────────────────────────────────────────────
    EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
    txt_path   = EXTRACTED_DIR / f"{entry_id}.txt"
    pages_path = EXTRACTED_DIR / f"{entry_id}_pages.json"

    txt_path.write_text("\n\n".join(page_texts), encoding="utf-8")
    pages_path.write_text(
        json.dumps(pages_data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    if not updated.get("needs_ocr"):
        n_table_pages = sum(1 for p in pages_data if p.get("has_tables"))
        n_ocr_pages   = sum(1 for p in pages_data if p.get("ocr"))
        log.info(
            "[%s] %d pages | %d chars | avg %.0f/page%s%s",
            entry_id, page_count, total_chars, avg_chars,
            f" | {n_table_pages} table pages" if n_table_pages else "",
            f" | {n_ocr_pages} OCR pages"     if n_ocr_pages   else "",
        )

    return updated


# ── Main ─────────────────────────────────────────────────────────────────────


_anthropic_client: anthropic.Anthropic | None = None


def _get_anthropic_client() -> anthropic.Anthropic:
    """Return (or lazily create) the shared Anthropic client."""
    global _anthropic_client
    if _anthropic_client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            log.error("ANTHROPIC_API_KEY not set in environment or .env")
            raise SystemExit(1)
        _anthropic_client = anthropic.Anthropic(api_key=api_key)
    return _anthropic_client


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--single",
        metavar="ID",
        help="Process only the entry with this id (e.g. USA_2023).",
    )
    parser.add_argument(
        "--ocr-only",
        action="store_true",
        help="Reprocess only documents currently flagged needs_ocr=True.",
    )
    parser.add_argument(
        "--correct-ocr",
        action="store_true",
        help="Apply LLM correction to docs with ocr_engine set but ocr_corrected=False.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-extract text even if output already exists (override incremental skip).",
    )
    args = parser.parse_args()

    if not CATALOGUE_PATH.exists():
        log.error("Catalogue not found: %s", CATALOGUE_PATH)
        raise SystemExit(1)

    catalogue: list[dict] = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))
    catalogue_index: dict[str, dict] = {e["id"]: e for e in catalogue}

    # ── --correct-ocr mode ────────────────────────────────────────────────────
    if args.correct_ocr:
        targets = [
            e for e in catalogue
            if e.get("ocr_engine") and not e.get("ocr_corrected")
        ]
        log.info("--correct-ocr mode: %d documents to correct", len(targets))
        client = _get_anthropic_client()
        n_corrected = n_failed = 0
        for entry in targets:
            result = correct_existing_ocr(entry, client)
            if result:
                catalogue_index[result["id"]] = result
                n_corrected += 1
            else:
                n_failed += 1
        updated_list = [catalogue_index[e["id"]] for e in catalogue]
        CATALOGUE_PATH.write_text(
            json.dumps(updated_list, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        log.info("Catalogue updated: %s", CATALOGUE_PATH)
        print("\n── Summary ──────────────────────────────────────────────────")
        print(f"  Documents OCR-corrected:           {n_corrected}")
        if n_failed:
            print(f"  Errors:                            {n_failed}")
        print("─────────────────────────────────────────────────────────────\n")
        return

    # ── Normal / --ocr-only / --single modes ──────────────────────────────────
    if args.single:
        targets = [e for e in catalogue if e["id"] == args.single]
        if not targets:
            log.error("No entry with id=%r in catalogue", args.single)
            raise SystemExit(1)
        log.info("--single mode: processing %s", args.single)
    elif args.ocr_only:
        targets = [e for e in catalogue if e.get("downloaded") and e.get("needs_ocr")]
        log.info("--ocr-only mode: %d documents flagged needs_ocr", len(targets))
    else:
        targets = [e for e in catalogue if e.get("downloaded")]
        log.info("Processing %d downloaded PDFs", len(targets))

    n_processed = n_ocr_done = n_ocr_failed = n_failed = 0
    total_pages = 0

    for entry in targets:
        result = process_pdf(entry, force=getattr(args, 'force', False))
        if result is None:
            n_failed += 1
            continue

        catalogue_index[result["id"]] = result

        if result.get("needs_ocr"):
            n_ocr_failed += 1
        elif result.get("ocr_engine"):
            n_ocr_done += 1
            total_pages += result.get("page_count", 0)
        else:
            n_processed += 1
            total_pages += result.get("page_count", 0)

    # Persist catalogue
    updated_list = [catalogue_index[e["id"]] for e in catalogue]
    CATALOGUE_PATH.write_text(
        json.dumps(updated_list, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    log.info("Catalogue updated: %s", CATALOGUE_PATH)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n── Summary ──────────────────────────────────────────────────")
    print(f"  Documents processed (pdfplumber):  {n_processed}")
    if n_ocr_done:
        print(f"  Documents processed (OCR):         {n_ocr_done}")
    if n_ocr_failed:
        print(f"  OCR failed / still needs_ocr:      {n_ocr_failed}")
    if n_failed:
        print(f"  Errors / not found:                {n_failed}")
    print(f"  Total pages extracted:             {total_pages}")
    print("─────────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
