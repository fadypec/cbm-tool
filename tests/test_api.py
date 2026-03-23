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


# ── /ready ─────────────────────────────────────────────────────────────────────

class TestReady:
    def test_returns_ready_when_db_ok(self, client):
        c, pool = client
        _setup_cursor(pool, fetchone={"?column?": 1})
        r = c.get("/ready")
        assert r.status_code == 200
        assert r.json() == {"status": "ready"}

    def test_returns_503_when_db_fails(self, client):
        c, pool = client
        pool.getconn.side_effect = Exception("connection refused")
        r = c.get("/ready")
        assert r.status_code == 503
        assert r.json()["status"] == "unavailable"


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


# ── /api/stats ──────────────────────────────────────────────────────────────

class TestStats:
    def test_returns_summary(self, client):
        c, pool = client
        _setup_cursor(pool, fetchone={
            "total_submissions": 517,
            "total_countries": 45,
            "total_facility_years": 1600,
            "total_unique_facilities": 457,
            "geocoded_facility_years": 1400,
            "vaccine_facility_years": 599,
            "defence_facility_years": 969,
            "total_unique_vaccine": 120,
            "total_unique_defence": 85,
            "year_min": 1987,
            "year_max": 2025,
        })
        r = c.get("/api/stats")
        assert r.status_code == 200
        body = r.json()
        assert body["total_submissions"] == 517
        assert body["total_countries"] == 45
        assert body["year_min"] == 1987
        assert body["year_max"] == 2025

    def test_contains_all_expected_keys(self, client):
        c, pool = client
        _setup_cursor(pool, fetchone={
            "total_submissions": 1,
            "total_countries": 1,
            "total_facility_years": 0,
            "total_unique_facilities": 0,
            "geocoded_facility_years": 0,
            "vaccine_facility_years": 0,
            "defence_facility_years": 0,
            "total_unique_vaccine": 0,
            "total_unique_defence": 0,
            "year_min": 2020,
            "year_max": 2020,
        })
        r = c.get("/api/stats")
        body = r.json()
        expected_keys = {
            "total_submissions", "total_countries", "total_facility_years",
            "total_unique_facilities", "geocoded_facility_years",
            "vaccine_facility_years", "defence_facility_years",
            "total_unique_vaccine", "total_unique_defence",
            "year_min", "year_max",
        }
        assert expected_keys == set(body.keys())


# ── /api/bwc-membership ─────────────────────────────────────────────────────

class TestBwcMembership:
    def test_returns_membership_dict(self, client):
        c, _ = client
        r = c.get("/api/bwc-membership")
        assert r.status_code == 200
        body = r.json()
        assert "membership" in body
        assert "last_updated" in body
        assert "source" in body

    def test_known_restricted_countries(self, client):
        c, _ = client
        body = c.get("/api/bwc-membership").json()
        m = body["membership"]
        assert m["CHN"] == "restricted"
        assert m["FRA"] == "restricted"
        assert m["RUS"] == "restricted"
        assert m["IND"] == "restricted"

    def test_known_non_parties(self, client):
        c, _ = client
        body = c.get("/api/bwc-membership").json()
        m = body["membership"]
        assert m["ISR"] == "non_party"
        assert m["ERI"] == "non_party"

    def test_does_not_hit_database(self, client):
        """BWC membership is a static dict — no DB query needed."""
        c, pool = client
        c.get("/api/bwc-membership")
        pool.getconn.assert_not_called()


# ── /api/country/{iso3}/defence ──────────────────────────────────────────────

class TestCountryDefence:
    def test_returns_programmes_and_entities(self, client):
        c, pool = client
        cur = _setup_cursor(pool)
        cur.fetchall.side_effect = [
            # programmes
            [{
                "year": 2024, "programme_name": "Bundeswehr Bio Defence",
                "responsible_org": "BMVg", "objectives_summary": "Protection",
                "research_areas": "diagnostics", "total_funding_amount": 5000000,
                "total_funding_currency": "EUR", "uses_contractors": True,
                "contractor_proportion_pct": 15, "confidence": "high",
            }],
            # entities
            [{
                "canonical_id": "DEU_D001", "canonical_name": "WIS Munster",
                "first_year": 2015, "last_year": 2024,
                "has_bsl4": False, "has_bsl3": True,
            }],
        ]
        r = c.get("/api/country/DEU/defence")
        assert r.status_code == 200
        body = r.json()
        assert "programmes" in body
        assert "entities" in body
        assert "records" not in body
        assert len(body["programmes"]) == 1
        assert body["programmes"][0]["programme_name"] == "Bundeswehr Bio Defence"
        assert body["entities"][0]["canonical_name"] == "WIS Munster"

    def test_empty_defence_data(self, client):
        c, pool = client
        cur = _setup_cursor(pool)
        cur.fetchall.side_effect = [[], []]
        r = c.get("/api/country/LUX/defence")
        assert r.status_code == 200
        body = r.json()
        assert body["programmes"] == []
        assert body["entities"] == []
        assert "records" not in body


# ── /api/country/{iso3}/vaccine ──────────────────────────────────────────────

class TestCountryVaccine:
    def test_returns_entities_and_records(self, client):
        c, pool = client
        cur = _setup_cursor(pool)
        cur.fetchall.side_effect = [
            # entities
            [{
                "canonical_id": "GBR_V001",
                "canonical_name": "Porton Biopharma Ltd",
                "first_year": 2015, "last_year": 2024,
            }],
            # records
            [{
                "year": 2024,
                "canonical_vaccine_facility_id": "GBR_V001",
                "facility_name": "Porton Biopharma Ltd",
                "city": "Salisbury", "address": "Manor Farm Rd",
                "diseases_covered": "anthrax, tuberculosis",
                "vaccines_summary": "Anthrax vaccine production",
                "confidence": "high",
            }],
        ]
        r = c.get("/api/country/GBR/vaccine")
        assert r.status_code == 200
        body = r.json()
        assert "entities" in body
        assert "records" in body
        assert body["entities"][0]["canonical_name"] == "Porton Biopharma Ltd"

    def test_empty_vaccine_data(self, client):
        c, pool = client
        cur = _setup_cursor(pool)
        cur.fetchall.side_effect = [[], []]
        r = c.get("/api/country/ZZZ/vaccine")
        assert r.status_code == 200
        body = r.json()
        assert body["entities"] == []
        assert body["records"] == []


# ── /api/country/{iso3}/legislation ──────────────────────────────────────────

class TestCountryLegislation:
    def test_returns_legislation_records(self, client):
        c, pool = client
        _setup_cursor(pool, fetchall=[{
            "year": 2024,
            "prohibitions_legislation": True,
            "prohibitions_regulations": True,
            "prohibitions_other_measures": False,
            "prohibitions_amended": False,
            "exports_legislation": True,
            "exports_regulations": True,
            "exports_other_measures": False,
            "exports_amended": False,
            "imports_legislation": True,
            "imports_regulations": False,
            "imports_other_measures": False,
            "imports_amended": False,
            "biosafety_legislation": True,
            "biosafety_regulations": True,
            "biosafety_other_measures": True,
            "biosafety_amended": False,
            "key_laws": ["Biological Weapons Act 1974"],
            "notes": None,
            "confidence": "high",
            "document_id": 42,
            "source_url": "https://example.com/doc.pdf",
        }])
        r = c.get("/api/country/GBR/legislation")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert body[0]["year"] == 2024
        assert body[0]["prohibitions_legislation"] is True

    def test_empty_legislation(self, client):
        c, pool = client
        _setup_cursor(pool, fetchall=[])
        r = c.get("/api/country/ZZZ/legislation")
        assert r.status_code == 200
        assert r.json() == []


# ── /api/country/{iso3}/past-programmes ──────────────────────────────────────

class TestCountryPastProgrammes:
    def test_returns_past_programme_records(self, client):
        c, pool = client
        _setup_cursor(pool, fetchall=[{
            "year": 2023,
            "convention_entry_date": "1975-03-26",
            "has_offensive_programme": False,
            "offensive_period": None,
            "offensive_summary": None,
            "has_defensive_programme": True,
            "defensive_period": "1940-1979",
            "defensive_summary": "Gruinard Island programme (terminated 1979)",
            "confidence": "high",
            "notes": None,
            "document_id": 99,
            "source_url": "https://example.com/gbr_2023.pdf",
        }])
        r = c.get("/api/country/GBR/past-programmes")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert body[0]["has_defensive_programme"] is True
        assert body[0]["defensive_period"] == "1940-1979"

    def test_empty_past_programmes(self, client):
        c, pool = client
        _setup_cursor(pool, fetchall=[])
        r = c.get("/api/country/LUX/past-programmes")
        assert r.status_code == 200
        assert r.json() == []


# ── /api/entity/{entity_id} (research facility) ─────────────────────────────

class TestEntityDetail:
    def test_returns_entity_with_year_records(self, client):
        c, pool = client
        cur = _setup_cursor(pool)
        cur.fetchone.return_value = {
            "canonical_facility_id": "DEU_001",
            "canonical_name": "Robert Koch Institut",
            "country_iso3": "DEU",
            "all_names": ["Robert Koch Institute", "RKI Berlin"],
            "years_declared": [2020, 2021, 2022, 2023, 2024],
            "latest_containment": "BSL-3",
            "latest_area_m2": 1200,
            "country_name": "Germany",
        }
        cur.fetchall.return_value = [{
            "year": 2024, "document_id": 10,
            "facility_name": "Robert Koch Institut",
            "responsible_org": "BMG",
            "city": "Berlin", "address": "Nordufer 20",
            "has_bsl4": False, "bsl4_area_m2": 0,
            "has_bsl3": True, "bsl3_area_m2": 1200,
            "highest_containment": "BSL-3",
            "agents_summary": "influenza, SARS-CoV-2",
            "mod_funded": False, "confidence": "high",
            "geocode_confidence": "exact",
            "source_url": "https://example.com/deu_2024.pdf",
            "flagged_for_review": False, "flag_note": None,
        }]
        r = c.get("/api/entity/DEU_001")
        assert r.status_code == 200
        body = r.json()
        assert body["canonical_facility_id"] == "DEU_001"
        assert body["canonical_name"] == "Robert Koch Institut"
        assert body["country_iso3"] == "DEU"
        assert len(body["year_records"]) == 1
        assert body["year_records"][0]["year"] == 2024

    def test_unknown_entity_returns_404(self, client):
        c, pool = client
        _setup_cursor(pool, fetchone=None)
        r = c.get("/api/entity/ZZZ_999")
        assert r.status_code == 404


# ── /api/entity/defence/{entity_id} ─────────────────────────────────────────

class TestDefenceEntityDetail:
    def test_returns_defence_entity(self, client):
        c, pool = client
        cur = _setup_cursor(pool)
        cur.fetchone.return_value = {
            "canonical_defence_facility_id": "GBR_D001",
            "canonical_name": "Dstl Porton Down",
            "country_iso3": "GBR",
            "country_name": "United Kingdom",
            "all_names": ["Dstl Porton Down", "DSTL"],
            "first_year": 2012,
            "last_year": 2024,
        }
        cur.fetchall.return_value = [{
            "year": 2024, "facility_name": "Dstl Porton Down",
            "city": "Salisbury", "address": "Manor Farm Rd",
            "bsl2_area_m2": 500, "bsl3_area_m2": 200, "bsl4_area_m2": 50,
            "total_lab_area_m2": 750,
            "personnel_total": 300, "personnel_military": 20,
            "personnel_civilian": 280,
            "personnel_scientists": 150, "personnel_engineers": 30,
            "personnel_technicians": 60, "personnel_admin": 40,
            "mod_funded": True, "work_description": "Bio defence research",
            "funding_source": "MOD", "funding_research": 10000000,
            "funding_development": 5000000, "funding_te": 2000000,
            "funding_currency": "GBP",
            "confidence": "high", "geocode_confidence": "exact",
            "source_url": "https://example.com/gbr_2024.pdf",
        }]
        r = c.get("/api/entity/defence/GBR_D001")
        assert r.status_code == 200
        body = r.json()
        assert body["canonical_defence_facility_id"] == "GBR_D001"
        assert body["canonical_name"] == "Dstl Porton Down"
        assert len(body["year_records"]) == 1

    def test_unknown_defence_entity_returns_404(self, client):
        c, pool = client
        _setup_cursor(pool, fetchone=None)
        r = c.get("/api/entity/defence/ZZZ_D999")
        assert r.status_code == 404


# ── /api/entity/vaccine/{entity_id} ─────────────────────────────────────────

class TestVaccineEntityDetail:
    def test_returns_vaccine_entity(self, client):
        c, pool = client
        cur = _setup_cursor(pool)
        cur.fetchone.return_value = {
            "canonical_vaccine_facility_id": "DEU_V001",
            "canonical_name": "Paul-Ehrlich-Institut",
            "country_iso3": "DEU",
            "first_year": 2015,
            "last_year": 2024,
            "country_name": "Germany",
        }
        cur.fetchall.return_value = [{
            "year": 2024, "document_id": 55,
            "facility_name": "Paul-Ehrlich-Institut",
            "city": "Langen", "address": "Paul-Ehrlich-Str. 51-59",
            "diseases_covered": "influenza, measles",
            "vaccines_summary": "Vaccine quality control and batch release",
            "confidence": "high",
            "source_url": "https://example.com/deu_2024.pdf",
        }]
        r = c.get("/api/entity/vaccine/DEU_V001")
        assert r.status_code == 200
        body = r.json()
        assert body["canonical_vaccine_facility_id"] == "DEU_V001"
        assert body["canonical_name"] == "Paul-Ehrlich-Institut"
        assert len(body["year_records"]) == 1
        assert body["year_records"][0]["city"] == "Langen"

    def test_unknown_vaccine_entity_returns_404(self, client):
        c, pool = client
        _setup_cursor(pool, fetchone=None)
        r = c.get("/api/entity/vaccine/ZZZ_V999")
        assert r.status_code == 404


# ── /api/map/facilities (GeoJSON) ───────────────────────────────────────────

class TestMapFacilities:
    def test_returns_geojson_feature_collection(self, client):
        c, pool = client
        _setup_cursor(pool, fetchall=[{
            "canonical_facility_id": "DEU_001",
            "name": "Robert Koch Institut",
            "country_iso3": "DEU",
            "containment": "BSL-3",
            "year": 2024,
            "city": "Berlin",
            "geocode_confidence": "exact",
            "lon": 13.3530,
            "lat": 52.5320,
            "country_name": "Germany",
            "agents_summary": "influenza, SARS-CoV-2",
            "agents_redacted": False,
        }])
        r = c.get("/api/map/facilities")
        assert r.status_code == 200
        body = r.json()
        assert body["type"] == "FeatureCollection"
        assert len(body["features"]) == 1
        feat = body["features"][0]
        assert feat["type"] == "Feature"
        assert feat["geometry"]["type"] == "Point"
        assert feat["geometry"]["coordinates"] == [13.353, 52.532]
        props = feat["properties"]
        assert props["id"] == "DEU_001"
        assert props["name"] == "Robert Koch Institut"
        assert props["containment"] == "BSL-3"

    def test_empty_facilities_returns_empty_collection(self, client):
        c, pool = client
        _setup_cursor(pool, fetchall=[])
        r = c.get("/api/map/facilities")
        assert r.status_code == 200
        body = r.json()
        assert body["type"] == "FeatureCollection"
        assert body["features"] == []


# ── /api/map/defence (GeoJSON) ──────────────────────────────────────────────

class TestMapDefence:
    def test_returns_geojson_feature_collection(self, client):
        c, pool = client
        _setup_cursor(pool, fetchall=[{
            "id": 42,
            "name": "Dstl Porton Down",
            "country_iso3": "GBR",
            "year": 2024,
            "city": "Salisbury",
            "geocode_confidence": "exact",
            "lon": -1.7954,
            "lat": 51.0691,
            "country_name": "United Kingdom",
        }])
        r = c.get("/api/map/defence")
        assert r.status_code == 200
        body = r.json()
        assert body["type"] == "FeatureCollection"
        assert len(body["features"]) == 1
        feat = body["features"][0]
        assert feat["geometry"]["type"] == "Point"
        assert feat["properties"]["name"] == "Dstl Porton Down"

    def test_empty_defence_map(self, client):
        c, pool = client
        _setup_cursor(pool, fetchall=[])
        r = c.get("/api/map/defence")
        assert r.status_code == 200
        assert r.json()["features"] == []


# ── /api/map/vaccines (GeoJSON) ─────────────────────────────────────────────

class TestMapVaccines:
    def test_returns_geojson_feature_collection(self, client):
        c, pool = client
        _setup_cursor(pool, fetchall=[{
            "id": "DEU_V001",
            "name": "Paul-Ehrlich-Institut",
            "country_iso3": "DEU",
            "year": 2024,
            "city": "Langen",
            "geocode_confidence": "exact",
            "lon": 8.6565,
            "lat": 50.0002,
            "country_name": "Germany",
        }])
        r = c.get("/api/map/vaccines")
        assert r.status_code == 200
        body = r.json()
        assert body["type"] == "FeatureCollection"
        assert len(body["features"]) == 1
        props = body["features"][0]["properties"]
        assert props["id"] == "DEU_V001"
        assert props["country_iso3"] == "DEU"

    def test_empty_vaccine_map(self, client):
        c, pool = client
        _setup_cursor(pool, fetchall=[])
        r = c.get("/api/map/vaccines")
        assert r.status_code == 200
        assert r.json()["features"] == []


# ── /api/map/compliance ─────────────────────────────────────────────────────

class TestMapCompliance:
    def test_default_compliance_returns_list(self, client):
        c, pool = client
        _setup_cursor(pool, fetchall=[{
            "country_iso3": "DEU",
            "country_name": "Germany",
            "submission_count": 12,
            "a1_rate": 0.917,
        }])
        r = c.get("/api/map/compliance")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert body[0]["country_iso3"] == "DEU"
        assert "a1_rate" in body[0]

    def test_form_specific_compliance(self, client):
        c, pool = client
        _setup_cursor(pool, fetchall=[{
            "country_iso3": "GBR",
            "country_name": "United Kingdom",
            "submission_count": 10,
            "rate": 0.800,
        }])
        r = c.get("/api/map/compliance/G")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert body[0]["country_iso3"] == "GBR"
        assert "rate" in body[0]

    def test_invalid_form_returns_400(self, client):
        c, _ = client
        r = c.get("/api/map/compliance/X")
        assert r.status_code == 400

    def test_form_is_case_insensitive(self, client):
        """Lowercase form codes like 'a1' should work (uppercased internally)."""
        c, pool = client
        _setup_cursor(pool, fetchall=[])
        r = c.get("/api/map/compliance/a1")
        assert r.status_code == 200


# ── /api/stats/timeline ─────────────────────────────────────────────────────

class TestTimeline:
    def test_returns_yearly_arrays(self, client):
        c, pool = client
        _setup_cursor(pool, fetchall=[
            {"year": 2020, "a1_facility_years": 80, "bsl4_facility_years": 5, "submitting_countries": 30},
            {"year": 2021, "a1_facility_years": 90, "bsl4_facility_years": 6, "submitting_countries": 32},
            {"year": 2022, "a1_facility_years": 95, "bsl4_facility_years": 7, "submitting_countries": 33},
        ])
        r = c.get("/api/stats/timeline")
        assert r.status_code == 200
        body = r.json()
        assert body["years"] == [2020, 2021, 2022]
        assert body["a1_facility_years"] == [80, 90, 95]
        assert body["bsl4_facility_years"] == [5, 6, 7]
        assert body["submitting_countries"] == [30, 32, 33]

    def test_empty_timeline(self, client):
        c, pool = client
        _setup_cursor(pool, fetchall=[])
        r = c.get("/api/stats/timeline")
        assert r.status_code == 200
        body = r.json()
        assert body["years"] == []
        assert body["a1_facility_years"] == []


# ── /api/stats/bsl4-capacity ────────────────────────────────────────────────

class TestBsl4Capacity:
    def test_returns_per_country_year_data(self, client):
        c, pool = client
        _setup_cursor(pool, fetchall=[
            {
                "year": 2023,
                "country_iso3": "DEU",
                "country_name": "Germany",
                "total_bsl4_area_m2": 2500.0,
                "bsl4_facility_count": 3,
            },
            {
                "year": 2023,
                "country_iso3": "GBR",
                "country_name": "United Kingdom",
                "total_bsl4_area_m2": 1800.0,
                "bsl4_facility_count": 2,
            },
        ])
        r = c.get("/api/stats/bsl4-capacity")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert len(body) == 2
        assert body[0]["country_iso3"] == "DEU"
        assert body[0]["total_bsl4_area_m2"] == 2500.0

    def test_empty_bsl4_data(self, client):
        c, pool = client
        _setup_cursor(pool, fetchall=[])
        r = c.get("/api/stats/bsl4-capacity")
        assert r.status_code == 200
        assert r.json() == []


# ── /api/changes/notable ────────────────────────────────────────────────────

class TestNotableChanges:
    def test_detects_bsl4_gained(self, client):
        """A facility that gains BSL-4 status should appear as a notable change."""
        c, pool = client
        # Endpoint runs one big query, then does Python-side diff computation
        _setup_cursor(pool, fetchall=[
            {
                "canonical_facility_id": "DEU_001",
                "facility_name": "Friedrich-Loeffler-Institut",
                "country_iso3": "DEU", "country_name": "Germany",
                "year": 2018, "has_bsl4": False, "bsl4_area_m2": 0,
                "has_bsl3": True, "bsl3_area_m2": 500,
                "highest_containment": "BSL-3",
                "personnel_total": None, "agents_summary": None,
            },
            {
                "canonical_facility_id": "DEU_001",
                "facility_name": "Friedrich-Loeffler-Institut",
                "country_iso3": "DEU", "country_name": "Germany",
                "year": 2019, "has_bsl4": False, "bsl4_area_m2": 0,
                "has_bsl3": True, "bsl3_area_m2": 500,
                "highest_containment": "BSL-3",
                "personnel_total": None, "agents_summary": None,
            },
            {
                "canonical_facility_id": "DEU_001",
                "facility_name": "Friedrich-Loeffler-Institut",
                "country_iso3": "DEU", "country_name": "Germany",
                "year": 2020, "has_bsl4": True, "bsl4_area_m2": 200,
                "has_bsl3": True, "bsl3_area_m2": 500,
                "highest_containment": "BSL-4",
                "personnel_total": None, "agents_summary": None,
            },
        ])
        r = c.get("/api/changes/notable?min_years=3")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert len(body) >= 1
        bsl4_changes = [ch for ch in body if ch["type"] == "bsl4_gained"]
        assert len(bsl4_changes) == 1
        assert bsl4_changes[0]["facility_name"] == "Friedrich-Loeffler-Institut"

    def test_no_changes_with_high_min_years(self, client):
        """If min_years exceeds the record count, no changes should be returned."""
        c, pool = client
        _setup_cursor(pool, fetchall=[
            {
                "canonical_facility_id": "GBR_001",
                "facility_name": "Pirbright Institute",
                "country_iso3": "GBR", "country_name": "United Kingdom",
                "year": 2023, "has_bsl4": False, "bsl4_area_m2": 0,
                "has_bsl3": True, "bsl3_area_m2": 300,
                "highest_containment": "BSL-3",
                "personnel_total": None, "agents_summary": None,
            },
        ])
        r = c.get("/api/changes/notable?min_years=5")
        assert r.status_code == 200
        assert r.json() == []

    def test_empty_data_returns_empty_list(self, client):
        c, pool = client
        _setup_cursor(pool, fetchall=[])
        r = c.get("/api/changes/notable")
        assert r.status_code == 200
        assert r.json() == []


# ── /api/pathogens/frequency ────────────────────────────────────────────────

class TestPathogenFrequency:
    def test_returns_sorted_pathogen_list(self, client):
        c, pool = client
        # The endpoint builds a dynamic query with one COUNT per pathogen term.
        # There are 20 terms, so we need c_0 through c_19 in the mock row.
        row = {f"c_{i}": 0 for i in range(20)}
        row["c_0"] = 15   # Anthrax
        row["c_4"] = 8    # Tularaemia
        row["c_8"] = 42   # Influenza
        _setup_cursor(pool, fetchone=row)
        r = c.get("/api/pathogens/frequency")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        # Should be sorted by count descending
        assert body[0]["label"] == "Influenza"
        assert body[0]["count"] == 42
        assert body[1]["label"] == "Anthrax"
        assert body[1]["count"] == 15
        # Zero-count pathogens should be omitted
        assert all(p["count"] > 0 for p in body)

    def test_all_zero_returns_empty(self, client):
        c, pool = client
        row = {f"c_{i}": 0 for i in range(20)}
        _setup_cursor(pool, fetchone=row)
        r = c.get("/api/pathogens/frequency")
        assert r.status_code == 200
        assert r.json() == []


# ── /api/countries/transparency ─────────────────────────────────────────────

class TestTransparency:
    def test_returns_scored_country_list(self, client):
        c, pool = client
        _setup_cursor(pool, fetchall=[{
            "country_iso3": "DEU",
            "country_name": "Germany",
            "submission_count": 12,
            "first_year": 2012,
            "latest_year": 2024,
            "a1_rate": 0.917,
            "regularity_score": 0.923,
            "recency_score": 1.0,
        }])
        r = c.get("/api/countries/transparency")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert len(body) == 1
        entry = body[0]
        assert entry["country_iso3"] == "DEU"
        assert "transparency_score" in entry
        # transparency = (0.923 * 0.40 + 0.917 * 0.40 + 1.0 * 0.20) * 100
        expected = round((0.923 * 0.40 + 0.917 * 0.40 + 1.0 * 0.20) * 100, 1)
        assert entry["transparency_score"] == expected

    def test_lapsed_country_gets_low_recency(self, client):
        """A country whose latest submission was 8+ years ago gets a low recency score."""
        c, pool = client
        _setup_cursor(pool, fetchall=[{
            "country_iso3": "TLS",
            "country_name": "Timor-Leste",
            "submission_count": 2,
            "first_year": 2013,
            "latest_year": 2014,
            "a1_rate": 0.5,
            "regularity_score": 1.0,
            "recency_score": 0.1,
        }])
        r = c.get("/api/countries/transparency")
        assert r.status_code == 200
        body = r.json()
        entry = body[0]
        # recency=0.1, so score should be quite low
        assert entry["transparency_score"] < 70

    def test_empty_transparency(self, client):
        c, pool = client
        _setup_cursor(pool, fetchall=[])
        r = c.get("/api/countries/transparency")
        assert r.status_code == 200
        assert r.json() == []
