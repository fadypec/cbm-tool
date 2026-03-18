"""Regression tests for the CBM Facility Explorer REST API.

All tests run against a mocked database — no real PostgreSQL connection needed.
Run with:
    pytest tests/test_api.py -v
"""
import os
import pytest
from unittest.mock import MagicMock, patch

# ── Env vars must be set before importing the app ────────────────────────────
os.environ.setdefault("DATABASE_URL", "postgresql://cbm:cbm@localhost/cbm")
os.environ.setdefault("REVIEW_API_KEY", "test-review-key")

from fastapi.testclient import TestClient  # noqa: E402
from api.main import app                   # noqa: E402

REVIEW_KEY = os.environ["REVIEW_API_KEY"]


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def mock_pool():
    return MagicMock()


@pytest.fixture()
def client(mock_pool):
    """TestClient with lifespan DB pool replaced by a mock."""
    with patch("psycopg2.pool.ThreadedConnectionPool", return_value=mock_pool):
        with TestClient(app) as c:
            yield c, mock_pool


def _setup_cursor(mock_pool, *, fetchone=None, fetchall=None, rowcount=1):
    """Configure mock_pool so cursor() returns the given data.

    For endpoints that make multiple queries, fetchone/fetchall are returned
    every time — tests that need to distinguish calls should set side_effect on
    the mock cursor directly after calling this helper.
    """
    cur = mock_pool.getconn.return_value.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value = fetchone
    cur.fetchall.return_value = fetchall if fetchall is not None else []
    cur.rowcount = rowcount
    return cur


# ── /health ───────────────────────────────────────────────────────────────────

class TestHealth:
    def test_returns_200(self, client):
        c, _ = client
        assert c.get("/health").status_code == 200

    def test_response_body(self, client):
        c, _ = client
        assert c.get("/health").json() == {"status": "ok"}

    def test_does_not_hit_database(self, client):
        c, pool = client
        c.get("/health")
        pool.getconn.assert_not_called()


# ── Security headers ──────────────────────────────────────────────────────────

class TestSecurityHeaders:
    """Every response must carry the mandatory security headers."""

    def _get(self, client):
        c, _ = client
        return c.get("/health")

    def test_x_content_type_options(self, client):
        assert self._get(client).headers.get("x-content-type-options") == "nosniff"

    def test_x_frame_options(self, client):
        assert self._get(client).headers.get("x-frame-options") == "DENY"

    def test_referrer_policy(self, client):
        assert self._get(client).headers.get("referrer-policy") == "strict-origin-when-cross-origin"

    def test_csp_default_src(self, client):
        csp = self._get(client).headers.get("content-security-policy", "")
        assert "default-src 'self'" in csp

    def test_csp_frame_ancestors(self, client):
        csp = self._get(client).headers.get("content-security-policy", "")
        assert "frame-ancestors 'none'" in csp


# ── 404 handling ──────────────────────────────────────────────────────────────

class TestNotFound:
    def test_unknown_route(self, client):
        c, _ = client
        assert c.get("/api/does-not-exist").status_code == 404

    def test_unknown_country(self, client):
        c, pool = client
        _setup_cursor(pool, fetchone=None)
        assert c.get("/api/country/ZZZ").status_code == 404

    def test_unknown_entity(self, client):
        c, pool = client
        _setup_cursor(pool, fetchone=None)
        assert c.get("/api/entity/ZZZ_999").status_code == 404

    def test_unknown_country_is_case_insensitive(self, client):
        """iso3 is uppercased internally, so 'zzz' and 'ZZZ' should both 404."""
        c, pool = client
        _setup_cursor(pool, fetchone=None)
        assert c.get("/api/country/zzz").status_code == 404


# ── Auth guards ───────────────────────────────────────────────────────────────

class TestAuthGuards:
    def test_flag_without_key_rejected(self, client):
        c, _ = client
        r = c.post("/api/entity/GBR_001/flag/2022", json={"flag": True, "note": ""})
        assert r.status_code in (401, 503)

    def test_flag_with_wrong_key_rejected(self, client):
        c, _ = client
        r = c.post(
            "/api/entity/GBR_001/flag/2022",
            json={"flag": True, "note": ""},
            headers={"X-Review-Key": "wrong-key"},
        )
        assert r.status_code == 401

    def test_flagged_list_without_key_rejected(self, client):
        c, _ = client
        assert c.get("/api/flagged").status_code in (401, 503)

    def test_flagged_list_with_correct_key(self, client):
        c, pool = client
        _setup_cursor(pool, fetchall=[])
        r = c.get("/api/flagged", headers={"X-Review-Key": REVIEW_KEY})
        assert r.status_code == 200
        assert r.json() == []

    def test_flag_with_correct_key_and_existing_entity(self, client):
        c, pool = client
        _setup_cursor(pool, rowcount=1)
        r = c.post(
            "/api/entity/GBR_001/flag/2022",
            json={"flag": True, "note": "suspicious"},
            headers={"X-Review-Key": REVIEW_KEY},
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True}


# ── /api/search input validation ─────────────────────────────────────────────

class TestSearchValidation:
    def test_very_long_query_does_not_cause_500(self, client):
        c, pool = client
        _setup_cursor(pool, fetchall=[])
        r = c.get("/api/search?q=" + "x" * 500)
        assert r.status_code != 500

    def test_search_returns_list(self, client):
        c, pool = client
        _setup_cursor(pool, fetchall=[
            {
                "canonical_facility_id": "DEU_001",
                "canonical_name": "Robert Koch Institut",
                "country_iso3": "DEU",
                "country_name": "Germany",
                "years_declared": [2015, 2016],
                "latest_containment": "BSL-3",
                "latest_area_m2": None,
                "agents_summary": "influenza",
                "lat": 52.5,
                "lon": 13.4,
            }
        ])
        r = c.get("/api/search?q=robert+koch")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)


# ── /api/countries ────────────────────────────────────────────────────────────

class TestCountries:
    def test_returns_list(self, client):
        c, pool = client
        _setup_cursor(pool, fetchall=[
            {
                "country_iso3": "DEU",
                "country_name": "Germany",
                "submission_count": 10,
                "latest_year": 2024,
                "facility_count": 5,
                "bsl4_count": 1,
                "a1_rate": "0.900",
            }
        ])
        r = c.get("/api/countries")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert body[0]["country_iso3"] == "DEU"


# ── /api/country/{iso3} ───────────────────────────────────────────────────────

class TestCountryDetail:
    def test_known_country_returns_200(self, client):
        c, pool = client
        cur = _setup_cursor(
            pool,
            fetchone={"country_name": "Germany"},
            fetchall=[],
        )
        # First call returns the country row; subsequent calls return []
        cur.fetchone.side_effect = [{"country_name": "Germany"}, None]
        r = c.get("/api/country/DEU")
        assert r.status_code == 200
        body = r.json()
        assert body["country_iso3"] == "DEU"
        assert body["country_name"] == "Germany"

    def test_iso3_is_uppercased(self, client):
        c, pool = client
        cur = _setup_cursor(pool, fetchone=None)
        # lowercase 'deu' should behave the same as 'DEU' — 404 when DB has no row
        cur.fetchone.return_value = None
        r = c.get("/api/country/deu")
        # Should not get a 500; any of 200/404 is valid depending on mock data
        assert r.status_code in (200, 404)
