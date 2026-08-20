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


def make(access=None, **overrides) -> ServiceLocation:
    payload = dict(
        id="service:pharmacy:osm:node:1", category=ServiceCategory.PHARMACY,
        lon=7.59, lat=47.55, source="OpenStreetMap", source_dataset="OSM amenity=pharmacy",
        source_id="node/1",
    )
    payload.update(overrides)
    service = ServiceLocation(**payload)
    for network, (node_id, distance, quality) in (access or {}).items():
        service.set_access(network, node_id, distance, quality)
    return service


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
    assert make(access={"walk": ("n1", 10.0, "good")}).is_routable is True
    assert make(access={"walk": ("n1", 200.0, "poor")}).is_routable is True
    assert make(access={"walk": (None, 900.0, "unreachable")}).is_routable is False


def test_networks_are_attached_independently():
    """A service can be well attached to one network and badly to another."""
    service = make(access={"walk": ("w1", 10.0, "good"), "bike": (None, 800.0, "unreachable")})
    assert service.is_routable_on("walk") is True
    assert service.is_routable_on("bike") is False
    assert service.access_for("bike").distance_m == 800.0
    assert service.access_for("nothing-prepared").quality == "unsnapped"
    # The bare properties keep meaning "on foot".
    assert service.access_node_id == "w1"


def test_a_v03_cache_still_loads():
    """The old flat walking attachment maps onto the walk network."""
    legacy = {
        "id": "service:park:osm:way:1", "category": "park", "lon": 7.59, "lat": 47.55,
        "source": "OpenStreetMap", "source_dataset": "d", "source_id": "way/1",
        "access_node_id": "n7", "access_distance_m": 21.0, "access_quality": "good",
    }
    service = ServiceLocation.from_dict(legacy)
    assert service.access_for("walk").node_id == "n7"
    assert service.is_routable_on("walk") is True
    assert service.is_routable_on("bike") is False


# --- serialization -----------------------------------------------------------
def test_round_trips_through_a_dict():
    original = make(name="Coop", attributes={"shop": "supermarket"},
                    access={"walk": ("n1", 12.5, "good"), "bike": ("b9", 30.0, "good")})
    restored = ServiceLocation.from_dict(original.to_dict())
    assert restored == original
    assert restored.category is ServiceCategory.PHARMACY
    assert restored.access_for("bike").node_id == "b9"


def test_from_dict_ignores_derived_keys():
    payload = make().to_dict()
    payload["geometry"] = {"type": "Point", "coordinates": [0, 0]}
    payload["unexpected"] = "ignored"
    assert ServiceLocation.from_dict(payload).lon == 7.59


def test_summary_carries_full_provenance():
    summary = make(name="Coop", access={"walk": ("n1", 12.5, "good")},
                   source_url="https://osm.org/node/1",
                   license="ODbL 1.0", retrieved_at="2026-01-01T00:00:00+00:00").summary()
    assert summary["geometry"] == {"type": "Point", "coordinates": [7.59, 47.55]}
    assert summary["access"] == {"node_id": "n1", "snap_distance_m": 12.5, "quality": "good"}
    assert summary["access_network"] == "walk"
    provenance = summary["provenance"]
    assert provenance["source"] == "OpenStreetMap"
    assert provenance["source_id"] == "node/1"
    assert provenance["dataset"] == "OSM amenity=pharmacy"
    assert provenance["retrieved_at"] == "2026-01-01T00:00:00+00:00"
    assert provenance["license"] == "ODbL 1.0"
    assert provenance["category"] == "pharmacy"
    assert provenance["derived"] is False
