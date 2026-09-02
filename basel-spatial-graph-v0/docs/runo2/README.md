# runO2

runO2 is the Hack am Rhein warm-up application built on top of Basel Spatial Graph.

> Basel's trams measure the air. What does your running route pass through?

The product idea is intentionally narrower than a general running app: generate a few plausible running loops from a chosen start point and distance, compare them against measured Basel air-quality data, show the limits of that evidence, and export the selected route as GPX.

## Current status

**Repository foundation:** Basel Spatial Graph is already present and working as the parent system. It provides the walking/cycling network, structured spatial relations, provenance-aware query model, API/CLI/MCP surfaces, frozen snapshots and the existing 15-Minute Basel reference application.

**runO2 concept:** documented in [`clean-air-run-concept.json`](clean-air-run-concept.json).

**Scope guardrails:** documented in [`deferred-extensions.json`](deferred-extensions.json). Nothing in that file is part of the warm-up build unless the core entry has shipped.

**UX direction:** agreed at concept/mock level: a cinematic intro leading into a map-first planner, with a restrained left control rail and a pre-run report before GPX export.

**Imported Claude build:** not yet integrated. As of 2026-09-02, the current GitHub `main` tree contains no ZIP archive and no separate runO2 application directory to unpack. Do not fabricate this step. When the archive is committed or otherwise made available, unpack it into a dedicated application directory and reconcile it against this documentation.

## Product boundary

runO2 should not become another Strava or Komoot clone.

The differentiator is the evidence layer:

- measured PM2.5 / PM10 from Basel tram sensors;
- routing over the existing deterministic walking network;
- explicit display of unmeasured route segments;
- provenance attached to values;
- route comparison rather than health recommendation;
- GPX export rather than account/OAuth integrations.

The application must never render an unmeasured street as "clean". Absence of evidence is an explicit state.

## UX direction

### 1. Intro

A short, styled parallax landing sequence with generic runner imagery/video. It should feel atmospheric rather than like a fitness SaaS landing page.

Suggested hierarchy:

- `runO2`
- one-sentence premise;
- subtle reference to Basel tram measurements;
- one CTA: **Plan a run**.

The intro may be visually cinematic; the planner should become much quieter and more instrument-like.

### 2. Planner

Desktop target:

```text
┌────────────────────┬──────────────────────────────────────┐
│                    │                                      │
│ runO2              │                                      │
│                    │              BASEL MAP               │
│ Start              │                                      │
│ Distance           │          route candidates            │
│ When               │                                      │
│ Preferences        │                                      │
│                    │                                      │
│ [ Find routes ]    │                                      │
└────────────────────┴──────────────────────────────────────┘
```

The map is the main object. The left rail should only ask for decisions needed to generate the route.

Core inputs:

1. **Start** — click map or use current location/address where practical.
2. **Distance** — simple slider / presets.
3. **When** — now, later today, tomorrow where forecast data is available.
4. **Preferences** — secondary, not required for first route generation.

Return three candidate loops rather than claiming a mathematically optimal route.

Each candidate should show at minimum:

- distance;
- estimated duration;
- ascent;
- measured-air coverage;
- relative air comparison against the other candidates.

### 3. Context strip

Weather and pollen are context, not a dashboard.

Small map-level indicators are enough:

- temperature;
- rain probability / near-term rain;
- wind;
- AQI where available;
- dominant pollen signal.

The existing Basel weather prototype established a useful visual language: near-black paper, IBM Plex Mono for instrument labels, restrained Rhine green, thin rules, and data-source transparency. Reuse those principles rather than embedding the entire weather application.

### 4. Pre-run report

Before export, open a focused report for the selected route.

Suggested structure:

```text
YOUR RUN
8.1 km · 43 min · +46 m

AIR
lowest measured PM of 3 candidates
71% of route covered by measurements
29% explicitly unmeasured

CONDITIONS
18.4°C
0% rain next hour
7 km/h NW
AQI 24 · good

POLLEN
grass      low
birch      none
ragweed    none

TERRAIN
+46 / -44 m
max smoothed grade 4.1%

WHY THIS ROUTE
short explanation based only on computed evidence

[ EXPORT GPX ]
```

The report must separate measured, derived, dynamic/forecast and unmeasured values.

### 5. GPX

Export the selected route as GPX. Provenance may be placed in `<extensions>` so compatible apps can ignore it while the record still carries the evidence used to create it.

## Weather, pollen and elevation

Weather/pollen integration is now considered useful UX context, despite the earlier concept file deferring a weather layer. Keep it deliberately narrow and dependency-light.

Preferred implementation direction:

- use Open-Meteo-style keyless browser/server calls where practical;
- cache/freeze only if reliability becomes an issue;
- do not make route generation dependent on a weather service being online;
- pollen is contextual and must not become personalized medical advice.

Elevation is useful and should be added to the route report if the data quality is adequate. Compute:

- ascent / descent;
- a smoothed elevation profile;
- smoothed grade rather than false street-level precision.

## Immediate build order

### Gate 0 — air-data viability

Before polishing route recommendations, answer the questions already defined in the concept:

1. Does spatial variation exceed same-location/sensor noise enough to affect route ranking?
2. Does time of day change segment ranking enough to justify a time control?
3. What share of the walking network is actually near a qualifying measurement?

Output: `experiments/AIR_VIABILITY.md`.

If this gate fails, change the product claim rather than forcing a route recommendation.

### P1 — measured air layer

- ingest the tram PM dataset;
- normalize timestamps / coordinates;
- attach readings to appropriate network segments;
- compute counts, observation windows and coverage;
- preserve source/provenance metadata;
- add tests for snapping and missing coverage.

### P2 — route comparison

- generate three reasonable loop candidates from start + distance;
- score only measured portions of each route;
- keep unmeasured share explicit;
- return a comparison rather than an absolute health score.

### P3 — runO2 application

- integrate the Claude UI build once available;
- map + left control rail;
- candidate cards;
- selected-route visualization;
- responsive/mobile treatment after desktop interaction works.

### P4 — context + report

- weather;
- pollen;
- elevation profile / ascent;
- pre-run report;
- explicit provenance drawer/labels.

### P5 — export + submission polish

- GPX export;
- provenance extensions;
- README / attribution;
- public deploy;
- one or two strong screenshots;
- confirm every user-visible statistic is traceable.

## Not now

The deferred file is authoritative. In particular, do not spend warm-up time on:

- Strava OAuth;
- Google Maps;
- accounts/history/training plans;
- mathematically optimal loop generation;
- ATProto records;
- Basel Evidence / parliamentary reports;
- generic multi-dataset dashboards.

## Definition of done for the warm-up

A visitor can:

1. open the page;
2. choose a start and approximate running distance;
3. see three plausible Basel loops;
4. understand which parts have actual tram air measurements and which do not;
5. compare route/air/terrain/current-condition context;
6. review a concise report;
7. export a GPX file.

The strongest outcome is not "nice running app". It is:

> I did not know Basel measured this — and I can see exactly what the data does and does not know about my route.
