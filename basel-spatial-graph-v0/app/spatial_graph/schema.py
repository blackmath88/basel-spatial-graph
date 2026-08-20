"""The heterogeneous graph schema: what exists, and what may point at what.

This is a small, explicit, machine-readable schema rather than a convention.
It exists so a client — a person, a script, or later an agent — can *discover*
the graph instead of being told about it in a prompt.

Two kinds of relation are described here and the difference matters:

* **structural** relations are facts about the city that do not depend on a
  question: a pharmacy is in a neighbourhood, a stop is served by a route.
  These are built once and persisted.
* **analytical** relations depend on a mode, a time budget and a departure
  time. They are never persisted — there would be millions of them and they
  would be wrong as soon as a parameter changed. They are computed on demand by
  the routing engines the reference application already has.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

STRUCTURAL = "structural"
ANALYTICAL = "analytical"


@dataclass(frozen=True)
class Field:
    name: str
    type: str                      # string | number | integer | boolean | geometry | list
    description: str
    unit: Optional[str] = None
    filterable: bool = True

    def describe(self) -> dict:
        return {"name": self.name, "type": self.type, "description": self.description,
                "unit": self.unit, "filterable": self.filterable}


@dataclass(frozen=True)
class NodeType:
    name: str
    description: str
    fields: Sequence[Field]
    id_prefix: str
    source: str

    def describe(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "id_prefix": self.id_prefix,
            "source": self.source,
            "fields": [f.describe() for f in self.fields],
        }

    @property
    def field_names(self) -> List[str]:
        return [f.name for f in self.fields]


@dataclass(frozen=True)
class RelationType:
    name: str
    description: str
    sources: Sequence[str]
    targets: Sequence[str]
    kind: str = STRUCTURAL
    # A relation may have more than one inverse when its sources differ:
    # LOCATED_IN is inverted by HAS_SERVICE for services and by
    # HAS_TRANSIT_STOP for stops.
    inverse: Optional[object] = None
    computed_by: Optional[str] = None

    @property
    def inverses(self) -> List[str]:
        if self.inverse is None:
            return []
        if isinstance(self.inverse, str):
            return [self.inverse]
        return list(self.inverse)

    def describe(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "from": list(self.sources),
            "to": list(self.targets),
            "kind": self.kind,
            "inverse": self.inverses,
            "computed_by": self.computed_by,
            "persisted": self.kind == STRUCTURAL,
        }


# --- node types ---------------------------------------------------------------
NODE_TYPES: Dict[str, NodeType] = {
    t.name: t for t in [
        NodeType(
            "Neighborhood",
            "A Basel-Stadt Wohnviertel (statistical neighbourhood), including "
            "Riehen and Bettingen. Carries the most recent population figures "
            "as convenience fields; the full time series lives in "
            "PopulationObservation nodes.",
            id_prefix="area:",
            source="data.bs.ch 100042 (Statistische Raumeinheiten: Wohnviertel)",
            fields=[
                Field("id", "string", "Stable entity id"),
                Field("name", "string", "Neighbourhood name, e.g. Gundeldingen"),
                Field("wov_id", "string", "Official Wohnviertel id"),
                Field("area_km2", "number", "Polygon area", unit="km2"),
                Field("population_total", "integer", "Residents in the reference year"),
                Field("children", "integer", "Residents aged 0-17 in the reference year"),
                Field("young", "integer", "Residents aged 0-19 (the cantonal youth definition)"),
                Field("working_age", "integer", "Residents aged 20-64"),
                Field("elderly", "integer", "Residents aged 65 and over"),
                Field("elderly_80_plus", "integer", "Residents aged 80 and over"),
                Field("child_share", "number", "children / population_total"),
                Field("elderly_share", "number", "elderly / population_total"),
                Field("population_density_km2", "number", "Residents per km2", unit="1/km2"),
                Field("reference_year", "integer", "Year the population figures describe"),
                Field("representative_lat", "number", "Origin used for accessibility analysis"),
                Field("representative_lon", "number", "Origin used for accessibility analysis"),
                Field("origin_method", "string", "How that origin was chosen", filterable=False),
                Field("geometry", "geometry", "Polygon, omitted unless requested", filterable=False),
            ],
        ),
        NodeType(
            "PopulationObservation",
            "One neighbourhood's population broken down by age group for one "
            "year. Separate nodes keep the year dimension explicit instead of "
            "silently overwriting it.",
            id_prefix="population:",
            source="data.bs.ch 100128 (Wohnbevölkerung nach Geschlecht, Alter, "
                   "Staatsangehörigkeit und Wohnviertel)",
            fields=[
                Field("id", "string", "Stable entity id"),
                Field("neighborhood_id", "string", "The neighbourhood observed"),
                Field("year", "integer", "Reference year (31 December)"),
                Field("total", "integer", "All residents"),
                Field("children", "integer", "Aged 0-17"),
                Field("young", "integer", "Aged 0-19"),
                Field("working_age", "integer", "Aged 20-64"),
                Field("elderly", "integer", "Aged 65+"),
                Field("elderly_80_plus", "integer", "Aged 80+"),
            ],
        ),
        NodeType(
            "ServiceCategory",
            "One of the eight everyday destination categories.",
            id_prefix="category:",
            source="app/service_model.py (ServiceCategory enum)",
            fields=[
                Field("id", "string", "Stable entity id"),
                Field("category", "string", "Canonical category id, e.g. pharmacy"),
                Field("label", "string", "Display label, e.g. Pharmacies"),
                Field("essential", "boolean", "Counts towards the completeness indicator"),
                Field("count", "integer", "Prepared locations in this category"),
            ],
        ),
        NodeType(
            "ServiceLocation",
            "One everyday destination: a shop, pharmacy, practice, school, "
            "park, sports facility, library or culture venue.",
            id_prefix="service:",
            source="data.bs.ch and OpenStreetMap, via app/service_sources",
            fields=[
                Field("id", "string", "Stable entity id"),
                Field("name", "string", "Upstream name; null when the source has none"),
                Field("display_name", "string", "Name, or a labelled fallback"),
                Field("category", "string", "Canonical category id"),
                Field("lat", "number", "WGS84 latitude"),
                Field("lon", "number", "WGS84 longitude"),
                Field("neighborhood_id", "string", "Containing neighbourhood, if any"),
                Field("source", "string", "Provider name"),
                Field("routable_walk", "boolean", "Attached to the pedestrian network"),
                Field("routable_bike", "boolean", "Attached to the bicycle network"),
                Field("geometry", "geometry", "Point, omitted unless requested", filterable=False),
            ],
        ),
        NodeType(
            "StreetAccessPoint",
            "A street-network node that something attaches to. Only nodes that "
            "are actually used as an access point are in this graph — mirroring "
            "20,000 routing nodes here would help nobody.",
            id_prefix="access:",
            source="OpenStreetMap walking and cycling networks",
            fields=[
                Field("id", "string", "Stable entity id"),
                Field("node_id", "string", "Id in the routing network"),
                Field("network", "string", "walk or bike"),
                Field("lat", "number", "WGS84 latitude"),
                Field("lon", "number", "WGS84 longitude"),
                Field("attached_count", "integer", "Entities attached to this node"),
            ],
        ),
        NodeType(
            "TransitStop",
            "A public-transport station inside the prepared area, with its "
            "pedestrian attachment.",
            id_prefix="stop:",
            source="opentransportdata.swiss (Swiss national GTFS)",
            fields=[
                Field("id", "string", "Stable entity id"),
                Field("stop_id", "string", "GTFS stop id of the parent station"),
                Field("name", "string", "Stop name"),
                Field("lat", "number", "WGS84 latitude"),
                Field("lon", "number", "WGS84 longitude"),
                Field("neighborhood_id", "string", "Containing neighbourhood, if any"),
                Field("route_count", "integer", "Distinct routes calling here"),
                Field("vehicles", "list", "Vehicle types calling here, e.g. Tram, Bus"),
                Field("walk_access", "boolean", "Attached to the pedestrian network"),
                Field("geometry", "geometry", "Point, omitted unless requested", filterable=False),
            ],
        ),
        NodeType(
            "TransitRoute",
            "A public-transport line, e.g. Tram 8.",
            id_prefix="route:",
            source="opentransportdata.swiss (Swiss national GTFS)",
            fields=[
                Field("id", "string", "Stable entity id"),
                Field("route_id", "string", "GTFS route id"),
                Field("short_name", "string", "Line number, e.g. 8"),
                Field("label", "string", "How a passenger would say it, e.g. Tram 8"),
                Field("vehicle", "string", "Tram, Bus, S-Bahn, Train, …"),
                Field("agency", "string", "Operating agency"),
                Field("stop_count", "integer", "Stops in the prepared area"),
            ],
        ),
    ]
}

# --- relation types -----------------------------------------------------------
RELATION_TYPES: Dict[str, RelationType] = {
    r.name: r for r in [
        RelationType("LOCATED_IN", "Point entity lies inside a neighbourhood polygon.",
                     ["ServiceLocation", "TransitStop"], ["Neighborhood"],
                     inverse=("HAS_SERVICE", "HAS_TRANSIT_STOP")),
        RelationType("HAS_SERVICE", "Neighbourhood contains this service location.",
                     ["Neighborhood"], ["ServiceLocation"], inverse="LOCATED_IN"),
        RelationType("HAS_TRANSIT_STOP", "Neighbourhood contains this transit stop.",
                     ["Neighborhood"], ["TransitStop"], inverse="LOCATED_IN"),
        RelationType("HAS_POPULATION_OBSERVATION",
                     "Neighbourhood's population figures for one year.",
                     ["Neighborhood"], ["PopulationObservation"], inverse="OBSERVES"),
        RelationType("OBSERVES", "The neighbourhood these figures describe.",
                     ["PopulationObservation"], ["Neighborhood"],
                     inverse="HAS_POPULATION_OBSERVATION"),
        RelationType("ADJACENT_TO", "Neighbourhood polygons share a boundary.",
                     ["Neighborhood"], ["Neighborhood"], inverse="ADJACENT_TO"),
        RelationType("OF_CATEGORY", "Service belongs to this category.",
                     ["ServiceLocation"], ["ServiceCategory"], inverse="HAS_MEMBER"),
        RelationType("HAS_MEMBER", "Category contains this service.",
                     ["ServiceCategory"], ["ServiceLocation"], inverse="OF_CATEGORY"),
        RelationType("ACCESS_POINT", "Entity attaches to the street network here.",
                     ["ServiceLocation", "TransitStop"], ["StreetAccessPoint"],
                     inverse="ATTACHES"),
        RelationType("ATTACHES", "Entities attached at this street node.",
                     ["StreetAccessPoint"], ["ServiceLocation", "TransitStop"],
                     inverse="ACCESS_POINT"),
        RelationType("SERVED_BY", "A route calls at this stop.",
                     ["TransitStop"], ["TransitRoute"], inverse="SERVES"),
        RelationType("SERVES", "This route calls at that stop.",
                     ["TransitRoute"], ["TransitStop"], inverse="SERVED_BY"),
        # Never persisted — see the module docstring.
        RelationType("REACHABLE_WITHIN",
                     "Destination reachable from an origin within a time budget. "
                     "Depends on mode, minutes and (for transit) departure time, so it is "
                     "computed on demand, never stored.",
                     ["Neighborhood"], ["ServiceLocation"], kind=ANALYTICAL,
                     computed_by="app.accessibility / app.multimodal"),
    ]
}

FILTER_OPS = {
    "eq": "equals", "ne": "not equal", "gt": "greater than", "gte": "greater or equal",
    "lt": "less than", "lte": "less or equal", "in": "value is in a list",
    "not_in": "value is not in a list", "contains": "case-insensitive substring",
    "exists": "field is present and not null", "between": "value within [low, high]",
}

ANALYSIS_TYPES = {
    "accessibility": {
        "description": "Run the reference application's routing engine from a neighbourhood's "
                       "representative origin and count what is reachable.",
        "applies_to": ["Neighborhood"],
        "parameters": {
            "mode": "walk | bike | transit",
            "minutes": "time budget, 1-60",
            "category": "a ServiceCategory id, or omitted for every category",
            "departure_time": "transit only; HH:MM or ISO, Europe/Zurich",
        },
        "produces": ["count", "nearest_minutes", "completeness", "per_category"],
        "computed_by": "app.accessibility / app.multimodal",
    }
}


def describe_schema() -> dict:
    return {
        "entity_types": {name: node.describe() for name, node in NODE_TYPES.items()},
        "relations": {name: rel.describe() for name, rel in RELATION_TYPES.items()},
        "filter_operators": FILTER_OPS,
        "analyses": ANALYSIS_TYPES,
        "query_language": {
            "pipeline": ["start", "filter", "traverse", "analysis", "group_by",
                         "aggregate", "having", "order_by", "limit", "return"],
            "aggregate_functions": ["count", "count_distinct", "sum", "avg", "min", "max"],
            "grouping": "One or more typed field paths; aggregate aliases may be used by HAVING, ORDER BY and return.",
        },
        "notes": [
            "Structural relations are persisted; analytical relations are computed on demand.",
            "Geometry is excluded from responses unless include_geometry=true.",
        ],
    }


def node_type(name: str) -> NodeType:
    from ..errors import UnknownEntityTypeError

    if name not in NODE_TYPES:
        raise UnknownEntityTypeError(
            f"Unknown entity type '{name}'.", known=sorted(NODE_TYPES))
    return NODE_TYPES[name]


def relation_type(name: str) -> RelationType:
    from ..errors import UnknownRelationError

    if name not in RELATION_TYPES:
        raise UnknownRelationError(
            f"Unknown relation '{name}'.", known=sorted(RELATION_TYPES))
    return RELATION_TYPES[name]
