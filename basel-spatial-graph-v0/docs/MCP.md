# MCP adapter

The Model Context Protocol (MCP) is the agent-facing interface to the same
deterministic core used by FastAPI and the CLI. It is intentionally thin:

```text
MCP client → FastMCP registration → SpatialGraphService → graph / routing / provenance
```

There is no LLM, prompt chain, natural-language parser, SQL, Cypher, or HTTP
loopback inside the server. The calling model reasons externally and submits
typed tool arguments. Structural relations remain persisted; parameterized
`REACHABLE_WITHIN` results remain dynamic and are never written to the graph.

## Installation and startup

FastMCP 2.14.5 requires Python 3.10 or newer. The reference application still
supports its existing Python 3.9 environment, so use a separate MCP environment.
No data preparation is needed: the repository ships the frozen snapshot the
tools answer from.

```bash
cd basel-spatial-graph-v0
python3.10 -m venv .venv-mcp
source .venv-mcp/bin/activate
pip install -r requirements.txt -r requirements-mcp.txt
python -m app.mcp.server
```

The default transport is stdio. A local MCP client should launch that command
from the repository directory. Example configuration:

```json
{
  "mcpServers": {
    "basel-spatial-graph": {
      "command": "/absolute/path/basel-spatial-graph-v0/.venv-mcp/bin/python",
      "args": ["-m", "app.mcp.server"],
      "cwd": "/absolute/path/basel-spatial-graph-v0"
    }
  }
}
```

Remote deployment, authentication and Streamable HTTP are outside this first
local adapter.

## Tools

| Tool | Use | Delegates to |
|---|---|---|
| `describe_graph` | Discover types, fields, relations, operators and persisted/dynamic status | `SpatialGraphService.schema()` |
| `query_graph` | Run bounded filtering, traversal, grouping, aggregation and analysis | `SpatialGraphService.query()` |
| `find_reachable` | Reachable count and nearest service for one area/category/mode/budget | `AccessibilityAnalysis.accessibility()` |
| `compare_areas` | Compare one category across up to 25 areas and modes | cached `AccessibilityAnalysis.accessibility()` |
| `get_provenance` | Inspect an entity or declared relation | `SpatialGraphService.provenance()` |

`query_graph` accepts a structured query object, never natural language. Call
`describe_graph` first if fields or relation directions are unknown. Responses
default to no geometry and bounded rows.

Example grouped tool arguments:

```json
{
  "query": {
    "start": {"type": "PopulationObservation"},
    "group_by": ["year"],
    "aggregate": [
      {"function": "sum", "field": "children", "as": "children_total"}
    ],
    "order_by": [{"field": "year", "direction": "asc"}]
  }
}
```

## Provenance and recovery

Grouped queries return dataset metadata plus an `aggregation` block with the
grouping keys, function, input field, alias, classification, and null semantics.
Dynamic tools return their exact parameters and computation classification.
Query results are request-scoped and have no persistent IDs; their provenance
travels inline.

Every answer also carries `provenance.data_state`, saying whether the graph
behind it is the repository's frozen snapshot, data prepared locally since, or
synthetic fixtures — with the snapshot's date and the refresh command. A client
can therefore tell how current an answer is without inspecting the filesystem.

Expected domain failures return a stable error code, explanation and recovery
metadata such as valid fields, types, relations, modes, operators or functions.

## Resources decision

Schema and source metadata remain tools in P3. `describe_graph` returns them in
one bounded call and `get_provenance` handles focused inspection. MCP resources
would duplicate that surface without improving this first local workflow; they
can be added if real client tests show value.

## Limitations

- No natural-language planner exists yet (P4). Provenance itself is complete
  (P2.5): field-level classification, a source registry and computation records
  travel with every answer.
- Answers describe the frozen snapshot, not live Basel. `data_state` and the
  snapshot's `valid_until` say so explicitly.
- Origins are documented neighbourhood representative points, not arbitrary
  coordinates; the map/FastAPI interface remains the arbitrary-point client.
- Geometry and route itineraries are not exposed by the first MCP tools.
- Query/result IDs are not persisted.
- Stdio is the only transport: local subprocess, no remote endpoint and no
  authentication. Out of scope for this adapter.
