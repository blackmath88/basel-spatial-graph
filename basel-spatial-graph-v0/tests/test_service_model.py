"""The normalized service domain model."""
import pytest

from app.service_model import (
    CATEGORY_COLORS,
    CATEGORY_LABELS,
    ESSENTIAL_CATEGORIES,
    ServiceCategory,
    ServiceLocation,
    category_label,
)


def make(**overrides) -> ServiceLocation:
    payload = dict(
        id="service:pharmacy:osm:node:1", category=ServiceCategory.PHARMACY,
        lon=7.59, lat=47.55, source="OpenStreetMap", source_dataset="OSM amenity=pharmacy",
        source_id="node/1",
    )
    payload.update(overrides)
    return ServiceLocation(**payload)


# --- category typing ---------------------------------------------------------
def test_categories_are_typed_not_arbitrary_strings():
    assert ServiceCategory.parse("pharmacy") is ServiceCategory.PHARMACY
    assert ServiceCategory.parse(" Pharmacy ") is ServiceCategory.PHARMACY
    assert ServiceCategory.parse(ServiceCategory.PARK) is ServiceCategory.PARK


def test_unknown_category_is_rejected_with_the_known_list():
    with pytest.raises(ValueError) as excinfo:
        ServiceCategory.parse("kebab")
    assert "kebab" in str(excinfo.value)
    assert "pharmacy" in str(excinfo.value)


def test_the_six_essential_categories():
    assert [c.value for c in ESSENTIAL_CATEGORIES] == [
        "grocery", "pharmacy", "healthcare", "school", "park", "sport",
    ]
    assert set(ESSENTIAL_CATEGORIES) < set(ServiceCategory)


def test_every_category_has_a_label_and_colour():
    for category in ServiceCategory:
        assert CATEGORY_LABELS[category]
        assert CATEGORY_COLORS[category].startswith("#")


def test_labels_are_separate_from_canonical_ids():
    assert ServiceCategory.GROCERY.value == "grocery"
    assert category_label(ServiceCategory.GROCERY) == "Groceries"


# --- names -------------------------------------------------------------------
def test_missing_names_are_never_invented():
    service = make(category=ServiceCategory.PARK, name=None)
    assert service.name is None
    assert service.display_name == "Park (unnamed)"
    assert service.to_dict()["name"] is None
    assert service.to_feature()["properties"]["name"] is None


def test_a_real_name_is_used_as_is():
    assert make(name="Bäumlihof Apotheke").display_name == "Bäumlihof Apotheke"


# --- routability -------------------------------------------------------------
def test_a_service_is_not_routable_until_snapped():
    assert make().is_routable is False
    assert make(access_node_id="n1", access_quality="good").is_routable is True
    assert make(access_node_id="n1", access_quality="poor").is_routable is True
    assert make(access_node_id=None, access_quality="unreachable").is_routable is False


# --- serialization -----------------------------------------------------------
def test_round_trips_through_a_dict():
    original = make(name="Coop", attributes={"shop": "supermarket"},
                    access_node_id="n1", access_distance_m=12.5, access_quality="good")
    restored = ServiceLocation.from_dict(original.to_dict())
    assert restored == original
    assert restored.category is ServiceCategory.PHARMACY


def test_from_dict_ignores_derived_keys():
    payload = make().to_dict()
    payload["geometry"] = {"type": "Point", "coordinates": [0, 0]}
    payload["unexpected"] = "ignored"
    assert ServiceLocation.from_dict(payload).lon == 7.59


def test_summary_carries_full_provenance():
    summary = make(name="Coop", access_node_id="n1", access_distance_m=12.5,
                   access_quality="good", source_url="https://osm.org/node/1",
                   license="ODbL 1.0", retrieved_at="2026-01-01T00:00:00+00:00").summary()
    assert summary["geometry"] == {"type": "Point", "coordinates": [7.59, 47.55]}
    assert summary["access"] == {"node_id": "n1", "snap_distance_m": 12.5, "quality": "good"}
    provenance = summary["provenance"]
    assert provenance["source"] == "OpenStreetMap"
    assert provenance["source_id"] == "node/1"
    assert provenance["dataset"] == "OSM amenity=pharmacy"
    assert provenance["retrieved_at"] == "2026-01-01T00:00:00+00:00"
    assert provenance["license"] == "ODbL 1.0"
    assert provenance["category"] == "pharmacy"
    assert provenance["derived"] is False
