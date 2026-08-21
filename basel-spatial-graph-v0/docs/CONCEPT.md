# Concept — the reference application

The conceptual model behind **15-Minute Basel**, the accessibility map. It predates the
Spatial Graph Core and is kept because the accessibility reasoning still holds; the relational
layer that the map became one client of is described in
[SPATIAL_GRAPH.md](SPATIAL_GRAPH.md) and [SPATIAL_GRAPH_MCP_CONCEPT.md](SPATIAL_GRAPH_MCP_CONCEPT.md).
Version labels such as *V0.2* below are this project's own milestones, listed in the README.

Traditional proximity asks “how close is it in a straight line?” Walking accessibility asks “how far is it through streets and paths I can use?” A river, railway, fenced site, or sparse crossing can make those answers very different.

The model now has four complementary layers:

```text
ServiceLocation --ACCESS_POINT-> StreetNode      (8 everyday destination categories,
                                                  once per network: walk and bike)
StreetNode   --WALKABLE_TO----> StreetNode       (pedestrian graph, weighted by metres)
BikeNode     --CYCLABLE_TO----> BikeNode         (bicycle graph, a different network)
StreetNode   --WALK_TO_STOP---> TransitStop      (how you board)
TransitStop  --scheduled trip-> TransitStop      (weighted by the timetable, not by metres)

School   --IN_AREA------------> Area             (context entities and their relations)
Accident --NEAR---------------> School           (Euclidean, retained and labeled)
```

The fourth layer is the one that changes the nature of the question. Distance on a graph is a
property of the graph; a departure at 14:34 is a property of *time*. Once the timetable is in, "what
can I reach?" stops having a single answer and starts having an answer per moment — which is what
the city actually feels like. The model keeps that visible rather than averaging it away: the
walk, the wait, the ride and the final walk are reported separately, and they add up.

Everyday destinations are one typed category model — `grocery`, `pharmacy`, `healthcare`, `school`,
`park`, `sport`, `library`, `culture` — not eight special cases. Schools were the first of them and
are now simply one category among the others. The categories are the *question*; the network is the
*answer*; the entity graph keeps the reusable facts.

An isochrone is the set of places reachable within a travel-time budget. V0.2's authoritative isochrone is the returned collection of reachable street segments over the real OpenStreetMap pedestrian network of Basel. Any polygon is a visual aid only: the map widens the reachable segments into a translucent corridor, and the API's buffered polygon is opt-in and explicitly marked approximate. Neither claims that the space between two streets is reachable — only the segments do.

The dashed Euclidean circle the UI can overlay is the conceptual control: it is what proximity looks like when the network is ignored.

The step from "what is reachable" to "what does that mean" is deliberately one short step, taken in
public: a category counts as reachable when at least one of its locations is inside the budget, and
the resulting `5 / 6` is labelled a prototype indicator with its definition on screen. Counts alone
mislead, so each category also reports its nearest service's walking time. No weighting, no rating,
nothing the reader cannot re-derive from the response.

Three modes then answer the same question with three cost models — distance ÷ speed for walking and
cycling, and walk + wait + ride + transfer + walk for transit — and return the same shape, so they
can be put side by side. The comparison is the point: it shows how much of "the 15-minute city"
depends on which 15 minutes you mean.

Once destinations exist, the query can be inverted: instead of asking what one origin reaches, ask
where in the city a category is *not* reachable. One multi-source Dijkstra over the whole network
answers that, and the answer is reported as street coverage — not as a population statistic it
cannot support.

Official and community source observations remain distinguishable from derived relations and per-request analytical results through provenance metadata. Deterministic geometry and graph algorithms remain the source of spatial truth.
