# Concept

Traditional proximity asks “how close is it in a straight line?” Walking accessibility asks “how far is it through streets and paths I can use?” A river, railway, fenced site, or sparse crossing can make those answers very different.

The model now has two complementary layers:

```text
School   --IN_AREA------> Area
Accident --NEAR---------> School       (Euclidean, retained and labeled)
School   --ACCESS_POINT-> StreetNode
StreetNode --WALKABLE_TO-> StreetNode  (weighted by metres)
```

An isochrone is the set of places reachable within a travel-time budget. V0.2's authoritative isochrone is the returned collection of reachable street segments over the real OpenStreetMap pedestrian network of Basel. Any polygon is a visual aid only: the map widens the reachable segments into a translucent corridor, and the API's buffered polygon is opt-in and explicitly marked approximate. Neither claims that the space between two streets is reachable — only the segments do.

The dashed Euclidean circle the UI can overlay is the conceptual control: it is what proximity looks like when the network is ignored.

Official and community source observations remain distinguishable from derived relations and per-request analytical results through provenance metadata. Deterministic geometry and graph algorithms remain the source of spatial truth.
