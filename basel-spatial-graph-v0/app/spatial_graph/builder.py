"""Build the heterogeneous graph from artefacts the project has already prepared.

Nothing here fetches anything. It reads the caches the reference application
writes — the entity datasets, the service catalogue with its per-network access
points, the timetable, the population figures — and turns them into one typed
graph whose edges are *structural facts*, not answers.

The routing graphs stay exactly where they are. This graph references them
(through `StreetAccessPoint` nodes and the representative origins it computes)
rather than copying them, because a shortest path is a job for a routing graph
and a cross-domain question is a job for this one.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

from ..service_model import ESSENTIAL_CATEGORIES, ServiceCategory, category_label
from .model import NetworkXSpatialGraph

# How a neighbourhood's accessibility origin is chosen. Stated in every result
# that depends on it, because a naive centroid can land in a river.
ORIGIN_METHOD = (
    "The polygon's representative point (guaranteed inside the neighbourhood), "
    "moved to the nearest pedestrian-network node that also lies inside the "
    "neighbourhood. Not population-weighted: the population data is only "
    "available per neighbourhood, so there is nothing finer to weight by."
)


def _clean(value):
    """numpy scalars and shapely floats do not belong in a JSON artefact."""
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    return value


class SpatialGraphBuilder:
    """Assembles the typed graph. One `build()` call, one artefact."""

    def __init__(self, entities: Optional[dict] = None, services=None,
                 transit=None, population: Optional[dict] = None,
                 networks: Optional[dict] = None, progress=None, data_quality=None):
        self.entities = entities or {}
        self.services = services
        self.transit = transit
        self.population = population or {}
        self.networks = networks or {}
        self.progress = progress or (lambda message: None)
        self.data_quality = data_quality or {"available": False}
        self.graph = NetworkXSpatialGraph()
        self._areas: List[dict] = []
        self._shapes: List = []
        self._tree: Optional[STRtree] = None
        self.warnings: List[str] = []

    # -- entry point ----------------------------------------------------------
    def build(self) -> NetworkXSpatialGraph:
        self._add_neighborhoods()
        self._add_population()
        self._add_categories()
        self._add_services()
        self._add_transit()
        self._add_representative_origins()
        self.graph.metadata = self._metadata()
        self.progress(f"{self.graph.graph.number_of_nodes():,} nodes, "
                      f"{self.graph.graph.number_of_edges():,} edges")
        return self.graph

    # -- neighbourhoods -------------------------------------------------------
    def _add_neighborhoods(self) -> None:
        from ..projection import project_geometry

        areas = self.entities.get("areas", [])
        for area in areas:
            try:
                polygon = shape(area["geometry"])
            except Exception:
                self.warnings.append(f"neighbourhood {area.get('id')} has unusable geometry")
                continue
            wov_id = str(area.get("properties", {}).get("wov_id")
                         or area["id"].split(":", 1)[-1])
            self._areas.append({"id": area["id"], "name": area.get("name"), "wov_id": wov_id})
            self._shapes.append(polygon)
            self.graph.add_node(
                area["id"], "Neighborhood",
                name=area.get("name"),
                wov_id=wov_id,
                area_km2=round(project_geometry(polygon).area / 1e6, 4),
                geometry=area["geometry"],
                provenance=area.get("provenance"),
            )
        if self._shapes:
            self._tree = STRtree(self._shapes)
        self.progress(f"{len(self._areas)} neighbourhoods")
        self._add_adjacency()

    def _add_adjacency(self) -> None:
        """Polygons that share a boundary. Symmetric, so both directions exist."""
        pairs = 0
        for i, first in enumerate(self._shapes):
            for j in range(i + 1, len(self._shapes)):
                second = self._shapes[j]
                if not first.touches(second) and not first.intersects(second.boundary):
                    continue
                a, b = self._areas[i]["id"], self._areas[j]["id"]
                shared = first.boundary.intersection(second.boundary)
                length = round(getattr(shared, "length", 0.0), 6)
                for source, target in ((a, b), (b, a)):
                    self.graph.add_edge(source, target, "ADJACENT_TO",
                                        derived=True, method="polygon boundary contact",
                                        shared_boundary_degrees=length)
                pairs += 1
        self.progress(f"{pairs} adjacent neighbourhood pairs")

    # -- population -----------------------------------------------------------
    def _add_population(self) -> None:
        """Observations as nodes; the latest year also denormalized onto the area.

        Both representations were considered. Nodes keep the year dimension
        honest — you can ask for 2019 without the graph pretending it is now.
        Denormalizing only the latest year onto `Neighborhood` keeps the common
        case ("how many children live here?") a plain field filter, which is
        what makes the query language small.
        """
        observations = self.population.get("observations", [])
        if not observations:
            self.warnings.append("no population data was available")
            return
        by_wov = {area["wov_id"]: area["id"] for area in self._areas}
        latest = self.population.get("latest_year") or max(o["year"] for o in observations)
        provenance = self.population.get("provenance", {})
        added = 0
        for row in observations:
            area_id = by_wov.get(str(row["wov_id"]))
            if area_id is None:
                continue
            node_id = f"population:{row['wov_id']}:{row['year']}"
            self.graph.add_node(
                node_id, "PopulationObservation",
                neighborhood_id=area_id, year=int(row["year"]),
                **{key: int(row.get(key, 0)) for key in
                   ("total", "children", "young", "working_age", "elderly", "elderly_80_plus")},
                provenance=provenance,
            )
            self.graph.add_edge(area_id, node_id, "HAS_POPULATION_OBSERVATION", derived=False)
            self.graph.add_edge(node_id, area_id, "OBSERVES", derived=False)
            added += 1
            if int(row["year"]) == int(latest):
                node = self.graph.graph.nodes[area_id]
                total = int(row.get("total", 0)) or 0
                area_km2 = node.get("area_km2") or 0
                node.update(
                    population_total=total,
                    children=int(row.get("children", 0)),
                    young=int(row.get("young", 0)),
                    working_age=int(row.get("working_age", 0)),
                    elderly=int(row.get("elderly", 0)),
                    elderly_80_plus=int(row.get("elderly_80_plus", 0)),
                    child_share=round(int(row.get("children", 0)) / total, 4) if total else None,
                    elderly_share=round(int(row.get("elderly", 0)) / total, 4) if total else None,
                    population_density_km2=round(total / area_km2, 1) if area_km2 else None,
                    reference_year=int(latest),
                )
        missing = [a["name"] for a in self._areas
                   if self.graph.graph.nodes[a["id"]].get("population_total") is None]
        if missing:
            self.warnings.append(
                f"{len(missing)} neighbourhood(s) have no population figures: {', '.join(missing[:4])}")
        self.progress(f"{added} population observations "
                      f"({len(self.population.get('years', []))} years, latest {latest})")

    # -- services -------------------------------------------------------------
    def _add_categories(self) -> None:
        if self.services is None:
            return
        for category in ServiceCategory:
            count = len(self.services.by_category.get(category, []))
            self.graph.add_node(
                f"category:{category.value}", "ServiceCategory",
                category=category.value,
                label=category_label(category),
                essential=category in ESSENTIAL_CATEGORIES,
                count=count,
            )

    def _add_services(self) -> None:
        if self.services is None:
            self.warnings.append("no service catalogue was available")
            return
        located = 0
        for service in self.services.services:
            area_id = self._containing_area(service.lon, service.lat)
            self.graph.add_node(
                service.id, "ServiceLocation",
                name=service.name,
                display_name=service.display_name,
                category=service.category.value,
                lat=round(service.lat, 6), lon=round(service.lon, 6),
                neighborhood_id=area_id,
                source=service.source,
                routable_walk=service.is_routable_on("walk"),
                routable_bike=service.is_routable_on("bike"),
                geometry=service.geometry,
                provenance=service.provenance,
            )
            self.graph.add_edge(service.id, f"category:{service.category.value}",
                                "OF_CATEGORY", derived=False)
            self.graph.add_edge(f"category:{service.category.value}", service.id,
                                "HAS_MEMBER", derived=False)
            if area_id:
                located += 1
                self.graph.add_edge(service.id, area_id, "LOCATED_IN",
                                    derived=True, method="point-in-polygon")
                self.graph.add_edge(area_id, service.id, "HAS_SERVICE",
                                    derived=True, method="point-in-polygon")
            for network in ("walk", "bike"):
                access = service.access_for(network)
                if access.is_routable:
                    self._attach(service.id, network, access.node_id, access.distance_m)
        self.progress(f"{len(self.services.services):,} services "
                      f"({located:,} inside a neighbourhood)")

    # -- transit --------------------------------------------------------------
    def _add_transit(self) -> None:
        if self.transit is None or not getattr(self.transit, "available", False):
            self.warnings.append("no timetable was available")
            return
        table = self.transit.timetable
        routes_by_stop: Dict[int, set] = {}
        for pattern in range(table.pattern_count):
            route_index = int(table.pattern_route[pattern])
            for stop in table.pattern_stop_slice(pattern):
                routes_by_stop.setdefault(int(stop), set()).add(route_index)

        used_routes = set()
        stops_added = 0
        for index in range(table.stop_count):
            access = self.transit.stop_access[index] if index < len(self.transit.stop_access) else None
            lon, lat = float(table.stop_lon[index]), float(table.stop_lat[index])
            area_id = self._containing_area(lon, lat)
            # A stop matters here if it is in Basel or reachable on foot from it.
            if area_id is None and not (access and access.is_routable):
                continue
            routes = sorted(routes_by_stop.get(index, ()))
            node_id = f"stop:{table.stop_ids[index]}"
            vehicles = sorted({self.transit.route_summary_for_index(r)["vehicle"]
                               for r in routes}) if routes else []
            self.graph.add_node(
                node_id, "TransitStop",
                stop_id=table.stop_ids[index],
                name=table.stop_names[index],
                lat=round(lat, 6), lon=round(lon, 6),
                neighborhood_id=area_id,
                route_count=len(routes),
                vehicles=vehicles,
                walk_access=bool(access and access.is_routable),
                geometry={"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                provenance=self.transit.provenance,
            )
            stops_added += 1
            if area_id:
                self.graph.add_edge(node_id, area_id, "LOCATED_IN",
                                    derived=True, method="point-in-polygon")
                self.graph.add_edge(area_id, node_id, "HAS_TRANSIT_STOP",
                                    derived=True, method="point-in-polygon")
            if access and access.is_routable:
                self._attach(node_id, "walk", access.node_id, access.distance_m)
            for route_index in routes:
                route_id = self._add_route(route_index)
                used_routes.add(route_index)
                self.graph.add_edge(node_id, route_id, "SERVED_BY", derived=False)
                self.graph.add_edge(route_id, node_id, "SERVES", derived=False)
        for route_index in used_routes:
            route_id = self._add_route(route_index)
            node = self.graph.graph.nodes[route_id]
            node["stop_count"] = len(self.graph.neighbors(route_id, relation="SERVES"))
        self.progress(f"{stops_added:,} transit stops on {len(used_routes)} routes")

    def _add_route(self, route_index: int) -> str:
        summary = self.transit.route_summary_for_index(route_index)
        node_id = f"route:{summary['id']}"
        if node_id not in self.graph.graph:
            self.graph.add_node(
                node_id, "TransitRoute",
                route_id=summary["id"], short_name=summary["short_name"],
                label=summary["label"], vehicle=summary["vehicle"],
                agency=summary["agency"], stop_count=0,
                provenance=self.transit.provenance,
            )
        return node_id

    # -- shared ---------------------------------------------------------------
    def _attach(self, entity_id: str, network: str, node_id: str, distance_m) -> None:
        access_id = f"access:{network}:{node_id}"
        if access_id not in self.graph.graph:
            streets = self.networks.get(network)
            data = streets.graph.nodes.get(node_id, {}) if streets else {}
            self.graph.add_node(
                access_id, "StreetAccessPoint",
                node_id=node_id, network=network,
                lat=round(float(data.get("lat", 0.0)), 6),
                lon=round(float(data.get("lon", 0.0)), 6),
                attached_count=0,
            )
        self.graph.graph.nodes[access_id]["attached_count"] += 1
        self.graph.add_edge(entity_id, access_id, "ACCESS_POINT",
                            network=network, distance_m=round(float(distance_m or 0), 1),
                            derived=True, method="nearest network node")
        self.graph.add_edge(access_id, entity_id, "ATTACHES", network=network, derived=True)

    def _containing_area(self, lon: float, lat: float) -> Optional[str]:
        if self._tree is None:
            return None
        point = Point(lon, lat)
        for index in self._tree.query(point):
            index = int(index)
            if self._shapes[index].contains(point):
                return self._areas[index]["id"]
        return None

    def _add_representative_origins(self) -> None:
        """Give every neighbourhood a defensible origin for accessibility work."""
        streets = self.networks.get("walk")
        for area, polygon in zip(self._areas, self._shapes):
            point = polygon.representative_point()
            lon, lat = float(point.x), float(point.y)
            method = "polygon representative point"
            if streets is not None:
                best = self._nearest_inside_node(streets, polygon, lon, lat)
                if best is not None:
                    lon, lat = best
                    method = ORIGIN_METHOD
            node = self.graph.graph.nodes[area["id"]]
            node["representative_lon"] = round(lon, 6)
            node["representative_lat"] = round(lat, 6)
            node["origin_method"] = method

    @staticmethod
    def _nearest_inside_node(streets, polygon, lon, lat):
        """The pedestrian node inside the polygon nearest its representative point."""
        minx, miny, maxx, maxy = polygon.bounds
        best, best_distance = None, float("inf")
        for node_id in streets.graph.nodes:
            data = streets.graph.nodes[node_id]
            nlon, nlat = data["lon"], data["lat"]
            if not (minx <= nlon <= maxx and miny <= nlat <= maxy):
                continue
            distance = (nlon - lon) ** 2 + (nlat - lat) ** 2
            if distance >= best_distance:
                continue
            if not polygon.contains(Point(nlon, nlat)):
                continue
            best, best_distance = (nlon, nlat), distance
        return best

    # -- metadata -------------------------------------------------------------
    def _metadata(self) -> dict:
        sources = {
            "neighborhoods": (self.entities.get("areas") or [{}])[0].get("provenance", {})
            if self.entities.get("areas") else {},
            "population": self.population.get("provenance", {}),
            "services": {
                "mode": getattr(self.services, "mode", None),
                "generated_at": getattr(self.services, "generated_at", None),
                "sources": sorted({s.source for s in self.services.services})
                if self.services else [],
            },
            "transit": self.transit.provenance if self.transit is not None else {},
            "networks": {
                name: {"mode": streets.mode, "source": streets.source_name,
                       "nodes": streets.graph.number_of_nodes()}
                for name, streets in self.networks.items()
            },
        }
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mode": "fixture" if self._is_fixture() else "live",
            "population_reference_year": self.population.get("latest_year"),
            "population_years": self.population.get("years", []),
            "origin_method": ORIGIN_METHOD,
            "warnings": list(self.warnings),
            "sources": sources,
            "data_quality": self.data_quality,
        }

    def _is_fixture(self) -> bool:
        modes = [self.population.get("mode"), getattr(self.services, "mode", None),
                 getattr(self.transit, "mode", None)]
        modes += [streets.mode for streets in self.networks.values()]
        return any(mode == "fixture" for mode in modes if mode)


def build_spatial_graph(entities=None, services=None, transit=None, population=None,
                        networks=None, progress=None, data_quality=None) -> NetworkXSpatialGraph:
    return SpatialGraphBuilder(entities, services, transit, population, networks,
                               progress, data_quality).build()
