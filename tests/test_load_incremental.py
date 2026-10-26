"""Tests for the incremental loader's entity matching (scripts/06_load_database.py)."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

pytest.importorskip("rapidfuzz")

_spec = spec_from_file_location("load_db", Path(__file__).resolve().parent.parent / "scripts" / "06_load_database.py")
load_db = module_from_spec(_spec)
_spec.loader.exec_module(load_db)
EntityMatcher = load_db.EntityMatcher

KNOWN = [
    ("CAN_001", "CAN", "National Microbiology Laboratory", 2015),
    ("CAN_002", "CAN", "Canadian Food Inspection Agency", 2015),
    ("MEX_003", "MEX", "State Public Health Laboratory of Sonora State", 2021),
    ("MEX_004", "MEX", "State Public Health Laboratory of Nayarit State", 2021),
    ("DNK_008", "DNK", None, 2024),
    ("DNK_001", "DNK", "Statens Serum Institut", 2024),
]


def _matcher(known=KNOWN, used=frozenset(), anchors=()):
    return EntityMatcher("{country}_{n:03d}", known, set(used), anchors=anchors)


class TestEntityMatcher:
    def test_close_name_in_later_year_joins_existing_entity(self):
        m = _matcher()
        assert m.resolve("CAN", "National Microbiology Laboratory (NML)", 2025) == "CAN_001"
        assert m.created == []

    def test_unrelated_name_gets_new_id_after_highest_existing(self):
        m = _matcher()
        assert m.resolve("CAN", "Vaccine and Infectious Disease Organization", 2025) == "CAN_003"
        assert m.created == [("CAN_003", "Vaccine and Infectious Disease Organization")]

    def test_new_ids_skip_ids_retired_by_merges(self):
        # CAN_009 no longer exists in the DB (merged away) but appeared in script 05
        # output; reusing it would let dedup_entities' hardcoded merges hit a new lab.
        m = _matcher(used={"CAN_009"})
        assert m.resolve("CAN", "Brand new lab", 2025) == "CAN_010"

    def test_matching_is_per_country(self):
        m = _matcher()
        assert m.resolve("MEX", "National Microbiology Laboratory", 2025) != "CAN_001"

    def test_same_year_uses_stricter_threshold(self):
        # One submission lists distinct state labs with near-identical names
        m = _matcher()
        assert m.resolve("MEX", "State Public Health Laboratory of Sinaloa State", 2021) not in (
            "MEX_003",
            "MEX_004",
        )

    def test_picks_best_scoring_entity(self):
        m = _matcher()
        assert m.resolve("MEX", "State Public Health Laboratory of Nayarit State", 2025) == "MEX_004"

    def test_new_entity_is_matched_by_later_documents(self):
        m = _matcher()
        first = m.resolve("CAN", "Vaccine and Infectious Disease Organization", 2025)
        assert m.resolve("CAN", "Vaccine and Infectious Disease Organisation", 2026) == first
        assert len(m.created) == 1

    def test_unnamed_row_joins_the_only_unnamed_entity(self):
        m = _matcher()
        assert m.resolve("DNK", None, 2025) == "DNK_008"
        assert m.resolve("DNK", "[Unnamed facility]", 2025) == "DNK_008"

    def test_unnamed_row_without_unnamed_entity_gets_new_id(self):
        m = _matcher()
        new = m.resolve("CAN", None, 2025)
        assert new == "CAN_003"
        assert m.resolve("CAN", None, 2026) == new

    def test_anchor_merge(self):
        known = [("CAN_001", "CAN", "National Microbiology Laboratory", 2015)]
        m = _matcher(known, anchors=[("CAN", "National Microbiology Laboratory")])
        long_name = (
            "National Microbiology Laboratory, Public Health Agency of Canada, "
            "Canadian Science Centre for Human and Animal Health"
        )
        assert m.resolve("CAN", long_name, 2025) == "CAN_001"

    def test_prefixed_id_formats(self):
        m = EntityMatcher("{country}_V{n:03d}", [("GBR_V020", "GBR", "Some vaccine maker", 2020)], {"GBR_V021"})
        assert m.resolve("GBR", "Another vaccine maker entirely", 2025) == "GBR_V022"
