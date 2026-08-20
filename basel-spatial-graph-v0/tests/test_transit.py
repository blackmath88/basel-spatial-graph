"""Schedule-aware transit: GTFS handling, RAPTOR, itineraries, the API.

Everything here runs on the four-stop fixture timetable. Nothing touches the
224 MB Swiss feed, and the suite blocks sockets outright.
"""
import datetime

import pytest
from fastapi.testclient import TestClient

from app.errors import (
    InvalidDepartureError,
    TransitSourceError,
    TransitUnavailableError,
    UnroutableServiceError,
)
from app.main import app
from app.multimodal import MultimodalAccessibilityService, parse_departure
from app.transit_index import TransitIndex
from app.transit_model import (
    format_gtfs_time,
    parse_gtfs_date,
    parse_gtfs_time,
    route_type_label,
)
from app.transit_sources import fixture_timetable, load_transit
from app.transit_sources.cache import read_cache, write_cache

THURSDAY = datetime.date(2026, 8, 20)
SATURDAY = datetime.date(2026, 8, 22)
FRIDAY = datetime.date(2026, 8, 21)
WEDNESDAY_AS_WEEKEND = datetime.date(2026, 7, 1)   # calendar exception in the fixture
STOP_A, STOP_B, STOP_C, STOP_D = 0, 1, 2, 3


@pytest.fixture
def timetable():
    return fixture_timetable()


@pytest.fixture
def multimodal(streets, transit_index, service_index, entity_graph):
    return MultimodalAccessibilityService(streets, transit_index, entity_graph, service_index)


@pytest.fixture
def client():
    return TestClient(app)


def at(hour, minute=0):
    return hour * 3600 + minute * 60


# --- GTFS time handling -------------------------------------------------------
def test_after_midnight_times_are_parsed_not_rejected():
    """25:15:00 is 01:15 on the next calendar day, still the previous service day."""
    assert parse_gtfs_time("25:15:00") == 25 * 3600 + 15 * 60
    assert parse_gtfs_time("00:00:00") == 0
    assert parse_gtfs_time("23:59:59") == 86399
    assert format_gtfs_time(90900) == "25:15:00"


@pytest.mark.parametrize("bad", ["", None, "nonsense", "12:70:00", "10:00", "-1:00:00"])
def test_malformed_times_are_rejected(bad):
    assert parse_gtfs_time(bad) is None


def test_dates_and_route_types():
    assert parse_gtfs_date("20260819") == 20260819
    assert parse_gtfs_date("nope") is None
    assert route_type_label(900) == "Tram"
    assert route_type_label(109) == "S-Bahn"
    assert route_type_label(700) == "Bus"
    assert route_type_label("nonsense") == "Transit"


# --- building the timetable ---------------------------------------------------
def test_trips_group_into_patterns(timetable):
    assert timetable.stop_count == 4
    assert timetable.route_count == 4
    assert timetable.trip_count == 10
    # T1's six trips share one stop sequence, so they are one pattern.
    assert timetable.pattern_count == 4


def test_route_labels_read_like_a_passenger_would_say_them(timetable):
    assert {r.label for r in timetable.routes} == {
        "Tram 8", "Bus 30", "Tram 2", "Bus N1"}


def test_service_window_covers_the_calendar(timetable):
    assert timetable.service_window() == (20260101, 20261231)
    assert timetable.serves(THURSDAY) is True
    assert timetable.serves(datetime.date(2025, 1, 1)) is False


# --- calendars ----------------------------------------------------------------
def test_weekday_and_weekend_calendars_differ(timetable):
    weekday = timetable.day_view(THURSDAY)
    weekend = timetable.day_view(SATURDAY)
    assert weekday.active_trip_count > weekend.active_trip_count
    assert weekend.active_trip_count > 0


def test_a_calendar_exception_turns_a_wednesday_into_a_weekend(timetable):
    """The fixture removes WKDY and adds WKND on 2026-07-01."""
    exception_day = timetable.day_view(WEDNESDAY_AS_WEEKEND)
    saturday = timetable.day_view(SATURDAY)
    assert exception_day.active_trip_count == saturday.active_trip_count


def test_an_after_midnight_trip_belongs_to_the_previous_service_day(timetable):
    """A 25:05 departure has to show up at 01:05 the next morning."""
    view = timetable.day_view(FRIDAY)
    for pattern in range(timetable.pattern_count):
        times = view.pattern_times(pattern)
        if times is None:
            continue
        stops = [timetable.stop_ids[i] for i in timetable.pattern_stop_slice(pattern)]
        if stops == ["stopA", "stopC"]:
            first_departures = sorted(int(d) for d in times[1][:, 0])
            assert first_departures[0] == at(1, 5)      # yesterday's run, shifted
            assert first_departures[-1] == at(25, 5)    # today's run
            return
    pytest.fail("the night pattern was not found")


# --- the search ---------------------------------------------------------------
def test_waiting_is_real_time(transit_index):
    """Standing at stopA at 10:00, the 10:05 tram means five minutes of waiting."""
    result = transit_index.search({STOP_A: at(10)}, at(10), at(10, 30), THURSDAY)
    assert int(result.arrival[STOP_B]) == at(10, 9)     # 5 min wait + 4 min ride
    assert int(result.arrival[STOP_C]) == at(10, 13)


def test_a_later_arrival_at_the_stop_catches_a_later_departure(transit_index):
    """Arriving at 10:06 misses the 10:05 and waits for the 10:15."""
    result = transit_index.search({STOP_A: at(10, 6)}, at(10, 6), at(10, 40), THURSDAY)
    assert int(result.arrival[STOP_B]) == at(10, 19)


def test_a_departure_after_the_last_service_reaches_nothing(transit_index):
    result = transit_index.search({STOP_A: at(23)}, at(23), at(23, 45), THURSDAY)
    assert set(result.reached_stops()) == {STOP_A}


def test_a_budget_that_ends_before_the_ride_reaches_nothing(transit_index):
    result = transit_index.search({STOP_A: at(10)}, at(10), at(10, 8), THURSDAY)
    assert set(result.reached_stops()) == {STOP_A}


def test_one_transfer_journey(transit_index):
    """Tram 8 to stopB, three minutes on the platform, bus 30 to stopD."""
    result = transit_index.search({STOP_A: at(10)}, at(10), at(10, 30), THURSDAY, max_transfers=1)
    assert int(result.arrival[STOP_D]) == at(10, 16)
    assert result.transfers_to(STOP_D) == 1
    legs = transit_index.journey_legs(result, STOP_D)
    rides = [leg for leg in legs if leg["kind"] == "ride"]
    assert [leg["route"]["label"] for leg in rides] == ["Tram 8", "Bus 30"]
    assert rides[0]["to_stop"]["id"] == "stopB"
    assert rides[1]["departure_seconds"] == at(10, 12)


def test_the_max_transfer_limit_is_respected(transit_index):
    """With no transfers allowed, stopD is only reachable by walking from stopC."""
    result = transit_index.search({STOP_A: at(10)}, at(10), at(10, 30), THURSDAY, max_transfers=0)
    assert result.transfers_to(STOP_D) == 0
    legs = transit_index.journey_legs(result, STOP_D)
    assert [leg["kind"] for leg in legs] == ["ride", "transfer"]
    assert int(result.arrival[STOP_D]) > at(10, 16)     # slower than the bus


def test_a_platform_interchange_costs_time(streets, transit_index):
    """A transfer needs enough time to make the connection, not zero.

    With ten minutes needed on the platform, the 10:12 bus is missed. The
    fastest remaining option is the tram to stopC and a walk, not the 10:32.
    """
    slow = TransitIndex(fixture_timetable(), mode="fixture",
                        min_transfer_seconds=600).attach_to_network(streets)
    quick = transit_index.search({STOP_A: at(10)}, at(10), at(10, 40), THURSDAY, max_transfers=1)
    patient = slow.search({STOP_A: at(10)}, at(10), at(10, 40), THURSDAY, max_transfers=1)
    assert int(quick.arrival[STOP_D]) == at(10, 16)
    assert int(patient.arrival[STOP_D]) > int(quick.arrival[STOP_D])
    rides = [leg for leg in slow.journey_legs(patient, STOP_D) if leg["kind"] == "ride"]
    assert [leg["route"]["label"] for leg in rides] == ["Tram 8"]


def test_the_night_bus_is_caught_after_midnight(transit_index):
    result = transit_index.search({STOP_A: at(0, 30)}, at(0, 30), at(1, 30), FRIDAY)
    assert int(result.arrival[STOP_C]) == at(1, 13)


def test_the_weekend_timetable_is_a_different_answer(transit_index):
    weekday = transit_index.search({STOP_A: at(10)}, at(10), at(10, 30), THURSDAY)
    weekend = transit_index.search({STOP_A: at(10)}, at(10), at(10, 30), SATURDAY)
    assert int(weekday.arrival[STOP_D]) == at(10, 16)   # tram + bus
    assert int(weekend.arrival[STOP_D]) == at(10, 11)   # the direct weekend tram
    assert weekend.arrival[STOP_B] > weekday.arrival[STOP_B]


# --- stop attachment ----------------------------------------------------------
def test_stops_attach_to_the_walking_network(transit_index):
    assert all(access.is_routable for access in transit_index.stop_access)
    assert transit_index.access_map


def test_a_stop_far_from_any_street_is_flagged(streets):
    index = TransitIndex(fixture_timetable(), mode="fixture")
    index.timetable.stop_lat[STOP_D] = 47.90        # move it far away
    index.timetable.stop_lon[STOP_D] = 8.50
    index.attach_to_network(streets)
    assert index.stop_access[STOP_D].quality == "unreachable"
    assert not index.stop_access[STOP_D].is_routable


# --- departure time -----------------------------------------------------------
def test_departure_time_formats():
    reference = datetime.datetime(2026, 8, 20, 9, 0)
    assert parse_departure("14:30", reference).hour == 14
    assert parse_departure("2026-08-20T14:30", reference).date() == THURSDAY
    assert parse_departure(None, reference).hour == 9


def test_departure_time_is_zurich_local():
    moment = parse_departure("2026-08-20T14:30")
    assert moment.tzinfo is not None
    assert moment.utcoffset().total_seconds() in (3600, 7200)   # CET or CEST


def test_an_unreadable_departure_time_is_a_clean_error():
    with pytest.raises(InvalidDepartureError):
        parse_departure("half past two")


def test_a_date_outside_the_feed_falls_back_and_says_so(multimodal):
    result = multimodal.calculate(47.5505, 7.5745, minutes=20,
                                  departure_time="2019-01-02T10:00")
    assert result["service_date_is_requested_date"] is False
    assert any("does not cover" in note for note in result["notes"])


# --- the multimodal answer ----------------------------------------------------
def test_transit_unlocks_destinations_walking_cannot_reach(multimodal, streets,
                                                           service_index, entity_graph):
    from app.accessibility import WalkingAccessibilityService

    origin = (47.5505, 7.5745)
    walk = WalkingAccessibilityService(streets, entity_graph, service_index)
    on_foot = walk.calculate(*origin, minutes=15)
    with_transit = multimodal.calculate(*origin, minutes=15, departure_time="2026-08-20T10:00")
    assert (sum(r["count"] for r in with_transit["reachable_services"].values())
            > sum(r["count"] for r in on_foot["reachable_services"].values()))


def test_the_journey_parts_add_up(multimodal):
    result = multimodal.calculate(47.5505, 7.5745, minutes=25,
                                  departure_time="2026-08-20T10:00")
    journeys = [item["journey"] for row in result["reachable_services"].values()
                for item in row["items"] if item["journey"] and item["journey"]["uses_transit"]]
    assert journeys, "the fixture timetable should unlock something"
    for journey in journeys:
        total = journey["walking_minutes"] + journey["waiting_minutes"] + journey["transit_minutes"]
        assert total == pytest.approx(journey["total_minutes"], abs=0.2)
        assert journey["waiting_minutes"] >= 0


def test_a_walkable_destination_needs_no_vehicle(multimodal):
    result = multimodal.calculate(47.5505, 7.5745, minutes=25,
                                  departure_time="2026-08-20T10:00")
    walked = [item for row in result["reachable_services"].values() for item in row["items"]
              if item["journey"] and not item["journey"]["uses_transit"]]
    assert walked
    assert all(item["journey"]["transfers"] == 0 for item in walked)


def test_leaving_later_changes_the_answer(multimodal):
    early = multimodal.calculate(47.5505, 7.5745, minutes=15,
                                 departure_time="2026-08-20T10:00")
    night = multimodal.calculate(47.5505, 7.5745, minutes=15,
                                 departure_time="2026-08-20T23:00")
    assert (sum(r["count"] for r in early["reachable_services"].values())
            > sum(r["count"] for r in night["reachable_services"].values()))
    assert night["transit"]["stops_reached_by_vehicle"] == 0


def test_completeness_is_reported_for_transit(multimodal):
    result = multimodal.calculate(47.5505, 7.5745, minutes=25,
                                  departure_time="2026-08-20T10:00")
    assert result["completeness"]["label"] == "Prototype accessibility completeness"
    assert result["completeness"]["total"] == 6


def test_transit_provenance_records_every_assumption(multimodal):
    result = multimodal.calculate(47.5505, 7.5745, minutes=15,
                                  departure_time="2026-08-20T10:00")
    p = result["provenance"]
    assert p["travel_mode"] == "transit"
    assert p["routing_method"] == "walk + wait + ride + transfer + walk"
    assert p["timezone"] == "Europe/Zurich"
    assert p["service_date"] == "2026-08-20"
    assert p["max_transfers"] == 1
    assert p["walking_speed_kmh"] == 4.8
    assert p["transit"]["source"] == "synthetic fixture"
    assert p["network_source"]


def test_the_map_gets_walking_reach_rides_and_stops(multimodal):
    result = multimodal.calculate(47.5505, 7.5745, minutes=25,
                                  departure_time="2026-08-20T10:00")
    kinds = {f["properties"]["kind"] for f in result["geometry"]["features"]}
    assert "reachable_edge" in kinds
    assert "transit_segment" in kinds
    assert "transit_stop" in kinds


# --- one itinerary ------------------------------------------------------------
def test_route_to_a_transit_only_service(multimodal):
    result = multimodal.route_to_service(47.5505, 7.5745, "service:grocery:fixture:2",
                                         minutes=40, departure_time="2026-08-20T10:00")
    journey = result["journey"]
    assert journey["uses_transit"] is True
    assert journey["routes"] == ["Tram 8"]
    assert journey["boarding_stop"]["name"] == "Fixture A"
    kinds = [step["kind"] for step in journey["steps"]]
    assert kinds == ["walk", "board", "wait", "ride", "exit", "walk"]
    assert [f["properties"]["kind"] for f in result["geometry"]["features"]]


def test_a_service_out_of_reach_is_refused(multimodal):
    with pytest.raises(UnroutableServiceError):
        multimodal.route_to_service(47.5505, 7.5745, "service:grocery:fixture:2",
                                    minutes=3, departure_time="2026-08-20T10:00")


# --- caching ------------------------------------------------------------------
def test_the_timetable_round_trips_through_its_cache(tmp_path, timetable):
    path = write_cache(timetable, tmp_path / "transit.npz")
    reloaded = read_cache(path)
    assert reloaded.stop_count == timetable.stop_count
    assert reloaded.trip_count == timetable.trip_count
    assert reloaded.pattern_count == timetable.pattern_count
    assert reloaded.serves(THURSDAY) == timetable.serves(THURSDAY)
    assert [r.label for r in reloaded.routes] == [r.label for r in timetable.routes]
    original = timetable.day_view(THURSDAY).active_trip_count
    assert reloaded.day_view(THURSDAY).active_trip_count == original


def test_reading_a_missing_timetable_raises(tmp_path):
    with pytest.raises(TransitSourceError):
        read_cache(tmp_path / "nope.npz")


def test_reading_a_corrupt_timetable_raises(tmp_path):
    path = tmp_path / "broken.npz"
    path.write_bytes(b"not an npz")
    with pytest.raises(TransitSourceError):
        read_cache(path)


def test_a_missing_cache_degrades_to_the_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEL_TRANSIT_SOURCE", "auto")
    index = load_transit(path=tmp_path / "missing.npz")
    assert index.mode == "fixture"
    assert "prepare_data" in index.fallback_reason
    assert index.available


def test_pinning_the_gtfs_source_refuses_to_pretend(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEL_TRANSIT_SOURCE", "gtfs")
    with pytest.raises(TransitSourceError):
        load_transit(path=tmp_path / "missing.npz")


def test_transit_is_reported_unavailable_rather_than_crashing(streets, service_index):
    empty = TransitIndex(None, mode="fixture", fallback_reason="nothing prepared")
    service = MultimodalAccessibilityService(streets, empty, None, service_index)
    assert service.available is False
    with pytest.raises(TransitUnavailableError):
        service.calculate(47.5505, 7.5745, minutes=15)


# --- API ----------------------------------------------------------------------
def test_transit_endpoint_schema(client):
    body = client.get("/accessibility/transit",
                      params={"lat": 47.5505, "lon": 7.5745, "minutes": 25,
                              "departure_time": "2026-08-20T10:00"}).json()
    for key in ("origin", "snapped_origin", "minutes", "departure_time", "service_date",
                "max_transfers", "network", "transit", "reachable_services", "completeness",
                "geometry", "provenance"):
        assert key in body
    assert body["mode"] == "transit"
    assert body["transit"]["stops_in_walking_range"] >= 1


def test_transit_route_endpoint(client):
    body = client.get("/accessibility/transit/route",
                      params={"lat": 47.5505, "lon": 7.5745,
                              "service_id": "service:grocery:fixture:2",
                              "minutes": 40, "departure_time": "2026-08-20T10:00"}).json()
    assert body["journey"]["uses_transit"] is True
    assert body["geometry"]["type"] == "FeatureCollection"


def test_transit_status_endpoint(client):
    body = client.get("/transit/status").json()
    assert body["available"] is True
    assert body["mode"] == "fixture"
    assert body["stops"] == 4
    assert body["routes"] == 4
    assert body["service_dates"]["first"] == 20260101


def test_transit_stops_and_routes_endpoints(client):
    stops = client.get("/transit/stops").json()
    assert stops["type"] == "FeatureCollection"
    assert stops["total"] == 4
    routes = client.get("/transit/routes").json()
    assert routes["total"] == 4
    assert "Tram" in routes["by_vehicle"]


def test_comparison_endpoint(client):
    body = client.get("/accessibility/compare",
                      params={"lat": 47.5505, "lon": 7.5745, "minutes": 25,
                              "departure_time": "2026-08-20T10:00"}).json()
    assert set(body["modes"]) == {"walk", "bike", "transit"}
    for row in body["modes"].values():
        assert "reachable_services" in row and "completeness" in row
        assert "geometry" not in row
    assert body["table"]["grocery"]["transit"] >= body["table"]["grocery"]["walk"]
    assert body["table"]["grocery"]["bike"] >= body["table"]["grocery"]["walk"]


def test_comparison_can_be_narrowed(client):
    body = client.get("/accessibility/compare",
                      params={"lat": 47.5505, "lon": 7.5745, "minutes": 15,
                              "modes": "walk,bike"}).json()
    assert set(body["modes"]) == {"walk", "bike"}


def test_health_reports_the_timetable(client):
    body = client.get("/health").json()
    assert body["transit"]["available"] is True
    assert body["transit"]["mode"] == "fixture"
    assert body["transit"]["stops"] == 4
