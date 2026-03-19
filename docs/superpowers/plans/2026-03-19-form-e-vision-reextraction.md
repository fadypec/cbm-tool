# Form E Vision Re-extraction Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace text-only Form E extraction with Claude Vision to correctly read strikethrough/underline/bold/circled Yes/No values that are invisible in extracted text.

**Architecture:** Add two helper functions to script 04 (`_form_e_page_numbers` and `_render_pages_as_base64`) and a vision-aware system prompt (`SYSTEM_PROMPT_E_VISION`). Modify `process_entry_e()` to render the Form E PDF pages as images and send them to Claude Vision alongside the existing text. Falls back to text-only if page rendering fails.

**Tech Stack:** `pdf2image` (already in requirements.txt), `base64` (stdlib), Anthropic Vision API (Claude `claude-sonnet-4-20250514`, already used throughout).

---

### Task 1: Add page-number parser and image-rendering helpers

**Files:**
- Modify: `scripts/04_extract_structured.py` (add two functions after `FORM_E_MAX_CHARS` constant, around line 104)

- [ ] **Step 1: Add the `RAW_PDFS_DIR` constant**

After the `STRUCTURED_DIR` line (~line 55), add:

```python
RAW_PDFS_DIR   = PROJECT_ROOT / "data" / "raw_pdfs"
```

- [ ] **Step 2: Add `base64` and `io` imports**

At the top of the file with the existing stdlib imports (~line 24–31), add `base64` and `io`:

```python
import base64
import io
```

- [ ] **Step 3: Write `_form_e_page_numbers()`**

Add after `FORM_E_MAX_CHARS = None` (~line 104):

```python
# ── Form E Vision helpers ────────────────────────────────────────────────

_PAGE_MARKER_RE = re.compile(r"^--- PAGE (\d+) ---$", re.MULTILINE)


def _form_e_page_numbers(form_text_path: Path) -> list[int]:
    """Extract 1-indexed PDF page numbers from segmented form_e.txt markers.

    The segmentation script inserts '--- PAGE N ---' headers.  Returns a
    sorted, deduplicated list of page numbers found in the file.
    """
    text = form_text_path.read_text(encoding="utf-8")
    return sorted(set(int(m.group(1)) for m in _PAGE_MARKER_RE.finditer(text)))
```

- [ ] **Step 4: Write `_render_pages_as_base64()`**

Add directly after `_form_e_page_numbers`:

```python
def _render_pages_as_base64(
    pdf_path: Path,
    pages: list[int],
    dpi: int = 150,
) -> list[dict]:
    """Render specific PDF pages to base64-encoded PNG content blocks.

    Returns a list of Anthropic Vision content blocks ready for insertion
    into a messages list:
        [{"type": "image", "source": {"type": "base64",
          "media_type": "image/png", "data": "..."}}, ...]

    Uses 150 DPI — sufficient for readable text while keeping each image
    under ~500 KB (well within Anthropic's 5 MB per-image limit).
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
```

- [ ] **Step 5: Verify helpers compile**

Run: `python3 -c "from scripts import __init__" 2>&1 || python3 -c "import scripts.extract_structured" 2>&1 || echo "check syntax manually"`

Actually, just run:
```bash
python3 -c "
import ast, sys
ast.parse(open('scripts/04_extract_structured.py').read())
print('Syntax OK')
"
```
Expected: `Syntax OK`

- [ ] **Step 6: Commit**

```bash
git add scripts/04_extract_structured.py
git commit -m "feat(form-e): add page-number parser and image-rendering helpers"
```

---

### Task 2: Add the vision-aware system prompt

**Files:**
- Modify: `scripts/04_extract_structured.py` (add `SYSTEM_PROMPT_E_VISION` after `SYSTEM_PROMPT_E`)

- [ ] **Step 1: Write `SYSTEM_PROMPT_E_VISION`**

Add directly after `SYSTEM_PROMPT_E` (~line 342):

```python
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
different ways — strikethrough on the rejected option, bold/underline on \
the selected option, circling, or handwritten marks.  Read whatever visual \
formatting is present to determine the intended value.

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
  (6) only leave a field null if genuinely no indication exists

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
```

- [ ] **Step 2: Verify syntax**

```bash
python3 -c "
import ast
ast.parse(open('scripts/04_extract_structured.py').read())
print('Syntax OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add scripts/04_extract_structured.py
git commit -m "feat(form-e): add vision-aware system prompt for Form E"
```

---

### Task 3: Modify `process_entry_e()` to use Vision

**Files:**
- Modify: `scripts/04_extract_structured.py` — rewrite `process_entry_e()` (~lines 1165–1203)

- [ ] **Step 1: Rewrite `process_entry_e()`**

Replace the existing function with:

```python
def process_entry_e(
    entry: dict, client, last_t: list[float],
    max_chars: int | None = FORM_E_MAX_CHARS,
) -> dict:
    """Extract Form E (legislation) for one document.

    Preferred path: render the Form E pages as images and send to Claude
    Vision, which can read strikethrough / bold / underline / circled
    formatting that plain text extraction loses.

    Falls back to text-only extraction if the source PDF is missing or
    page rendering fails.
    """
    entry_id  = entry["id"]
    form_path = SEGMENTED_DIR / entry_id / "form_e.txt"
    out_path  = STRUCTURED_DIR / f"{entry_id}_form_e.json"

    if out_path.exists():
        return {"id": entry_id, "status": "skipped"}

    if not form_path.exists():
        return {"id": entry_id, "status": "no_form_e"}

    text = form_path.read_text(encoding="utf-8")

    # ── Try vision path: render PDF pages as images ──────────────────
    used_vision = False
    pdf_path = RAW_PDFS_DIR / f"{entry_id}.pdf"
    if pdf_path.exists():
        page_nums = _form_e_page_numbers(form_path)
        if page_nums:
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
                used_vision = True
            except Exception as exc:
                log.warning("[%s/E] Vision rendering failed, falling back to "
                            "text: %s", entry_id, exc)

    # ── Fallback: text-only extraction ───────────────────────────────
    if not used_vision:
        if max_chars and len(text) > max_chars:
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
    mode = "vision" if used_vision else "text"
    log.info("[%s/E] (%s) prohibitions=%s exports=%s | in=%d out=%d tokens",
             entry_id, mode,
             (cats.get("prohibitions") or {}).get("legislation"),
             (cats.get("exports") or {}).get("legislation"),
             usage["input_tokens"], usage["output_tokens"])

    truncated = (not used_vision and max_chars is not None
                 and len(form_path.read_text(encoding="utf-8")) > (max_chars or 0))
    _write_output_e(out_path, entry, data, usage, truncated=truncated)
    return {"id": entry_id, "status": "ok", "calls": 1, **usage}
```

- [ ] **Step 2: Verify syntax**

```bash
python3 -c "
import ast
ast.parse(open('scripts/04_extract_structured.py').read())
print('Syntax OK')
"
```

- [ ] **Step 3: Smoke-test with one Austrian document**

```bash
source .venv/bin/activate
# Delete existing output to force re-extraction
rm -f data/structured/AUT_2023_form_e.json
python3 scripts/04_extract_structured.py --form-e --single AUT_2023
cat data/structured/AUT_2023_form_e.json | python3 -m json.tool | head -30
```

Expected: JSON with `"prohibitions": {"legislation": true, ...}` — not null.
The log line should show `(vision)` mode.

- [ ] **Step 4: Commit**

```bash
git add scripts/04_extract_structured.py
git commit -m "feat(form-e): use Claude Vision for Form E extraction

Renders Form E PDF pages as images and sends them to Claude Vision,
which can read strikethrough/underline/bold/circled Yes/No formatting
that plain text extraction loses. Falls back to text-only if the
source PDF is missing or page rendering fails."
```

---

### Task 4: Re-extract all Form E documents

**Files:**
- No code changes — execution only

- [ ] **Step 1: Delete all existing Form E JSONs**

```bash
rm -f data/structured/*_form_e.json
ls data/structured/*_form_e.json 2>/dev/null | wc -l  # should be 0
```

- [ ] **Step 2: Run full Form E re-extraction**

```bash
source .venv/bin/activate
python3 scripts/04_extract_structured.py --form-e
```

Expected: ~343 documents processed, ~$3–5 total cost, ~1 hour runtime at ≤6 RPM.
Watch for `(vision)` in log lines and verify no mass failures.

- [ ] **Step 3: Verify improvement — count non-null booleans**

```bash
python3 -c "
import json, pathlib
files = sorted(pathlib.Path('data/structured').glob('*_form_e.json'))
null_count = 0
for f in files:
    d = json.loads(f.read_text())
    cats = d.get('categories') or {}
    all_null = all(
        (cats.get(c) or {}).get('legislation') is None
        for c in ['prohibitions', 'exports', 'imports', 'biosafety']
    )
    if all_null and d.get('status') != 'no_form_e':
        null_count += 1
        print(f'  ALL-NULL: {f.name}')
print(f'\nTotal: {len(files)} files, {null_count} all-null')
"
```

Expected: null count drops from 41 to near 0 (some may be genuinely blank forms).

- [ ] **Step 4: Commit the re-extracted data is not tracked by git — no commit needed for JSON files. Proceed to Task 5.**

---

### Task 5: Rebuild pipeline outputs and reload database

**Files:**
- No code changes — execution of existing pipeline

- [ ] **Step 1: Reassemble output CSVs**

```bash
source .venv/bin/activate
python3 scripts/05_assemble_output.py
```

Verify: `data/output/legislation.csv` is regenerated. Check row count matches.

- [ ] **Step 2: Reload legislation into local database**

```bash
export PATH="/opt/homebrew/opt/postgresql@17/bin:$PATH"
psql postgresql://cbm:cbm@localhost:5432/cbm -c "DELETE FROM legislation;"
python3 scripts/06_load_database.py
```

- [ ] **Step 3: Verify local improvement**

```bash
psql postgresql://cbm:cbm@localhost:5432/cbm -c "
SELECT COUNT(*) AS total,
       COUNT(*) FILTER (WHERE prohibitions_legislation IS NULL
                          AND exports_legislation IS NULL
                          AND biosafety_legislation IS NULL) AS all_null
FROM legislation;"
```

Expected: `all_null` drops from 41 to near 0.

- [ ] **Step 4: Reload Supabase production database**

```bash
DATABASE_URL="$SUPABASE_URL" \
psql "$DATABASE_URL" -c "DELETE FROM legislation;"

DATABASE_URL="$SUPABASE_URL" \
python3 scripts/06_load_database.py
```

- [ ] **Step 5: Verify production improvement**

```bash
psql "$SUPABASE_URL" -c "
SELECT COUNT(*) AS total,
       COUNT(*) FILTER (WHERE prohibitions_legislation IS NULL
                          AND exports_legislation IS NULL
                          AND biosafety_legislation IS NULL) AS all_null
FROM legislation;"
```

- [ ] **Step 6: Commit code changes and push**

```bash
git add scripts/04_extract_structured.py
git commit -m "feat: Form E vision re-extraction — all 343 documents re-processed

Uses Claude Vision on Form E PDF pages to correctly read strikethrough,
underline, bold, and circled Yes/No formatting. Previously 41 records
(12%) had all-NULL booleans from lost formatting; expected near-zero
after re-extraction."
git push
```
