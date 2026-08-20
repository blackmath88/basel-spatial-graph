"""OpenStreetMap walking network via OSMnx.

Downloading only ever happens in `python -m app.prepare_data`. At runtime the
source reads the prepared GraphML cache, so starting the API never touches the
network and never rebuilds the graph.
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

import networkx as nx
from shapely import wkt
from shapely.geometry import LineString
from shapely.errors import ShapelyError

from ..config import (
    BASEL_BBOX,
    BASEL_PLACE_QUERIES,
    OSMNX_CACHE_DIR,
    OSMNX_NETWORK_TYPE,
    WALK_NETWORK_CACHE,
)
from ..errors import NetworkSourceError
from .base import LIVE, StreetNetwork, WalkingNetworkSource, make_provenance, utc_now_iso
from .graphml_cache import read_cache, write_cache

OSM_ATTRIBUTION = "© OpenStreetMap contributors"


def _first(value):
    """OSM tags are sometimes lists after OSMnx simplification."""
    if isinstance(value, (list, tuple)):
        return _first(value[0]) if value else None
    return value


def _as_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class OSMnxWalkingNetworkSource(WalkingNetworkSource):
    name = "OpenStreetMap / OSMnx"

    def __init__(
        self,
        cache_path: Path = WALK_NETWORK_CACHE,
        place_queries: Sequence[str] = BASEL_PLACE_QUERIES,
        bbox=BASEL_BBOX,
        network_type: str = OSMNX_NETWORK_TYPE,
        allow_download: bool = False,
        refresh: bool = False,
    ):
        self.cache_path = Path(cache_path)
        self.place_queries = tuple(place_queries)
        self.bbox = bbox
        self.network_type = network_type
        self.allow_download = allow_download
        self.refresh = refresh
        self.used_cache = False

    # -- public ---------------------------------------------------------------
    def load(self) -> StreetNetwork:
        if not self.refresh and self.cache_path.exists():
            network = read_cache(self.cache_path)
            self.used_cache = True
            return network
        if not self.allow_download:
            raise NetworkSourceError(
                f"No prepared walking network at {self.cache_path}. "
                "Run `python -m app.prepare_data` once to download and cache it."
            )
        network = self.download()
        write_cache(network, self.cache_path)
        network.provenance["cache_path"] = str(self.cache_path)
        return network

    def download(self) -> StreetNetwork:
        ox = self._import_osmnx()
        OSMNX_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        ox.settings.use_cache = True
        ox.settings.cache_folder = str(OSMNX_CACHE_DIR)
        ox.settings.log_console = False

        errors = []
        for place in self.place_queries:
            try:
                graph = ox.graph_from_place(place, network_type=self.network_type, simplify=True)
                return self._convert(graph, place=place, ox_version=ox.__version__)
            except Exception as exc:
                errors.append(f"place '{place}': {exc}")
        try:
            south, west, north, east = self.bbox
            graph = ox.graph_from_bbox(
                bbox=(west, south, east, north),
                network_type=self.network_type,
                simplify=True,
            )
            return self._convert(
                graph, place=f"bbox {self.bbox}", ox_version=ox.__version__
            )
        except Exception as exc:
            errors.append(f"bbox {self.bbox}: {exc}")
        raise NetworkSourceError(
            "Could not download a Basel walking network from OpenStreetMap.",
            attempts=errors,
        )

    # -- internals ------------------------------------------------------------
    @staticmethod
    def _import_osmnx():
        try:
            import osmnx as ox  # noqa: WPS433 — optional, prepare-time only dependency
        except ImportError as exc:
            raise NetworkSourceError(
                "OSMnx is not installed. Install it with `pip install -r requirements.txt` "
                "to prepare a live walking network.",
                import_error=str(exc),
            )
        return ox

    def _convert(self, source_graph, place: str, ox_version: str) -> StreetNetwork:
        """MultiDiGraph from OSMnx -> undirected, metre-weighted StreetNetwork.

        The pedestrian network is walkable in both directions, so parallel and
        reversed OSM edges collapse to the shortest connection per node pair.
        """
        graph = nx.Graph()
        for node, data in source_graph.nodes(data=True):
            lon, lat = _as_float(data.get("x")), _as_float(data.get("y"))
            if lon is None or lat is None:
                continue
            graph.add_node(str(node), lon=lon, lat=lat, osmid=str(node), type="StreetNode")

        for u, v, data in source_graph.edges(data=True):
            u, v = str(u), str(v)
            if u == v or u not in graph or v not in graph:
                continue  # self loops add no reachability
            length = _as_float(data.get("length"))
            geom = data.get("geometry")
            if isinstance(geom, str):
                try:
                    geom = wkt.loads(geom)
                except (ShapelyError, ValueError):
                    geom = None
            if geom is None:
                geom = LineString([
                    (graph.nodes[u]["lon"], graph.nodes[u]["lat"]),
                    (graph.nodes[v]["lon"], graph.nodes[v]["lat"]),
                ])
            existing = graph.get_edge_data(u, v)
            if existing is not None and length is not None:
                if _as_float(existing.get("length_m"), float("inf")) <= length:
                    continue
            graph.add_edge(
                u, v,
                length_m=length,
                geom=geom,
                highway=_first(data.get("highway")),
                name=_first(data.get("name")),
                osmid=str(_first(data.get("osmid")) or ""),
                type="WALKABLE_TO",
            )

        if graph.number_of_edges() == 0:
            raise NetworkSourceError(f"OpenStreetMap returned no walkable edges for {place}")

        provenance = make_provenance(
            mode=LIVE,
            source=self.name,
            dataset=f"OSM {self.network_type} network",
            source_url="https://www.openstreetmap.org/copyright",
            license="ODbL 1.0",
            retrieved_at=utc_now_iso(),
            place=place,
            network_type=self.network_type,
            osmnx_version=ox_version,
            attribution=OSM_ATTRIBUTION,
        )
        return StreetNetwork(graph, provenance)
