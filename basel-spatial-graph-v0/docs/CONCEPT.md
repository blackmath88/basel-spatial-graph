# Concept

## Thesis

GIS answers **where** very well. A spatial graph adds a reusable model for **how things relate**.

Instead of repeatedly combining layers for every analysis, relationships such as containment, adjacency, proximity, reachability and dependency become typed edges with provenance.

V0 deliberately asks only one question: can ordinary Basel public GIS data become an inspectable relational object that is useful through both a map and an API?

## V0 model

```text
School   --IN_AREA--> Area
Accident --IN_AREA--> Area
Accident --NEAR-----> School
Area     --ADJACENT_TO--> Area
```

Every node keeps geometry. Every derived edge records how it was calculated.

## Long-term direction

A Spatial Graph API above GIS infrastructure, usable by GIS analysts, conventional apps and constrained AI agents. LLMs should translate intent and explain results; deterministic GIS/graph code remains the source of spatial truth.
