# Walking accessibility

A street graph represents intersections or path vertices as nodes and walkable connections as edges. Every edge has a length in metres. For a request, the service:

1. snaps the clicked longitude/latitude to its nearest network node;
2. converts time and walking speed to a distance budget: `km/h × 1000 × minutes / 60`;
3. runs single-source Dijkstra using `length_m` as cost;
4. keeps only nodes and complete street segments within the budget;
5. attaches schools through their nearest access nodes and sorts them by routed time;
6. intersects reachable segments with areas;
7. returns street GeoJSON and a narrow approximate display boundary.

At the default 4.8 km/h, 5, 10, and 15 minutes correspond to maximum routed distances of 400, 800, and 1,200 metres. These are network budgets, not circle radii. The response's `euclidean_vs_network` list exposes straight-line distance, route distance, and detour factor separately.

The current model assumes constant speed and adds the entity-to-node connector distance. It does not yet account for slope, stairs, surface, crossings, construction, or individual mobility needs. Origins farther from the network can have a large snap distance; clients should show that value, as the included UI does.
