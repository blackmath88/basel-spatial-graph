"""Test-wide isolation.

`app.main` builds its graphs at import time, so fixture mode must be set before
anything imports it. No test in this suite may touch the network.
"""
import os

os.environ["BASEL_GRAPH_FIXTURE"] = "1"
os.environ["BASEL_STREET_NETWORK_SOURCE"] = "fixture"
os.environ["BASEL_SERVICE_SOURCE"] = "fixture"
os.environ["BASEL_TRANSIT_SOURCE"] = "fixture"

import socket  # noqa: E402

import pytest  # noqa: E402

from app.fixtures import fixture_records  # noqa: E402
from app.graph import build_graph  # noqa: E402
from app.service_index import ServiceIndex, snap_services  # noqa: E402
from app.service_sources import fixture_services  # noqa: E402
from app.street_sources import fixture_street_network  # noqa: E402
from app.transit_index import TransitIndex  # noqa: E402
from app.transit_sources import fixture_timetable  # noqa: E402


@pytest.fixture
def streets():
    return fixture_street_network()


@pytest.fixture
def entity_graph():
    return build_graph(fixture_records())


@pytest.fixture
def bike_network():
    return fixture_street_network(kind="bike")


@pytest.fixture
def service_index(streets, bike_network):
    """The synthetic services, snapped to both synthetic grids."""
    services = fixture_services()
    snap_services(streets, services, network="walk")
    snap_services(bike_network, services, network="bike")
    return ServiceIndex(services, mode="fixture", networks=("walk", "bike"))


@pytest.fixture
def transit_index(streets):
    """The synthetic timetable, attached to the synthetic walking grid."""
    return TransitIndex(fixture_timetable(), mode="fixture").attach_to_network(streets)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Hard guarantee: the suite never reaches OpenStreetMap or data.bs.ch."""
    def blocked(*args, **kwargs):
        raise AssertionError("Tests must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
