# Accessibility

Three modes answer the same question — *what can I reach from here in this much time?* — with three
cost models:

```text
Walk      network distance / 4.8 km/h
Bike      network distance / 15 km/h        (a different graph; see CYCLING.md)
Transit   walk + wait + ride + transfer + walk, against the real timetable (see TRANSIT.md)
```

All three return the same shape — origin, budget, mode, reachable services by category, nearest per
category, completeness, provenance — which is what makes them comparable at
`/accessibility/compare`. What follows describes the street modes; transit has its own page.

## Walking and cycling

A street graph represents intersections and path vertices as nodes and usable connections as edges.
Every edge carries a length in metres, computed in EPSG:2056. For a request the service:

1. validates the coordinates and snaps the click to its nearest network node, in projected metres;
2. converts time and walking speed to a distance budget: `km/h × 1000 × minutes / 60`;
3. runs single-source Dijkstra with `cutoff = budget` and `weight = "length_m"`;
4. collects the edges between reachable nodes — walking outward from the reachable set, not scanning the
   whole city;
5. looks up every service attached to a reachable node, adds its snap distance, keeps what fits in the
   budget, and groups the result by category — sorted by walking time, with each category's nearest;
6. derives the prototype completeness indicator from the six essential categories;
7. intersects the reachable segments with neighbourhood polygons;
8. returns per-edge GeoJSON, plus a labelled Euclidean circle for comparison.

Step 5 is a dictionary lookup, not a search: the service index keeps an `access node -> services` map,
so the cost of answering "what can I reach?" is proportional to the reachable set, not to the catalogue.
See [the services guide](SERVICES.md) for the categories, their sources and how they attach.

At the default **4.8 km/h**, 5 / 10 / 15 / 30 minutes are maximum routed distances of
400 / 800 / 1,200 / 2,400 m. Cycling at 15 km/h turns the same budgets into
1,250 / 2,500 / 3,750 / 7,500 m over the bicycle graph.
These are network budgets, not circle radii. `euclidean_vs_network` reports straight-line distance, routed
distance and detour factor for each category's *nearest* service, so the difference is measurable rather
than implied.

Counts alone mislead — "18 groceries" says nothing about whether the first one is 2 or 14 minutes away —
so every category also reports `nearest_minutes` and `nearest_id`.

## Reachable, not nearby

The map can overlay the dashed Euclidean circle of the same budget. From Barfüsserplatz, the 15-minute
circle covers 4.5 km² of map; the walking network reaches ~76 km of street in a much more ragged shape,
cut short by the Rhine, the rail corridor and the motorway. The circle is always drawn *under* the network
and is labelled `NOT reachability` in its own properties.

## Failure modes, handled explicitly

| Situation | Behaviour |
|---|---|
| Click far outside the network | HTTP 422 `outside_network` with the actual distance and the 1,000 m limit |
| NaN / out-of-range coordinates | HTTP 422 `invalid_coordinates` |
| `minutes` or speed ≤ 0 | HTTP 422 |
| Origin on an isolated fragment | Valid response: 1 node, 0 edges, plus a `notes` entry naming the fragment |
| Small disconnected component | `snapped_origin.component_size` is reported and a note is added |
| Edge with a missing or unusable length | Rebuilt from projected node positions, or dropped and counted |
| Empty network | HTTP 503 `empty_network` |
| Unknown travel mode | HTTP 404 `unknown_mode`, listing the known ones |
| Transit asked for with no timetable prepared | HTTP 503 `transit_unavailable` |
| Unreadable departure time | HTTP 422 `invalid_departure` |
| Departure date outside the feed | answered on the nearest covered date, stated in `notes` |
| Unknown service category | HTTP 404 `unknown_category`, listing the known ids |
| Unknown service id | HTTP 404 `unknown_service` |
| Route to a service that is not attached to the network | HTTP 422 `unroutable_service` |
| Service > 500 m from any street | kept in the catalogue, excluded from routing, flagged `unreachable` |

None of these produce a stack trace; each returns `{"error": …, "message": …, "details": …}`.

## What the model does not do

Constant speed, no slope, no stairs penalty, no surface or width, no crossings, signals, construction or
opening hours, and no individual mobility needs. Origins snap to the nearest node rather than the nearest
point along an edge; the snap distance is reported so a client can show it, as the sidebar does.

The completeness indicator counts categories, not quality: one kiosk counts the same as a supermarket,
and a category with a single reachable location scores exactly like one with twenty. It is labelled a
prototype wherever it appears, and its definition ships inside the response. It is computed
independently for each mode, so "5/6 walking, 6/6 cycling" is two honest statements rather than one
blended score.
