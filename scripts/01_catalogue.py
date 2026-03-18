#!/usr/bin/env python3
"""
01_catalogue.py — Discover, download, and catalogue BWC CBM PDF submissions.

Source: UN CBM portal (bwc-cbm.un.org) — uses a JSON search API to
enumerate all public reports (~500+), then downloads each PDF from the Strapi
backend at cms-bwc-cbm.un.org.

Downloads go to data/raw_pdfs/ with standardised filenames, and results are
written to data/catalogue.json.

Usage:
    python scripts/01_catalogue.py
    python scripts/01_catalogue.py --skip-download
"""

import argparse
import json
import logging
import re
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Logging ────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_PDFS_DIR = PROJECT_ROOT / "data" / "raw_pdfs"
CATALOGUE_PATH = PROJECT_ROOT / "data" / "catalogue.json"

# ── Constants ──────────────────────────────────────────────────────────────

HEADERS = {"User-Agent": "BWC-CBM-Research-Tool/0.1 (academic research)"}
DOWNLOAD_DELAY = 2  # seconds between PDF downloads

# ── UN portal API (primary source) ─────────────────────────────────────────
UN_SEARCH_URL = "https://bwc-cbm.un.org/api/search/"
UN_DOWNLOAD_URL = "https://cms-bwc-cbm.un.org/api/getDocument"
UN_SEARCH_PAGE_SIZE = 100

# Language name → ISO-639-1 code (as returned by UN portal)
LANGUAGE_CODE_MAP: dict[str, str] = {
    "english": "en",
    "french": "fr",
    "spanish": "es",
    "arabic": "ar",
    "chinese": "zh",
    "russian": "ru",
    "german": "de",
    "portuguese": "pt",
}

# ── Country mappings ───────────────────────────────────────────────────────
# ISO-2 → (ISO-3, full English name)

ISO2_MAP: dict[str, tuple[str, str]] = {
    "AF": ("AFG", "Afghanistan"),
    "AL": ("ALB", "Albania"),
    "DZ": ("DZA", "Algeria"),
    "AO": ("AGO", "Angola"),
    "AR": ("ARG", "Argentina"),
    "AM": ("ARM", "Armenia"),
    "AU": ("AUS", "Australia"),
    "AT": ("AUT", "Austria"),
    "AZ": ("AZE", "Azerbaijan"),
    "BH": ("BHR", "Bahrain"),
    "BD": ("BGD", "Bangladesh"),
    "BY": ("BLR", "Belarus"),
    "BE": ("BEL", "Belgium"),
    "BJ": ("BEN", "Benin"),
    "BO": ("BOL", "Bolivia"),
    "BA": ("BIH", "Bosnia and Herzegovina"),
    "BW": ("BWA", "Botswana"),
    "BR": ("BRA", "Brazil"),
    "BG": ("BGR", "Bulgaria"),
    "BF": ("BFA", "Burkina Faso"),
    "KH": ("KHM", "Cambodia"),
    "CM": ("CMR", "Cameroon"),
    "CA": ("CAN", "Canada"),
    "CF": ("CAF", "Central African Republic"),
    "TD": ("TCD", "Chad"),
    "CL": ("CHL", "Chile"),
    "CN": ("CHN", "China"),
    "CO": ("COL", "Colombia"),
    "CG": ("COG", "Congo"),
    "CD": ("COD", "DR Congo"),
    "CR": ("CRI", "Costa Rica"),
    "HR": ("HRV", "Croatia"),
    "CU": ("CUB", "Cuba"),
    "CY": ("CYP", "Cyprus"),
    "CZ": ("CZE", "Czech Republic"),
    "DK": ("DNK", "Denmark"),
    "DJ": ("DJI", "Djibouti"),
    "EC": ("ECU", "Ecuador"),
    "EG": ("EGY", "Egypt"),
    "ER": ("ERI", "Eritrea"),
    "EE": ("EST", "Estonia"),
    "ET": ("ETH", "Ethiopia"),
    "FI": ("FIN", "Finland"),
    "FR": ("FRA", "France"),
    "GA": ("GAB", "Gabon"),
    "GM": ("GMB", "Gambia"),
    "GE": ("GEO", "Georgia"),
    "DE": ("DEU", "Germany"),
    "GH": ("GHA", "Ghana"),
    "GR": ("GRC", "Greece"),
    "GT": ("GTM", "Guatemala"),
    "GN": ("GIN", "Guinea"),
    "GY": ("GUY", "Guyana"),
    "HT": ("HTI", "Haiti"),
    "HU": ("HUN", "Hungary"),
    "IS": ("ISL", "Iceland"),
    "IN": ("IND", "India"),
    "ID": ("IDN", "Indonesia"),
    "IR": ("IRN", "Iran"),
    "IQ": ("IRQ", "Iraq"),
    "IE": ("IRL", "Ireland"),
    "IL": ("ISR", "Israel"),
    "IT": ("ITA", "Italy"),
    "JP": ("JPN", "Japan"),
    "JO": ("JOR", "Jordan"),
    "KZ": ("KAZ", "Kazakhstan"),
    "KE": ("KEN", "Kenya"),
    "KP": ("PRK", "North Korea"),
    "KR": ("KOR", "South Korea"),
    "KW": ("KWT", "Kuwait"),
    "KG": ("KGZ", "Kyrgyzstan"),
    "LA": ("LAO", "Laos"),
    "LV": ("LVA", "Latvia"),
    "LB": ("LBN", "Lebanon"),
    "LY": ("LBY", "Libya"),
    "LT": ("LTU", "Lithuania"),
    "LU": ("LUX", "Luxembourg"),
    "MG": ("MDG", "Madagascar"),
    "MW": ("MWI", "Malawi"),
    "MY": ("MYS", "Malaysia"),
    "ML": ("MLI", "Mali"),
    "MT": ("MLT", "Malta"),
    "MR": ("MRT", "Mauritania"),
    "MU": ("MUS", "Mauritius"),
    "MX": ("MEX", "Mexico"),
    "MD": ("MDA", "Moldova"),
    "MN": ("MNG", "Mongolia"),
    "ME": ("MNE", "Montenegro"),
    "MA": ("MAR", "Morocco"),
    "MZ": ("MOZ", "Mozambique"),
    "MM": ("MMR", "Myanmar"),
    "NA": ("NAM", "Namibia"),
    "NP": ("NPL", "Nepal"),
    "NL": ("NLD", "Netherlands"),
    "NZ": ("NZL", "New Zealand"),
    "NI": ("NIC", "Nicaragua"),
    "NE": ("NER", "Niger"),
    "NG": ("NGA", "Nigeria"),
    "MK": ("MKD", "North Macedonia"),
    "NO": ("NOR", "Norway"),
    "OM": ("OMN", "Oman"),
    "PK": ("PAK", "Pakistan"),
    "PA": ("PAN", "Panama"),
    "PY": ("PRY", "Paraguay"),
    "PE": ("PER", "Peru"),
    "PH": ("PHL", "Philippines"),
    "PL": ("POL", "Poland"),
    "PT": ("PRT", "Portugal"),
    "QA": ("QAT", "Qatar"),
    "RO": ("ROU", "Romania"),
    "RU": ("RUS", "Russia"),
    "RW": ("RWA", "Rwanda"),
    "SA": ("SAU", "Saudi Arabia"),
    "SN": ("SEN", "Senegal"),
    "RS": ("SRB", "Serbia"),
    "SL": ("SLE", "Sierra Leone"),
    "SG": ("SGP", "Singapore"),
    "SK": ("SVK", "Slovakia"),
    "SI": ("SVN", "Slovenia"),
    "SO": ("SOM", "Somalia"),
    "ZA": ("ZAF", "South Africa"),
    "SS": ("SSD", "South Sudan"),
    "ES": ("ESP", "Spain"),
    "LK": ("LKA", "Sri Lanka"),
    "SD": ("SDN", "Sudan"),
    "SE": ("SWE", "Sweden"),
    "CH": ("CHE", "Switzerland"),
    "SY": ("SYR", "Syria"),
    "TJ": ("TJK", "Tajikistan"),
    "TZ": ("TZA", "Tanzania"),
    "TH": ("THA", "Thailand"),
    "TG": ("TGO", "Togo"),
    "TN": ("TUN", "Tunisia"),
    "TR": ("TUR", "Turkey"),
    "TM": ("TKM", "Turkmenistan"),
    "UG": ("UGA", "Uganda"),
    "UA": ("UKR", "Ukraine"),
    "AE": ("ARE", "United Arab Emirates"),
    "GB": ("GBR", "United Kingdom"),
    "UK": ("GBR", "United Kingdom"),  # non-standard but common in filenames
    "US": ("USA", "United States"),
    "UY": ("URY", "Uruguay"),
    "UZ": ("UZB", "Uzbekistan"),
    "VE": ("VEN", "Venezuela"),
    "VN": ("VNM", "Vietnam"),
    "YE": ("YEM", "Yemen"),
    "ZM": ("ZMB", "Zambia"),
    "ZW": ("ZWE", "Zimbabwe"),
}

# Build ISO-3 → (ISO-3, name) lookup for filenames that use three-letter codes
ISO3_LOOKUP: dict[str, tuple[str, str]] = {v[0]: v for v in ISO2_MAP.values()}


# ── UN portal: report enumeration ──────────────────────────────────────────


def fetch_all_un_reports(session: requests.Session) -> list[dict]:
    """
    Paginate through the UN CBM portal search API and return a list of
    source-record dicts for every public report.

    Each record has:
      type, un_id, iso3, country, year, language, source_url
    """
    records: list[dict] = []
    offset = 0
    total: int | None = None

    while total is None or offset < total:
        payload = {
            "from": offset,
            "size": UN_SEARCH_PAGE_SIZE,
            "search": "",
            "filter": {"country": []},
        }
        try:
            resp = session.post(
                UN_SEARCH_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.warning("UN search API error at offset %d: %s", offset, exc)
            break

        data = resp.json()
        if total is None:
            t = data.get("total", {})
            total = t.get("value", 0) if isinstance(t, dict) else int(t or 0)
            log.info("UN portal: %d public reports available", total)

        hits = data.get("hits", [])
        if not hits:
            break

        for hit in hits:
            src = hit["_source"]
            if src.get("access") != "public":
                continue
            lang_raw = (src.get("language") or "english").lower()
            lang_code = LANGUAGE_CODE_MAP.get(lang_raw, lang_raw[:2])
            year_raw = src.get("year")
            records.append({
                "type": "un",
                "un_id": src["id"],
                "iso3": src["country"]["iso"],
                "country": src["country"]["name"],
                "year": int(year_raw) if year_raw else None,
                "language": lang_code,
                "source_url": f"https://bwc-cbm.un.org/report/detail/{src['id']}",
            })

        offset += len(hits)

    log.info("Fetched %d UN portal records", len(records))
    return records


def collect_all_sources(session: requests.Session) -> list[dict]:
    """
    Build the complete list of report source-records from the UN portal API.
    """
    return fetch_all_un_reports(session)


# ── Standardised filename assignment ──────────────────────────────────────


def assign_local_filename(meta: dict, used_ids: dict[str, int]) -> str:
    """
    Return a standardised filename like USA_2023.pdf.
    If the (iso3, year) pair has been seen before, append _2, _3, etc.
    Updates used_ids in place.
    """
    iso3 = meta["iso3"]
    year = meta["year"] or "UNKNOWN"
    base_id = f"{iso3}_{year}"

    count = used_ids.get(base_id, 0) + 1
    used_ids[base_id] = count

    if count == 1:
        return f"{base_id}.pdf"
    return f"{base_id}_{count}.pdf"


# ── Download ───────────────────────────────────────────────────────────────


def download_pdf(
    session: requests.Session, url: str, dest: Path, *, first: bool
) -> bool:
    """
    Download a PDF to dest.  Returns True on success.
    Applies DOWNLOAD_DELAY before every download except the first.
    """
    if not first:
        time.sleep(DOWNLOAD_DELAY)

    try:
        resp = session.get(url, timeout=60, stream=True)
        if resp.status_code == 404:
            log.warning("404 — skipping: %s", url)
            return False
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.error("Download failed for %s: %s", url, exc)
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as fh:
        for chunk in resp.iter_content(chunk_size=65536):
            fh.write(chunk)

    log.info("Downloaded %s → %s (%d bytes)", url, dest.name, dest.stat().st_size)
    return True


def download_pdf_from_un(
    session: requests.Session, un_id: int, dest: Path, *, first: bool
) -> bool:
    """
    Download a CBM PDF from the UN Strapi backend via POST /api/getDocument.
    language=null retrieves the original (non-translated) PDF.
    Returns True on success.
    """
    if not first:
        time.sleep(DOWNLOAD_DELAY)

    try:
        resp = session.post(
            UN_DOWNLOAD_URL,
            json={"reportId": un_id, "language": None},
            headers={"Content-Type": "application/json"},
            timeout=120,
            stream=True,
        )
        if resp.status_code == 404:
            log.warning("404 from UN backend for id=%d — skipping", un_id)
            return False
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.error("UN download failed for id=%d: %s", un_id, exc)
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as fh:
        for chunk in resp.iter_content(chunk_size=65536):
            fh.write(chunk)

    log.info("Downloaded UN id=%d → %s (%d bytes)", un_id, dest.name, dest.stat().st_size)
    return True


# ── Catalogue entry builder ────────────────────────────────────────────────


def build_entry(
    url: str,
    local_path: Path,
    meta: dict,
    filename: str,
    downloaded: bool,
) -> dict:
    file_size = local_path.stat().st_size if local_path.exists() else None
    entry_id = filename.removesuffix(".pdf")
    return {
        "id": entry_id,
        "country": meta["country"],
        "country_iso3": meta["iso3"],
        "year": meta["year"],
        "source_url": url,
        "local_path": str(local_path.relative_to(PROJECT_ROOT)),
        "file_size_bytes": file_size,
        "page_count": None,
        "language": meta["language"],
        "downloaded": downloaded,
    }


# ── Skip-download mode ────────────────────────────────────────────────────


def catalogue_from_existing() -> list[dict]:
    """
    Build catalogue entries from whatever is already in data/raw_pdfs/.
    Parses standardised filenames (ISO3_YEAR[_N].pdf) to recover metadata.
    Source URL is set to null since we don't know it from the filename alone.
    """
    entries = []
    for pdf in sorted(RAW_PDFS_DIR.glob("*.pdf")):
        stem = pdf.stem  # e.g. "USA_2023" or "USA_2023_2"
        parts = stem.split("_")
        iso3 = parts[0] if parts else "UNK"
        year_str = parts[1] if len(parts) > 1 else None
        year = int(year_str) if year_str and year_str.isdigit() else None
        country = ISO3_LOOKUP.get(iso3, (iso3, "Unknown"))[1]
        entries.append(
            {
                "id": stem,
                "country": country,
                "country_iso3": iso3,
                "year": year,
                "source_url": None,
                "local_path": str(pdf.relative_to(PROJECT_ROOT)),
                "file_size_bytes": pdf.stat().st_size,
                "page_count": None,
                "language": None,  # unknown; inferred during extraction
                "downloaded": True,
            }
        )
    return entries


# ── Main ───────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Build catalogue from already-downloaded files without fetching anything new.",
    )
    args = parser.parse_args()

    RAW_PDFS_DIR.mkdir(parents=True, exist_ok=True)

    if args.skip_download:
        log.info("--skip-download: cataloguing existing files in %s", RAW_PDFS_DIR)
        entries = catalogue_from_existing()
        log.info("Found %d local PDFs", len(entries))
        CATALOGUE_PATH.write_text(
            json.dumps(entries, indent=2, ensure_ascii=False) + "\n"
        )
        log.info("Catalogue written to %s", CATALOGUE_PATH)
        _print_summary(entries)
        return

    session = requests.Session()
    session.headers.update(HEADERS)

    sources = collect_all_sources(session)

    used_ids: dict[str, int] = {}
    entries: list[dict] = []
    is_first_download = True

    for src in sources:
        meta = {
            "iso3": src["iso3"],
            "country": src["country"],
            "year": src["year"],
            "language": src["language"],
        }
        filename = assign_local_filename(meta, used_ids)
        local_path = RAW_PDFS_DIR / filename

        if local_path.exists():
            log.info("Already exists, skipping download: %s", filename)
            downloaded = True
        elif src["type"] == "un":
            downloaded = download_pdf_from_un(
                session, src["un_id"], local_path, first=is_first_download
            )
            if downloaded:
                is_first_download = False
        else:
            downloaded = download_pdf(
                session, src["url"], local_path, first=is_first_download
            )
            if downloaded:
                is_first_download = False

        entries.append(build_entry(src["source_url"], local_path, meta, filename, downloaded))

    CATALOGUE_PATH.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False) + "\n"
    )
    log.info("Catalogue written to %s  (%d entries)", CATALOGUE_PATH, len(entries))
    _print_summary(entries)


def _print_summary(entries: list[dict]) -> None:
    downloaded = sum(1 for e in entries if e["downloaded"])
    skipped = len(entries) - downloaded
    years = sorted({e["year"] for e in entries if e["year"]})
    countries = sorted({e["country_iso3"] for e in entries if e["country_iso3"] != "UNK"})
    log.info(
        "Summary: %d downloaded, %d skipped/failed | years %s–%s | countries: %s",
        downloaded,
        skipped,
        years[0] if years else "?",
        years[-1] if years else "?",
        ", ".join(countries) if countries else "none",
    )


if __name__ == "__main__":
    main()
