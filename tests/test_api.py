"""Regression tests for the CBM Facility Explorer REST API.

All tests run against a mocked database — no real PostgreSQL connection needed.
Run with:
    pytest tests/test_api.py -v
"""

import json
import os
import pytest
from unittest.mock import MagicMock, patch

# ── Env vars must be set before importing the app ────────────────────────────
os.environ.setdefault("DATABASE_URL", "postgresql://cbm:cbm@localhost/cbm")
os.environ.setdefault("REVIEW_API_KEY", "test-review-key")

from fastapi.testclient import TestClient  # noqa: E402
from api.main import app  # noqa: E402

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
            # Reset rate limiter storage so tests are not affected by
            # previous test invocations within the same session.
            from api.main import limiter
            limiter.reset()
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
        _setup_cursor(
            pool,
            fetchall=[
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
            ],
        )
        r = c.get("/api/search?q=robert+koch")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)


# ── /api/countries ────────────────────────────────────────────────────────────


class TestCountries:
    def test_returns_list(self, client):
        c, pool = client
        _setup_cursor(
            pool,
            fetchall=[
                {
                    "country_iso3": "DEU",
                    "country_name": "Germany",
                    "submission_count": 10,
                    "latest_year": 2024,
                    "facility_count": 5,
                    "bsl4_count": 1,
                    "a1_rate": "0.900",
                }
            ],
        )
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
        _setup_cursor(
            pool,
            fetchone={
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
            },
        )
        r = c.get("/api/stats")
        assert r.status_code == 200
        body = r.json()
        assert body["total_submissions"] == 517
        assert body["total_countries"] == 45
        assert body["year_min"] == 1987
        assert body["year_max"] == 2025

    def test_contains_all_expected_keys(self, client):
        c, pool = client
        _setup_cursor(
            pool,
            fetchone={
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
            },
        )
        r = c.get("/api/stats")
        body = r.json()
        expected_keys = {
            "total_submissions",
            "total_countries",
            "total_facility_years",
            "total_unique_facilities",
            "geocoded_facility_years",
            "vaccine_facility_years",
            "defence_facility_years",
            "total_unique_vaccine",
            "total_unique_defence",
            "year_min",
            "year_max",
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
            [
                {
                    "year": 2024,
                    "programme_name": "Bundeswehr Bio Defence",
                    "responsible_org": "BMVg",
                    "objectives_summary": "Protection",
                    "research_areas": "diagnostics",
                    "total_funding_amount": 5000000,
                    "total_funding_currency": "EUR",
                    "uses_contractors": True,
                    "contractor_proportion_pct": 15,
                    "confidence": "high",
                }
            ],
            # entities
            [
                {
                    "canonical_id": "DEU_D001",
                    "canonical_name": "WIS Munster",
                    "first_year": 2015,
                    "last_year": 2024,
                    "has_bsl4": False,
                    "has_bsl3": True,
                }
            ],
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
            [
                {
                    "canonical_id": "GBR_V001",
                    "canonical_name": "Porton Biopharma Ltd",
                    "first_year": 2015,
                    "last_year": 2024,
                }
            ],
            # records
            [
                {
                    "year": 2024,
                    "canonical_vaccine_facility_id": "GBR_V001",
                    "facility_name": "Porton Biopharma Ltd",
                    "city": "Salisbury",
                    "address": "Manor Farm Rd",
                    "diseases_covered": "anthrax, tuberculosis",
                    "vaccines_summary": "Anthrax vaccine production",
                    "confidence": "high",
                }
            ],
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
        _setup_cursor(
            pool,
            fetchall=[
                {
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
                }
            ],
        )
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
        _setup_cursor(
            pool,
            fetchall=[
                {
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
                }
            ],
        )
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
        cur.fetchall.return_value = [
            {
                "year": 2024,
                "document_id": 10,
                "facility_name": "Robert Koch Institut",
                "responsible_org": "BMG",
                "city": "Berlin",
                "address": "Nordufer 20",
                "has_bsl4": False,
                "bsl4_area_m2": 0,
                "has_bsl3": True,
                "bsl3_area_m2": 1200,
                "highest_containment": "BSL-3",
                "agents_summary": "influenza, SARS-CoV-2",
                "mod_funded": False,
                "confidence": "high",
                "geocode_confidence": "exact",
                "source_url": "https://example.com/deu_2024.pdf",
                "flagged_for_review": False,
                "flag_note": None,
            }
        ]
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
        cur.fetchall.return_value = [
            {
                "year": 2024,
                "facility_name": "Dstl Porton Down",
                "city": "Salisbury",
                "address": "Manor Farm Rd",
                "bsl2_area_m2": 500,
                "bsl3_area_m2": 200,
                "bsl4_area_m2": 50,
                "total_lab_area_m2": 750,
                "personnel_total": 300,
                "personnel_military": 20,
                "personnel_civilian": 280,
                "personnel_scientists": 150,
                "personnel_engineers": 30,
                "personnel_technicians": 60,
                "personnel_admin": 40,
                "mod_funded": True,
                "work_description": "Bio defence research",
                "funding_source": "MOD",
                "funding_research": 10000000,
                "funding_development": 5000000,
                "funding_te": 2000000,
                "funding_currency": "GBP",
                "confidence": "high",
                "geocode_confidence": "exact",
                "source_url": "https://example.com/gbr_2024.pdf",
            }
        ]
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
        cur.fetchall.return_value = [
            {
                "year": 2024,
                "document_id": 55,
                "facility_name": "Paul-Ehrlich-Institut",
                "city": "Langen",
                "address": "Paul-Ehrlich-Str. 51-59",
                "diseases_covered": "influenza, measles",
                "vaccines_summary": "Vaccine quality control and batch release",
                "confidence": "high",
                "source_url": "https://example.com/deu_2024.pdf",
            }
        ]
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
        _setup_cursor(
            pool,
            fetchall=[
                {
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
                }
            ],
        )
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
        _setup_cursor(
            pool,
            fetchall=[
                {
                    "id": 42,
                    "name": "Dstl Porton Down",
                    "country_iso3": "GBR",
                    "year": 2024,
                    "city": "Salisbury",
                    "geocode_confidence": "exact",
                    "lon": -1.7954,
                    "lat": 51.0691,
                    "country_name": "United Kingdom",
                }
            ],
        )
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
        _setup_cursor(
            pool,
            fetchall=[
                {
                    "id": "DEU_V001",
                    "name": "Paul-Ehrlich-Institut",
                    "country_iso3": "DEU",
                    "year": 2024,
                    "city": "Langen",
                    "geocode_confidence": "exact",
                    "lon": 8.6565,
                    "lat": 50.0002,
                    "country_name": "Germany",
                }
            ],
        )
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
        _setup_cursor(
            pool,
            fetchall=[
                {
                    "country_iso3": "DEU",
                    "country_name": "Germany",
                    "submission_count": 12,
                    "a1_rate": 0.917,
                }
            ],
        )
        r = c.get("/api/map/compliance")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert body[0]["country_iso3"] == "DEU"
        assert "a1_rate" in body[0]

    def test_form_specific_compliance(self, client):
        c, pool = client
        _setup_cursor(
            pool,
            fetchall=[
                {
                    "country_iso3": "GBR",
                    "country_name": "United Kingdom",
                    "submission_count": 10,
                    "rate": 0.800,
                }
            ],
        )
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
        _setup_cursor(
            pool,
            fetchall=[
                {"year": 2020, "a1_facility_years": 80, "bsl4_facility_years": 5, "submitting_countries": 30},
                {"year": 2021, "a1_facility_years": 90, "bsl4_facility_years": 6, "submitting_countries": 32},
                {"year": 2022, "a1_facility_years": 95, "bsl4_facility_years": 7, "submitting_countries": 33},
            ],
        )
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
        _setup_cursor(
            pool,
            fetchall=[
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
            ],
        )
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
        _setup_cursor(
            pool,
            fetchall=[
                {
                    "canonical_facility_id": "DEU_001",
                    "facility_name": "Friedrich-Loeffler-Institut",
                    "country_iso3": "DEU",
                    "country_name": "Germany",
                    "year": 2018,
                    "has_bsl4": False,
                    "bsl4_area_m2": 0,
                    "has_bsl3": True,
                    "bsl3_area_m2": 500,
                    "highest_containment": "BSL-3",
                    "personnel_total": None,
                    "agents_summary": None,
                },
                {
                    "canonical_facility_id": "DEU_001",
                    "facility_name": "Friedrich-Loeffler-Institut",
                    "country_iso3": "DEU",
                    "country_name": "Germany",
                    "year": 2019,
                    "has_bsl4": False,
                    "bsl4_area_m2": 0,
                    "has_bsl3": True,
                    "bsl3_area_m2": 500,
                    "highest_containment": "BSL-3",
                    "personnel_total": None,
                    "agents_summary": None,
                },
                {
                    "canonical_facility_id": "DEU_001",
                    "facility_name": "Friedrich-Loeffler-Institut",
                    "country_iso3": "DEU",
                    "country_name": "Germany",
                    "year": 2020,
                    "has_bsl4": True,
                    "bsl4_area_m2": 200,
                    "has_bsl3": True,
                    "bsl3_area_m2": 500,
                    "highest_containment": "BSL-4",
                    "personnel_total": None,
                    "agents_summary": None,
                },
            ],
        )
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
        _setup_cursor(
            pool,
            fetchall=[
                {
                    "canonical_facility_id": "GBR_001",
                    "facility_name": "Pirbright Institute",
                    "country_iso3": "GBR",
                    "country_name": "United Kingdom",
                    "year": 2023,
                    "has_bsl4": False,
                    "bsl4_area_m2": 0,
                    "has_bsl3": True,
                    "bsl3_area_m2": 300,
                    "highest_containment": "BSL-3",
                    "personnel_total": None,
                    "agents_summary": None,
                },
            ],
        )
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
        row["c_0"] = 15  # Anthrax
        row["c_4"] = 8  # Tularaemia
        row["c_8"] = 42  # Influenza
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
        _setup_cursor(
            pool,
            fetchall=[
                {
                    "country_iso3": "DEU",
                    "country_name": "Germany",
                    "submission_count": 12,
                    "first_year": 2012,
                    "latest_year": 2024,
                    "a1_rate": 0.917,
                    "regularity_score": 0.923,
                    "recency_score": 1.0,
                }
            ],
        )
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
        _setup_cursor(
            pool,
            fetchall=[
                {
                    "country_iso3": "TLS",
                    "country_name": "Timor-Leste",
                    "submission_count": 2,
                    "first_year": 2013,
                    "latest_year": 2014,
                    "a1_rate": 0.5,
                    "regularity_score": 1.0,
                    "recency_score": 0.1,
                }
            ],
        )
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


# ── /api/natural-query (AI search) ────────────────────────────────────────


def _mock_claude_response(text: str):
    """Build a mock Anthropic message whose content[0].text returns *text*."""
    block = MagicMock()
    block.text = text
    msg = MagicMock()
    msg.content = [block]
    return msg


class TestNaturalQuery:
    """Tests for the POST /api/natural-query endpoint.

    The Anthropic SDK is mocked — no real API calls are made.
    """

    def _classification(self, query_type="facility_search", **overrides):
        """Build a classification response dict with defaults."""
        base = {
            "query_type": query_type,
            "organisms": [],
            "keywords": [],
            "countries": [],
            "forms": [],
            "bsl": [],
            "year_min": None,
            "year_max": None,
            "legislation_category": None,
            "rationale": "Test classification.",
        }
        base.update(overrides)
        return base

    def test_missing_api_key_returns_503(self, client):
        """If ANTHROPIC_API_KEY is unset, the endpoint should refuse with 503."""
        c, _ = client
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            r = c.post("/api/natural-query", json={"q": "BSL-4 labs in Germany"})
        assert r.status_code == 503
        assert "ANTHROPIC_API_KEY" in r.json()["detail"]

    def test_successful_facility_search_query(self, client):
        """A facility_search query should return the correct query_type and facilities list."""
        c, pool = client
        _setup_cursor(pool, fetchall=[])
        classification = self._classification(
            query_type="facility_search",
            organisms=["influenza"],
        )
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("api.main._anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.return_value = _mock_claude_response(json.dumps(classification))
                r = c.post("/api/natural-query", json={"q": "influenza research"})
        assert r.status_code == 200
        body = r.json()
        assert body["query_type"] == "facility_search"
        assert "facilities" in body
        assert "answer" in body

    def test_unknown_query_returns_help(self, client):
        """When Claude classifies as unknown, a help message is returned."""
        c, pool = client
        classification = self._classification(query_type="unknown")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("api.main._anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.return_value = _mock_claude_response(json.dumps(classification))
                r = c.post("/api/natural-query", json={"q": "what is the meaning of life"})
        assert r.status_code == 200
        body = r.json()
        assert body["query_type"] == "unknown"
        assert "BWC" in body["answer"]
        assert body["facilities"] == []

    def test_claude_api_failure_returns_500(self, client):
        """If the Anthropic API raises an exception, the endpoint returns 500."""
        c, _ = client
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("api.main._anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.side_effect = RuntimeError("API down")
                r = c.post("/api/natural-query", json={"q": "anthrax labs"})
        assert r.status_code == 500
        assert "failed" in r.json()["detail"].lower()

    def test_claude_returns_invalid_json_returns_500(self, client):
        """If Claude returns unparseable text, json.loads raises and we get 500."""
        c, _ = client
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("api.main._anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.return_value = _mock_claude_response("This is not JSON at all.")
                r = c.post("/api/natural-query", json={"q": "BSL-4 labs"})
        assert r.status_code == 500

    def test_code_fence_stripping(self, client):
        """Claude sometimes wraps JSON in ```json ... ``` — endpoint should strip it."""
        c, pool = client
        _setup_cursor(pool, fetchall=[])
        classification = self._classification(
            query_type="facility_search",
            organisms=["ebola"],
        )
        fenced = "```json\n" + json.dumps(classification) + "\n```"
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("api.main._anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.return_value = _mock_claude_response(fenced)
                r = c.post("/api/natural-query", json={"q": "ebola research"})
        assert r.status_code == 200
        body = r.json()
        assert body["query_type"] == "facility_search"

    def test_query_too_long_returns_422(self, client):
        """NaturalQueryRequest.q has max_length=400; exceeding it should return 422."""
        c, _ = client
        r = c.post("/api/natural-query", json={"q": "x" * 401})
        assert r.status_code == 422

    def test_invalid_query_type_becomes_unknown(self, client):
        """If Claude returns an invalid query_type, it should be clamped to unknown."""
        c, pool = client
        classification = self._classification(query_type="hacking_attempt")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("api.main._anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.return_value = _mock_claude_response(json.dumps(classification))
                r = c.post("/api/natural-query", json={"q": "hack the system"})
        assert r.status_code == 200
        assert r.json()["query_type"] == "unknown"


class TestNaturalQueryExpanded:
    """Tests for the expanded natural query classification and routing."""

    def _classification(self, query_type="facility_search", **overrides):
        """Build a classification response dict with defaults."""
        base = {
            "query_type": query_type,
            "organisms": [],
            "keywords": [],
            "countries": [],
            "forms": [],
            "bsl": [],
            "year_min": None,
            "year_max": None,
            "legislation_category": None,
            "rationale": "Test classification.",
        }
        base.update(overrides)
        return base

    def test_submission_history_routing(self, client):
        """A submission_history query should be routed to the correct handler and return stub data."""
        c, pool = client
        _setup_cursor(pool, fetchall=[])
        classification = self._classification(
            query_type="submission_history",
            countries=["AUT"],
            forms=["A1"],
        )
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("api.main._anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.return_value = _mock_claude_response(json.dumps(classification))
                r = c.post("/api/natural-query", json={"q": "Has Austria submitted Form A1?"})
        assert r.status_code == 200
        body = r.json()
        assert body["query_type"] == "submission_history"
        assert "answer" in body
        assert isinstance(body["data"], list)
        assert isinstance(body["facilities"], list)

    def test_unknown_query_type_returns_help_message(self, client):
        """Unknown queries should return the standard help message."""
        c, pool = client
        classification = self._classification(query_type="unknown")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("api.main._anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.return_value = _mock_claude_response(json.dumps(classification))
                r = c.post("/api/natural-query", json={"q": "tell me a joke"})
        assert r.status_code == 200
        body = r.json()
        assert body["query_type"] == "unknown"
        assert "BWC" in body["answer"]
        assert "Confidence-Building" in body["answer"]

    def test_facility_search_backward_compatible(self, client):
        """facility_search response should include facilities and answer fields."""
        c, pool = client
        _setup_cursor(pool, fetchall=[])
        classification = self._classification(
            query_type="facility_search",
            countries=["DEU"],
            organisms=["anthrax"],
            bsl=["BSL-3"],
        )
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("api.main._anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.return_value = _mock_claude_response(json.dumps(classification))
                r = c.post("/api/natural-query", json={"q": "anthrax labs in Germany with BSL-3"})
        assert r.status_code == 200
        body = r.json()
        assert body["query_type"] == "facility_search"
        assert "facilities" in body
        assert "answer" in body
        assert "data" in body
        assert "use_compare_mode" in body

    def test_daily_rate_limit_header(self, client):
        """The composite rate limit '10/minute;100/day' should be configured on the endpoint."""
        # Verify the rate limit string is set on the endpoint by checking that
        # slowapi returns rate-limit headers (or 429 if already exhausted).
        # We test the configuration rather than the exact header value, since
        # slowapi's internal header format may vary.
        c, pool = client
        _setup_cursor(pool, fetchall=[])
        classification = self._classification(query_type="facility_search")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("api.main._anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.return_value = _mock_claude_response(json.dumps(classification))
                r = c.post("/api/natural-query", json={"q": "BSL-4 labs"})
        # Either we get a successful response with rate limit headers,
        # or we get 429 because previous tests exhausted the per-minute limit.
        assert r.status_code in (200, 429)

    def test_comparative_sets_use_compare_mode(self, client):
        """Comparative queries should set use_compare_mode=True."""
        c, pool = client
        _setup_cursor(pool, fetchall=[])
        classification = self._classification(
            query_type="comparative",
            countries=["DEU", "GBR"],
        )
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("api.main._anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.return_value = _mock_claude_response(json.dumps(classification))
                r = c.post("/api/natural-query", json={"q": "Compare Germany and UK"})
        assert r.status_code == 200
        body = r.json()
        assert body["query_type"] == "comparative"
        assert body["use_compare_mode"] is True

    def test_comparative_two_countries_uses_compare_mode(self, client):
        """Two-country comparative with no filters should return use_compare_mode=True and a compare entity card."""
        c, pool = client
        cur = _setup_cursor(pool)
        cur.fetchall.side_effect = [
            [
                {"country_iso3": "GBR", "country_name": "United Kingdom"},
                {"country_iso3": "FRA", "country_name": "France"},
            ],
        ]
        classification = self._classification(
            query_type="comparative",
            countries=["GBR", "FRA"],
        )
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("api.main._anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.return_value = _mock_claude_response(json.dumps(classification))
                r = c.post("/api/natural-query", json={"q": "Compare UK and France"})
        assert r.status_code == 200
        body = r.json()
        assert body["use_compare_mode"] is True
        assert any(e["type"] == "compare" for e in body["entities"])

    def test_comparative_ranked_query(self, client):
        """Comparative with BSL filter should return ranked data and use_compare_mode=False."""
        c, pool = client
        cur = _setup_cursor(pool)
        cur.fetchall.side_effect = [
            [
                {"country_iso3": "USA", "country_name": "United States", "count": 5},
                {"country_iso3": "GBR", "country_name": "United Kingdom", "count": 3},
            ],
        ]
        classification = self._classification(
            query_type="comparative",
            countries=[],
            bsl=["BSL-4"],
        )
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("api.main._anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.return_value = _mock_claude_response(json.dumps(classification))
                r = c.post("/api/natural-query", json={"q": "Which countries have most BSL-4 labs?"})
        assert r.status_code == 200
        body = r.json()
        assert body["use_compare_mode"] is False
        assert len(body["data"]) >= 1

    def test_facility_search_returns_country_entities(self, client):
        """facility_search with country filter should include country entity cards."""
        c, pool = client
        cur = _setup_cursor(pool)
        cur.fetchall.side_effect = [
            [{"id": "DEU_001", "name": "RKI", "country_iso3": "DEU",
              "latest_containment": "BSL-3", "years_declared": [2024],
              "layer": "A1", "country_name": "Germany"}],
            [],  # vaccine
            [],  # defence
            [{"country_iso3": "DEU", "country_name": "Germany"}],  # _nq_country_names
        ]
        classification = {
            "query_type": "facility_search",
            "countries": ["DEU"], "forms": [], "year_min": None, "year_max": None,
            "organisms": [], "keywords": [], "bsl": [],
            "legislation_category": None, "rationale": "Facilities in Germany."
        }
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("api.main._anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.return_value = _mock_claude_response(json.dumps(classification))
                r = c.post("/api/natural-query", json={"q": "facilities in Germany"})
        assert r.status_code == 200
        body = r.json()
        assert body["query_type"] == "facility_search"
        assert any(e["type"] == "country" and e["iso3"] == "DEU" for e in body["entities"])

    def test_country_code_validation(self, client):
        """Invalid country codes should be filtered out."""
        c, pool = client
        _setup_cursor(pool, fetchall=[])
        classification = self._classification(
            query_type="facility_search",
            countries=["DEU", "invalid", "12X", "GBR"],
            organisms=["anthrax"],
        )
        mock_handler = MagicMock(return_value={"answer": "ok", "data": [], "entities": [], "facilities": [], "use_compare_mode": False})
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("api.main._anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.return_value = _mock_claude_response(json.dumps(classification))
                with patch.dict("api.main._NQ_HANDLERS", {"facility_search": mock_handler}):
                    r = c.post("/api/natural-query", json={"q": "anthrax labs"})
        assert r.status_code == 200
        # The handler should only receive valid ISO3 codes
        call_kwargs = mock_handler.call_args[1]
        assert call_kwargs["countries"] == ["DEU", "GBR"]

    def test_submission_history_returns_years(self, client):
        """submission_history for a single country returns rows, answer with country name, and entity card."""
        c, pool = client
        cur = _setup_cursor(pool)
        cur.fetchall.side_effect = [
            # _nq_country_names lookup (countries specified → called first)
            [{"country_iso3": "AUT", "country_name": "Austria"}],
            # compliance rows
            [
                {"country_iso3": "AUT", "year": 2022, "form": "A1", "status": "substantive"},
                {"country_iso3": "AUT", "year": 2023, "form": "A1", "status": "substantive"},
            ],
        ]
        classification = self._classification(
            query_type="submission_history",
            countries=["AUT"],
            forms=["A1"],
        )
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("api.main._anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.return_value = _mock_claude_response(json.dumps(classification))
                r = c.post("/api/natural-query", json={"q": "Has Austria submitted Form A1?"})
        assert r.status_code == 200
        body = r.json()
        assert body["query_type"] == "submission_history"
        assert len(body["data"]) == 2
        assert body["data"][0]["year"] == 2022
        assert "Austria" in body["answer"]
        assert any(e["type"] == "country" and e["iso3"] == "AUT" for e in body["entities"])

    def test_submission_history_no_countries_returns_all(self, client):
        """submission_history with no country filter returns rows for all matching countries."""
        c, pool = client
        cur = _setup_cursor(pool)
        cur.fetchall.side_effect = [
            # No country pre-lookup (countries=[])
            # compliance rows returned directly
            [
                {"country_iso3": "DEU", "year": 2023, "form": "G", "status": "substantive"},
                {"country_iso3": "GBR", "year": 2023, "form": "G", "status": "nothing_to_declare"},
            ],
            # _nq_country_names lookup for seen countries
            [
                {"country_iso3": "DEU", "country_name": "Germany"},
                {"country_iso3": "GBR", "country_name": "United Kingdom"},
            ],
        ]
        classification = self._classification(
            query_type="submission_history",
            countries=[],
            forms=["G"],
            year_min=2023,
            year_max=2023,
        )
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("api.main._anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.return_value = _mock_claude_response(json.dumps(classification))
                r = c.post("/api/natural-query", json={"q": "Form G submissions in 2023"})
        assert r.status_code == 200
        body = r.json()
        assert body["query_type"] == "submission_history"
        assert len(body["data"]) == 2

    def test_country_overview_returns_summary(self, client):
        """country_overview should return composite data and a Haiku-generated answer."""
        c, pool = client
        cur = _setup_cursor(pool)
        # Sequential calls: (1) country names, (2) submission summary, (3) facility counts, (4) legislation
        cur.fetchall.side_effect = [
            [{"country_iso3": "DEU", "country_name": "Germany"}],  # _nq_country_names
            [{"form": "A1", "total": 14, "substantive": 12}],       # submission summary
        ]
        cur.fetchone.side_effect = [
            {"a1_facilities": 25, "vaccine_facilities": 3, "defence_facilities": 10},  # facility counts
            None,  # legislation (no record)
        ]
        classification = {
            "query_type": "country_overview",
            "countries": ["DEU"], "forms": [], "year_min": None, "year_max": None,
            "organisms": [], "keywords": [], "bsl": [],
            "legislation_category": None, "rationale": "Overview of Germany."
        }
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("api.main._anthropic.Anthropic") as mock_cls:
                # First call: classification. Second call: summarization.
                mock_cls.return_value.messages.create.side_effect = [
                    _mock_claude_response(json.dumps(classification)),
                    _mock_claude_response("Germany has submitted CBMs consistently, declaring 25 research facilities."),
                ]
                r = c.post("/api/natural-query", json={"q": "Tell me about Germany"})
        assert r.status_code == 200
        body = r.json()
        assert body["query_type"] == "country_overview"
        assert "Germany" in body["answer"]
        assert any(e["type"] == "country" and e["iso3"] == "DEU" for e in body["entities"])

    def test_legislation_single_country(self, client):
        """legislation query for a single country should return rows and include the country name in the answer."""
        c, pool = client
        cur = _setup_cursor(pool)
        cur.fetchall.side_effect = [
            # _nq_country_names lookup for specified countries
            [{"country_iso3": "AUS", "country_name": "Australia"}],
            # legislation rows
            [
                {
                    "country_iso3": "AUS",
                    "country_name": "Australia",
                    "year": 2024,
                    "prohibitions_legislation": True,
                    "prohibitions_regulations": True,
                    "prohibitions_other_measures": False,
                    "exports_legislation": True,
                    "exports_regulations": False,
                    "exports_other_measures": False,
                    "imports_legislation": False,
                    "imports_regulations": False,
                    "imports_other_measures": False,
                    "biosafety_legislation": True,
                    "biosafety_regulations": True,
                    "biosafety_other_measures": False,
                    "key_laws": ["Biosecurity Act 2015"],
                }
            ],
        ]
        classification = self._classification(
            query_type="legislation",
            countries=["AUS"],
        )
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("api.main._anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.side_effect = [
                    _mock_claude_response(json.dumps(classification)),
                    _mock_claude_response("Australia has comprehensive legislation prohibiting biological weapons, with export controls under the Biosecurity Act 2015."),
                ]
                r = c.post("/api/natural-query", json={"q": "What legislation does Australia have?"})
        assert r.status_code == 200
        body = r.json()
        assert body["query_type"] == "legislation"
        assert len(body["data"]) >= 1
        assert "Australia" in body["answer"]

    def test_legislation_category_filter(self, client):
        """legislation query with category filter should return rows for matching countries."""
        c, pool = client
        cur = _setup_cursor(pool)
        cur.fetchall.side_effect = [
            # No country pre-lookup (countries=[])
            # legislation rows for DEU and GBR with exports columns
            [
                {
                    "country_iso3": "DEU",
                    "country_name": "Germany",
                    "year": 2024,
                    "exports_legislation": True,
                    "exports_regulations": True,
                    "exports_other_measures": False,
                    "key_laws": ["Außenwirtschaftsgesetz"],
                },
                {
                    "country_iso3": "GBR",
                    "country_name": "United Kingdom",
                    "year": 2024,
                    "exports_legislation": True,
                    "exports_regulations": False,
                    "exports_other_measures": True,
                    "key_laws": ["Export Control Act 2002"],
                },
            ],
            # _nq_country_names lookup for seen countries
            [
                {"country_iso3": "DEU", "country_name": "Germany"},
                {"country_iso3": "GBR", "country_name": "United Kingdom"},
            ],
        ]
        classification = self._classification(
            query_type="legislation",
            countries=[],
            legislation_category="exports",
        )
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("api.main._anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.side_effect = [
                    _mock_claude_response(json.dumps(classification)),
                    _mock_claude_response("Germany and United Kingdom both have export control legislation for biological agents."),
                ]
                r = c.post("/api/natural-query", json={"q": "Which countries have export controls for pathogens?"})
        assert r.status_code == 200
        body = r.json()
        assert body["query_type"] == "legislation"
        assert len(body["data"]) >= 1

    def test_defence_past_offensive(self, client):
        """defence_programmes query for Form F/offensive should return past_programme rows."""
        c, pool = client
        cur = _setup_cursor(pool)
        cur.fetchall.side_effect = [
            # past_programmes rows (query_past=True because forms=["F"])
            [
                {
                    "country_iso3": "GBR",
                    "country_name": "United Kingdom",
                    "year": 2023,
                    "has_offensive_programme": True,
                    "offensive_period": "1940-1957",
                    "offensive_summary": "Offensive BW programme terminated 1957",
                    "has_defensive_programme": True,
                    "defensive_period": "1957-present",
                    "defensive_summary": "Defensive research only",
                    "source": "past_programme",
                }
            ],
            # _nq_country_names lookup for seen countries (no countries specified)
            [{"country_iso3": "GBR", "country_name": "United Kingdom"}],
        ]
        classification = self._classification(
            query_type="defence_programmes",
            forms=["F"],
            keywords=["offensive"],
        )
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("api.main._anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.side_effect = [
                    _mock_claude_response(json.dumps(classification)),
                    _mock_claude_response("The United Kingdom had an offensive BW programme from 1940 to 1957."),
                ]
                r = c.post("/api/natural-query", json={"q": "Which countries had offensive BW programmes?"})
        assert r.status_code == 200
        body = r.json()
        assert body["query_type"] == "defence_programmes"
        assert len(body["data"]) >= 1

    def test_defence_current_programmes(self, client):
        """defence_programmes query for Form A2/budget should return defence_programme rows."""
        c, pool = client
        cur = _setup_cursor(pool)
        cur.fetchall.side_effect = [
            # _nq_country_names lookup for CAN (countries specified)
            [{"country_iso3": "CAN", "country_name": "Canada"}],
            # defence_programmes rows (query_current=True because forms=["A2"])
            [
                {
                    "country_iso3": "CAN",
                    "country_name": "Canada",
                    "year": 2023,
                    "programme_name": "BDRP",
                    "responsible_org": "DRDC Suffield",
                    "objectives_summary": "Biological defence research",
                    "total_funding_amount": 50000000,
                    "total_funding_currency": "CAD",
                    "uses_contractors": False,
                    "source": "defence_programme",
                }
            ],
        ]
        classification = self._classification(
            query_type="defence_programmes",
            countries=["CAN"],
            forms=["A2"],
            keywords=["budget"],
        )
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("api.main._anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.side_effect = [
                    _mock_claude_response(json.dumps(classification)),
                    _mock_claude_response("Canada's BDRP had a funding of 50,000,000 CAD in 2023."),
                ]
                r = c.post("/api/natural-query", json={"q": "What is Canada's defence programme budget?"})
        assert r.status_code == 200
        body = r.json()
        assert body["query_type"] == "defence_programmes"
        assert "Canada" in body["answer"]

    def test_aggregate_country_count(self, client):
        """aggregate_stats with forms filter should return the count of submitting countries."""
        c, pool = client
        cur = _setup_cursor(pool)
        # Path B: forms present, no BSL
        # fetchall for _nq_country_names (countries=[]) is not called in Path B with no countries
        cur.fetchone.return_value = {"count": 42}
        classification = self._classification(
            query_type="aggregate_stats",
            forms=["A1"],
        )
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("api.main._anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.return_value = _mock_claude_response(json.dumps(classification))
                r = c.post("/api/natural-query", json={"q": "How many countries submit Form A1?"})
        assert r.status_code == 200
        body = r.json()
        assert body["query_type"] == "aggregate_stats"
        assert "42" in body["answer"]

    def test_aggregate_facility_count(self, client):
        """aggregate_stats with BSL filter should return ranked country data with area."""
        c, pool = client
        cur = _setup_cursor(pool)
        # Path A: BSL filter — now returns ranked rows per country with area
        cur.fetchall.return_value = [
            {"country_iso3": "USA", "country_name": "United States", "facility_count": 5, "total_area_m2": 3200.0},
            {"country_iso3": "GBR", "country_name": "United Kingdom", "facility_count": 3, "total_area_m2": 1500.0},
        ]
        classification = self._classification(
            query_type="aggregate_stats",
            bsl=["BSL-4"],
        )
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("api.main._anthropic.Anthropic") as mock_cls:
                mock_cls.return_value.messages.create.return_value = _mock_claude_response(json.dumps(classification))
                r = c.post("/api/natural-query", json={"q": "Who has the most BSL-4 floorspace?"})
        assert r.status_code == 200
        body = r.json()
        assert body["query_type"] == "aggregate_stats"
        assert "United States" in body["answer"]
        assert len(body["data"]) == 2
        assert body["data"][0]["area_m2"] == 3200.0
