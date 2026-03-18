# CBM Tool — Security Audit

**Date:** 2026-03-17
**Scope:** API, dashboard, extraction pipeline, deployment configuration

---

## Current Mitigations (Already Implemented)

These are in place and working correctly:

- **Security headers middleware** (`api/main.py:166-202`): X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Content-Security-Policy, Cache-Control
- **CSP policy**: restricts scripts/styles to self + CDN domains; `frame-ancestors 'none'` prevents clickjacking
- **Rate limiting** via slowapi (`api/main.py:146`): 60/min default, 20/min for GeoJSON, 10/min for AI query
- **Proxy-aware IP extraction** (`api/main.py:133-140`): reads X-Forwarded-For for Railway
- **Review queue auth** (`api/main.py:59-65`): REVIEW_API_KEY required, fails closed if unset
- **API docs disabled in production** (`api/main.py:153-155`): only enabled when `ENVIRONMENT=dev`
- **GZip middleware** (`api/main.py:208`): reduces payload size
- **DATABASE_URL required** (`api/main.py:46-48`): fails fast if not set
- **Natural query input capped** (`api/main.py:1094`): 400 character max
- **Natural query output clamped** (`api/main.py:1150-1153, 1204`): list lengths and term sizes bounded
- **Natural query system prompt hardened** (`api/main.py:1097-1119`): explicit anti-injection rules
- **Parameterized SQL throughout**: no string interpolation in API queries
- **`esc()` function** (`dashboard/static/app.js:2982-2989`): applied to all onclick attributes and innerHTML from DB values
- **AI rationale uses `textContent`** not `innerHTML` (`dashboard/static/app.js:2345`): safe from HTML injection

---

## Findings

### CRITICAL

*None identified.* The application has a solid security posture for its scope.

---

### HIGH

#### H1. XSS in `exportCountryReport()` — zero HTML escaping
**Location:** `dashboard/static/app.js:2799-2840`

The report card export generates a standalone HTML page opened via `window.open()` on a `blob:` URL. Database-sourced values are interpolated as **raw HTML** with no escaping:

- Line 2801: `<title>CBM Report: ${data.country_name}</title>`
- Line 2815: `<h1>CBM Report Card: ${data.country_name}</h1>`
- Line 2828: `<td>${f.canonical_name || '[Unnamed]'}</td>`
- Line 2830: `${f.latest_containment}</span>`
- Line 2837: `<li><strong>${f.canonical_name || '[Unnamed]'}</strong></li>`

If any database field contains `<script>` or event handlers (e.g., from a malicious or corrupted PDF extraction), the content executes as JavaScript in the new tab.

**Mitigation:** The blob URL creates an isolated origin, so XSS cannot directly access the parent page's cookies/DOM. However, the generated document itself is compromised.

**Recommendation:** Apply `esc()` to all interpolated values in the report template.

#### H2. `esc()` function does not escape single quotes
**Location:** `dashboard/static/app.js:2982-2989`

The escaping function handles `&`, `<`, `>`, `"` but **not `'`**. All onclick handlers use single-quoted strings:

```js
onclick="showEntityModal('${esc(p.id)}')"
onclick="selectCountry('${esc(c.country_iso3)}')"
```

A database value containing a single quote could break out of the JS string context. In practice, IDs follow `GBR_001` format (alphanumeric + underscore), but this is a latent vulnerability.

**Recommendation:** Add `.replace(/'/g, '&#39;')` to `esc()`.

---

### MEDIUM

#### M1. CSP allows `'unsafe-inline'` for scripts
**Location:** `api/main.py:185`

Required because the dashboard uses inline `onclick` attributes. This weakens CSP protection — any XSS that achieves HTML injection can execute inline scripts.

**Recommendation:** Migrate onclick handlers to `addEventListener()` calls in app.js, then remove `'unsafe-inline'` from the CSP. This is a significant refactor.

#### M2. X-Forwarded-For header is trusted without validation
**Location:** `api/main.py:133-140`

The `get_client_ip()` function trusts the first entry of `X-Forwarded-For`. An attacker can spoof this header to bypass rate limiting by sending `X-Forwarded-For: random-ip` with each request.

**Mitigation in context:** Railway sets X-Forwarded-For at its edge, but does not strip existing headers. A client can prepend their own IP.

**Recommendation:** Consider trusting only the rightmost (or second-to-rightmost) entry, which is the one the proxy adds. Alternatively, Railway may provide a `X-Forwarded-For` header that it controls.

#### M3. Natural query relays user input to Claude API
**Location:** `api/main.py:1122-1206`

User text is sent to Claude Haiku as a message. While the system prompt includes injection defences, a determined attacker could potentially extract the system prompt or manipulate Claude's output. The output is parsed as JSON and used to construct SQL WHERE clauses.

**Mitigations already in place:**
- System prompt explicitly forbids non-search responses
- Output is validated and clamped (list sizes, term lengths)
- SQL is parameterized
- Rate limited to 10/min

**Residual risk:** If Claude returns unexpected JSON structure (e.g., nested objects), `_clean_list()` handles this safely. The main risk is cost: an attacker could make 10 Claude API calls per minute.

#### M4. Docker Compose uses default credentials
**Location:** `docker-compose.yml:6-8`

```yaml
POSTGRES_USER: cbm
POSTGRES_PASSWORD: cbm
```

These match the connection string in CLAUDE.md. Not a production issue (Railway uses Supabase), but if docker-compose is used in any accessible environment, the database is trivially accessible.

---

### LOW

#### L1. No CORS configuration
**Location:** `api/main.py`

No CORS middleware is configured. The API serves its own dashboard at `/`, so same-origin requests work. However, any external site can make cross-origin requests to the API because there are no CORS restrictions (FastAPI's default is no CORS headers, meaning browsers block cross-origin fetch requests — but simple requests like `<img>` or `<script>` can still be made).

**Assessment:** Acceptable for a read-only public API. Only the flag/unflag endpoints modify data, and those require an auth header.

#### L2. No Content-Length or payload size limit on POST endpoints
**Location:** `api/main.py:1122`

The natural-query POST endpoint validates `max_length=400` on the query string but has no overall payload size limit. An attacker could send a request with a very large JSON body (though Pydantic would reject unknown fields).

**Assessment:** Low risk — FastAPI/uvicorn have default body size limits.

#### L3. `migrate.sh` has unparameterized SQL via filename interpolation
**Location:** `db/migrate.sh:41`

`WHERE filename = '$fname'` interpolates the migration filename directly. Filenames are controlled (from the repo), but this is a code smell.

#### L4. API key persists in process memory
**Location:** `scripts/04_extract_structured.py:1563`

Standard practice for environment-variable-based secrets. `.env` is properly gitignored.

#### L5. Google Cloud Vision client created per-page in OCR
**Location:** `scripts/02_extract_text.py:169`

`_gvision.ImageAnnotatorClient()` instantiated inside `_ocr_page_vision` (called per page). Creates unnecessary client instances. Not a security issue but wasteful.

#### L6. No schema validation on LLM extraction output
**Location:** `scripts/04_extract_structured.py` (all extract functions)

Parsed JSON from Claude is accepted without schema validation. If Claude returns wrong types (e.g., `"yes"` instead of `true` for `has_bsl4`), these propagate into the database. Not exploitable remotely but affects data integrity.

---

## Accessibility Notes (Not Security, But Relevant)

- No ARIA labels on search input, mode toggle, or filter checkboxes
- No `role="listbox"` on search results dropdown
- Compliance grid cells use colour only (no text alternative)
- No keyboard shortcut for year animation play/pause
- No `aria-live` regions for dynamic content updates

---

## Recommended Priority Actions

| Priority | Action | Effort |
|----------|--------|--------|
| 1 | Add `esc()` to all values in `exportCountryReport()` | 15 min |
| 2 | Add `'` escaping to `esc()` function | 2 min |
| 3 | Investigate Railway's XFF header handling; consider rightmost-entry trust | 30 min |
| 4 | Migrate onclick handlers to addEventListener (removes `unsafe-inline`) | 2-3 hrs |
| 5 | Remove default credentials from docker-compose.yml | 5 min |
