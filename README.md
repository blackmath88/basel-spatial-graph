# Basel Spatial Graph

Heterogeneous public city data — OpenStreetMap street networks, cantonal open
government datasets, official population statistics and the Swiss GTFS
timetable — joined once into a **typed relational graph of Basel**, queryable
through a structured API and an MCP adapter, where **every answer carries its
own provenance**.

> The join is precomputed and correct, and the answer carries its own provenance.

Four sources that normally live in four incompatible shapes are normalized,
snapped and related ahead of time. Traversal, filtering, grouping and
aggregation then run over typed relations, and anything that depends on a
travel mode, a budget or a departure time is computed at request time by
deterministic routing engines — never stored, never averaged away, and always
labelled as a live computation rather than a stored fact.

```bash
git clone <this repo>
cd basel-spatial-graph/basel-spatial-graph-v0
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

That is the whole setup. The repository ships a **frozen snapshot of real
Basel data** (~8 MB in the clone), so there is no preparation step and the
server never opens a socket.

```bash
python -m app.spatial_graph.cli ask q6_children_underserved --table
```

```text
Which neighbourhoods with many children have below-median access to both
pharmacies and public transport?

  name          children   child_share   pharmacies   nearest   transit stops
  St. Alban         2237         17.5%            1    12.0 min             5
  Bruderholz        1994         20.5%            1     4.7 min            11
  Hirzbrunnen       1914         19.1%            2     8.7 min            14
```

Official population statistics, structural graph relations and a live routing
computation in one answer — which states which part came from where.

**15-Minute Basel**, the interactive accessibility map, is the reference
application: one client of the same core, not the product itself.

---

- **[Full documentation and quick start →](basel-spatial-graph-v0/README.md)**
- [Data sources and attribution](ATTRIBUTION.md) — OpenStreetMap (ODbL 1.0),
  data.bs.ch (CC BY 3.0 CH), opentransportdata.swiss
- [Licence](LICENSE) — MIT for the code; the committed data keep their upstream licences

The committed snapshot describes Basel as retrieved on **2026-08-20**. It is
real, and it is not current; the shipped timetable's last service date is
**2026-12-12**. The application reports at all times whether it is running on
the frozen snapshot, on locally prepared data, or on synthetic fixtures.
