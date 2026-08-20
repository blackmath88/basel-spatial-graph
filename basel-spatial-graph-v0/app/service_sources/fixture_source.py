"""Deterministic synthetic services, aligned with the fixture walking grid.

Every location is placed near a known fixture street node so tests can reason
about reachability by hand. Clearly labelled; never presented as real Basel.
"""
from __future__ import annotations

from typing import Sequence

from ..service_model import ServiceCategory, ServiceLocation
from .base import ServiceSource

SOURCE_NAME = "synthetic fixture"
LICENSE = "fixture-only; not real observations"
RETRIEVED_AT = "2020-01-01T00:00:00+00:00"  # fixed, so fixtures stay byte-stable

# (category, name, lon, lat) — the grid runs 7.574..7.610 E, 47.550..47.566 N.
FIXTURE_SERVICES = (
    (ServiceCategory.GROCERY, "Fixture Grocery West", 7.5745, 47.5505),
    (ServiceCategory.GROCERY, "Fixture Grocery Centre", 7.5865, 47.5585),
    (ServiceCategory.GROCERY, "Fixture Grocery East", 7.6045, 47.5585),
    (ServiceCategory.PHARMACY, "Fixture Pharmacy Centre", 7.5805, 47.5585),
    (ServiceCategory.PHARMACY, "Fixture Pharmacy East", 7.6045, 47.5625),
    (ServiceCategory.HEALTHCARE, "Fixture Practice", 7.5865, 47.5545),
    (ServiceCategory.SCHOOL, "Fixture School One", 7.5820, 47.5570),
    (ServiceCategory.SCHOOL, "Fixture School Two", 7.6000, 47.5580),
    (ServiceCategory.PARK, None, 7.5745, 47.5585),               # deliberately unnamed
    (ServiceCategory.SPORT, "Fixture Sports Hall", 7.5925, 47.5505),
    (ServiceCategory.LIBRARY, "Fixture Library", 7.5805, 47.5545),
    (ServiceCategory.CULTURE, "Fixture Museum", 7.5865, 47.5625),
)


class FixtureServiceSource(ServiceSource):
    name = SOURCE_NAME
    license = LICENSE

    @property
    def categories(self) -> Sequence[ServiceCategory]:
        return tuple(ServiceCategory)

    def fetch(self, category: ServiceCategory):
        return [
            ServiceLocation(
                id=f"service:{category.value}:fixture:{index}",
                category=category,
                lon=lon, lat=lat,
                name=name,
                source=SOURCE_NAME,
                source_dataset="Synthetic Basel-centred service fixture",
                source_id=str(index),
                license=LICENSE,
                retrieved_at=RETRIEVED_AT,
                attributes={"fixture": True},
            )
            for index, (cat, name, lon, lat) in enumerate(FIXTURE_SERVICES)
            if cat == category
        ]


def fixture_services(categories=None):
    source = FixtureServiceSource()
    wanted = tuple(categories) if categories else tuple(ServiceCategory)
    services = []
    for category in wanted:
        services.extend(source.fetch(category))
    return services
