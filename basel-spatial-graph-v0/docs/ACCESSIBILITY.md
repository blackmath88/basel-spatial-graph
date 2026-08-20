# Walking accessibility

A street graph represents intersections and path vertices as nodes and walkable connections as edges.
Every edge carries a length in metres, computed in EPSG:2056. For a request the service:

1. validates the coordinates and snaps the click to its nearest network node, in projected metres;
2. converts time and walking speed to a distance budget: `km/h × 1000 × minutes / 60`;
3. runs single-source Dijkstra with `cutoff = budget` and `weight = "length_m"`;
4. collects the edges between reachable nodes — walking outward from the reachable set, not scanning the
   whole city;
5. attaches schools through their pre-computed access nodes, adds the connector distance, sorts by routed
   distance;
6. intersects the reachable segments with neighbourhood polygons;
7. returns per-edge GeoJSON, plus a labelled Euclidean circle for comparison.

At the default **4.8 km/h**, 5 / 10 / 15 minutes are maximum routed distances of 400 / 800 / 1,200 m.
These are network budgets, not circle radii. `euclidean_vs_network` reports straight-line distance, routed
distance and detour factor for every reachable school, so the difference is measurable rather than implied.

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

None of these produce a stack trace; each returns `{"error": …, "message": …, "details": …}`.

## What the model does not do

Constant speed, no slope, no stairs penalty, no surface or width, no crossings, signals, construction or
opening hours, and no individual mobility needs. Origins snap to the nearest node rather than the nearest
point along an edge; the snap distance is reported so a client can show it, as the sidebar does.
