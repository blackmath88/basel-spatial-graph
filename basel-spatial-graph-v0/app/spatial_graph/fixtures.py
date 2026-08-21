"""A fully synthetic spatial graph, for tests and offline exploration.

Built from the same fixture sources the rest of the project uses — the 7x5
street grids, the twelve synthetic services, the four-stop timetable and a
hand-written population table — so a test can traverse and query real
structure without any prepared artefact or network access.
"""
from __future__ import annotations

from ..accessibility import CyclingAccessibilityService, WalkingAccessibilityService
from ..fixtures import fixture_records
from ..graph import build_graph
from ..modes import TravelMode
from ..multimodal import MultimodalAccessibilityService
from ..population import fixture_population
from ..service_index import ServiceIndex, snap_services
from ..service_sources import fixture_services
from ..street_sources import fixture_street_network
from ..transit_index import TransitIndex
from ..transit_sources import fixture_timetable
from ..data_quality import build_report, compact_snapshot
from .builder import build_spatial_graph


def fixture_graph():
    """The synthetic heterogeneous graph, with no engines attached."""
    networks = {"walk": fixture_street_network(), "bike": fixture_street_network(kind="bike")}
    services = fixture_services()
    for name, streets in networks.items():
        snap_services(streets, services, network=name)
    index = ServiceIndex(services, mode="fixture", networks=("walk", "bike"))
    transit = TransitIndex(fixture_timetable(), mode="fixture").attach_to_network(networks["walk"])
    quality = compact_snapshot(build_report(networks, fixture_records(), index, transit))
    graph = build_spatial_graph(fixture_records(), index, transit, fixture_population(), networks,
                                data_quality=quality)
    return graph, networks, index, transit


def fixture_service(with_engines: bool = True):
    """A `SpatialGraphService` over the synthetic graph, engines included."""
    from . import SpatialGraphService

    graph, networks, index, transit = fixture_graph()
    engines = {}
    if with_engines:
        entity_graph = build_graph(fixture_records())
        engines[TravelMode.WALK] = WalkingAccessibilityService(networks["walk"], entity_graph, index)
        engines[TravelMode.BIKE] = CyclingAccessibilityService(networks["bike"], entity_graph, index)
        multimodal = MultimodalAccessibilityService(networks["walk"], transit, entity_graph, index)
        if multimodal.available:
            engines[TravelMode.TRANSIT] = multimodal
    return SpatialGraphService(graph, engines)
