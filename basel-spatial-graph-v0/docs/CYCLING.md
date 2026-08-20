# Cycling

Cycling is deliberately the simple mode. It reuses the walking engine unchanged and swaps two
things: the graph it runs on, and the speed it assumes.

## Where the bicycle network comes from

The same place as the walking one — OpenStreetMap via [OSMnx](https://osmnx.readthedocs.io/) — but
with `network_type="bike"` instead of `"walk"`, over the same place (*Basel-Stadt, Switzerland*).

```bash
python -m app.prepare_data --network-only     # prepares both networks
```

| | Walking | Cycling |
|---|---:|---:|
| Nodes | 14,102 | 5,918 |
| Edges | 19,258 | 8,034 |
| Length | 884 km | 584 km |
| Cache | `basel_walking_network.graphml` | `basel_cycling_network.graphml` |

**It is genuinely a different graph, not a relabelled one.** OSMnx's bicycle filter drops footways,
pedestrian-only alleys, steps and anything tagged `bicycle=no`, and keeps cycleways, paths, streets
and tracks a bicycle may use. That is why the cycling network has 300 km *less* road in it than the
walking one even though a bicycle goes further per minute: a pedestrian can use far more of Basel's
surface than a bicycle can.

## How bike access differs from walking

- Pedestrian shortcuts through parks, stairs and passages are gone.
- Motorways and their slip roads are excluded from both.
- Bridges and underpasses that carry cycle traffic are kept.

Both networks are treated as **undirected**. For pedestrians that is simply true. For a prototype
cycling model it is a stated simplification: one-way streets usually have a contraflow cycle lane in
Basel, and modelling directionality properly would mean modelling turn restrictions too, which is
more accuracy than the rest of the model can support.

## The 15 km/h assumption

```text
travel time = network distance / 15 km/h
```

At the default 15 km/h a budget of 5 / 10 / 15 minutes becomes 1,250 / 2,500 / 3,750 metres, spent
along real edge lengths by the same Dijkstra that walking uses. Change it per request with
`cycling_speed_kmh`, or globally with `BASEL_CYCLING_SPEED_KMH`.

15 km/h is a flat door-to-door average for an ordinary bicycle in a flat-ish city: fast enough to
account for actual cycling, slow enough to absorb junctions, lights and parking the bike.

## What the model ignores

Everything that would make it a real cycling router:

- **elevation and slope** — Bruderholz is a genuine climb and this model does not know;
- **traffic stress** — a 50 km/h arterial costs the same as a quiet lane;
- **protected infrastructure** — cycle lanes get no preference;
- **surface quality**, cobbles, gravel;
- **accident risk**;
- **turn penalties and one-way rules**;
- **e-bikes**, cargo bikes, bike sharing, and bike + transit journeys;
- **parking the bike** at the destination.

Each of those is a later milestone, not an oversight. The provenance in every cycling response says
`"routing_method": "network distance / 15 km/h"` so the assumption travels with the answer.

## How services connect

Every service location is snapped **twice** during preparation — once to the walking network and
once to the cycling network — and both attachments are cached:

```text
ServiceLocation
   ├── access["walk"] → node on the pedestrian graph
   └── access["bike"] → node on the bicycle graph
```

They are usually different nodes, and a service can be well attached to one network and badly to the
other. `/data/status` reports snap quality per network, and each reachable-service row in a cycling
result carries `access_network: "bike"` so you can see which attachment was used.

Of 1,308 prepared services, 1,289 attach to each network. The 19 that do not are outside the
prepared area entirely (regional museums in Germany, France and Baselland).

## Verifying it

For the same origin and budget, cycling should reach further than walking — and it does. From
Barfüsserplatz at 15 minutes:

| | Walking | Cycling |
|---|---:|---:|
| Groceries | 25 | 154 |
| Pharmacies | 18 | 60 |
| Schools | 44 | 386 |
| Parks | 24 | 117 |

The **Network & routing detail** panel in the app shows the mode, the speed, the network source and
the reachable node/edge counts for whichever mode is active.
