# Concept

Traditional proximity asks “how close is it in a straight line?” Walking accessibility asks “how far is it through streets and paths I can use?” A river, railway, fenced site, or sparse crossing can make those answers very different.

The model now has three complementary layers:

```text
ServiceLocation --ACCESS_POINT-> StreetNode      (8 everyday destination categories)
StreetNode   --WALKABLE_TO----> StreetNode       (weighted by metres)

School   --IN_AREA------------> Area             (context entities and their relations)
Accident --NEAR---------------> School           (Euclidean, retained and labeled)
```

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

Once destinations exist, the query can be inverted: instead of asking what one origin reaches, ask
where in the city a category is *not* reachable. One multi-source Dijkstra over the whole network
answers that, and the answer is reported as street coverage — not as a population statistic it
cannot support.

Official and community source observations remain distinguishable from derived relations and per-request analytical results through provenance metadata. Deterministic geometry and graph algorithms remain the source of spatial truth.
