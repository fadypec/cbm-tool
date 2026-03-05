#!/usr/bin/env python3
"""
check_form_e_truncation.py — Sense-check Form E truncation at FORM_E_MAX_CHARS.

Selects a weighted-random sample of form_e.txt files (weighted toward larger
files) and extracts each twice: once with full text and once truncated at
FORM_E_MAX_CHARS. Compares boolean table values and key_laws counts.

Usage:
    python scripts/check_form_e_truncation.py [--n 15]
"""

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv

# ── project path bootstrap ────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

# Import from script 04 directly
from importlib.util import spec_from_file_location, module_from_spec

_spec = spec_from_file_location("s04", PROJECT_ROOT / "scripts" / "04_extract_structured.py")
_s04 = module_from_spec(_spec)
_spec.loader.exec_module(_s04)

SYSTEM_PROMPT_E  = _s04.SYSTEM_PROMPT_E
FORM_E_MAX_CHARS = _s04.FORM_E_MAX_CHARS
api_call         = _s04.api_call
parse_json_response = _s04.parse_json_response

SEGMENTED_DIR = PROJECT_ROOT / "data" / "segmented"
RATE_LIMIT_DELAY = 12.0   # conservative; two calls per doc


def _call(client, text: str, last_t: list[float]) -> tuple[dict, int, int]:
    """Call API and return (parsed JSON dict, input_tokens, output_tokens)."""
    messages = [{"role": "user", "content": text}]
    resp = api_call(client, messages, last_t, system=SYSTEM_PROMPT_E)
    raw = resp.content[0].text.strip()
    data = parse_json_response(raw)
    if data is None:
        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "user", "content": "Return only valid JSON, no other text."})
        resp2 = api_call(client, messages, last_t, system=SYSTEM_PROMPT_E)
        data = parse_json_response(resp2.content[0].text.strip())
    tokens_in  = resp.usage.input_tokens
    tokens_out = resp.usage.output_tokens
    return data or {}, tokens_in, tokens_out


def _cat_bools(cats: dict, cat: str) -> str:
    """Summarise one category as 'L=T R=T O=T A=F' style."""
    c = (cats or {}).get(cat) or {}
    def b(v):
        if v is True:  return "Y"
        if v is False: return "N"
        return "?"
    return f"leg={b(c.get('legislation'))} reg={b(c.get('regulations'))} oth={b(c.get('other_measures'))} amd={b(c.get('amended'))}"


def _compare(doc_id: str, full: dict, trunc: dict) -> list[str]:
    """Return list of field-level differences between full and truncated extractions."""
    diffs = []
    for cat in ("prohibitions", "exports", "imports", "biosafety"):
        fc = (full.get("categories") or {}).get(cat) or {}
        tc = (trunc.get("categories") or {}).get(cat) or {}
        for field in ("legislation", "regulations", "other_measures", "amended"):
            fv, tv = fc.get(field), tc.get(field)
            if fv != tv:
                diffs.append(f"  categories.{cat}.{field}: full={fv!r}  trunc={tv!r}")
    fl = full.get("key_laws") or []
    tl = trunc.get("key_laws") or []
    if len(fl) != len(tl):
        diffs.append(f"  key_laws count: full={len(fl)}  trunc={len(tl)}")
    return diffs


def weighted_sample(paths_sizes: list[tuple[Path, int]], n: int) -> list[tuple[Path, int]]:
    """
    Sample n items, weighted by file size.  Larger files get proportionally
    higher selection probability, ensuring the long tail is well represented.
    """
    paths, sizes = zip(*paths_sizes)
    total = sum(sizes)
    weights = [s / total for s in sizes]
    chosen = set()
    result = []
    while len(result) < min(n, len(paths_sizes)):
        idx = random.choices(range(len(paths)), weights=weights, k=1)[0]
        if idx not in chosen:
            chosen.add(idx)
            result.append(paths_sizes[idx])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=15,
                        help="Number of docs to sample (default 15).")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)

    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set"); sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    last_t: list[float] = [0.0]

    # Collect all form_e.txt files with sizes
    all_files: list[tuple[Path, int]] = []
    for p in sorted(SEGMENTED_DIR.glob("*/form_e.txt")):
        size = p.stat().st_size
        if size > 200:   # skip near-empty stubs
            all_files.append((p, size))

    sample = weighted_sample(all_files, args.n)
    sample.sort(key=lambda x: -x[1])   # largest first for display

    print(f"\nSampled {len(sample)} docs (weighted by size, seed={args.seed})")
    print(f"Size range: {sample[-1][1]:,} – {sample[0][1]:,} chars")
    print(f"Truncation threshold: {FORM_E_MAX_CHARS:,} chars\n")
    print(f"{'Doc ID':<18} {'Size':>7}  {'Trunc?':>6}  "
          f"{'Prohibitions (full)':>28}  {'Prohibitions (trunc)':>28}  {'Diffs'}")
    print("─" * 110)

    total_in = total_out = 0
    any_diff = False

    for path, size in sample:
        doc_id = path.parent.name
        text_full = path.read_text(encoding="utf-8")
        truncated = len(text_full) > FORM_E_MAX_CHARS
        text_trunc = text_full[:FORM_E_MAX_CHARS] if truncated else text_full

        # Full extraction
        full_data, fin, fout = _call(client, text_full, last_t)
        total_in += fin; total_out += fout

        if truncated:
            trunc_data, tin, tout = _call(client, text_trunc, last_t)
            total_in += tin; total_out += tout
        else:
            trunc_data = full_data   # identical text → no need for second call

        diffs = _compare(doc_id, full_data, trunc_data)
        diff_str = f"{len(diffs)} field(s)" if diffs else "none"
        if diffs:
            any_diff = True

        fp = _cat_bools(full_data.get("categories"), "prohibitions")
        tp = _cat_bools(trunc_data.get("categories"), "prohibitions")
        trunc_flag = f"{size:>7,}*" if truncated else f"{size:>7,} "

        print(f"{doc_id:<18} {trunc_flag}         {fp}  {tp}  {diff_str}")
        for d in diffs:
            print(d)

    cost = total_in * 3e-6 + total_out * 15e-6
    print("─" * 110)
    print(f"\nTokens: {total_in:,} in / {total_out:,} out  (~${cost:.3f})")
    print(f"\nVerdict: {'⚠ DIFFERENCES FOUND — review above' if any_diff else '✓ No boolean differences — truncation is safe'}")
    print()


if __name__ == "__main__":
    main()
