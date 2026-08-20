# Public transport

Walking and cycling ask "how far can I get?". Transit asks a harder question, because the answer
depends on *when you leave*: a tram you just missed costs you nine minutes of standing still.

## What GTFS is

GTFS — the General Transit Feed Specification — is the format agencies publish timetables in. It is
a zip of CSV files:

| File | What it holds |
|---|---|
| `stops.txt` | every stop and platform, with coordinates |
| `routes.txt` | the lines — Tram 8, Bus 30, S-Bahn S3 |
| `trips.txt` | one row per scheduled run of a route |
| `stop_times.txt` | when each trip calls at each stop |
| `calendar.txt` / `calendar_dates.txt` | which days a trip actually runs |
| `transfers.txt` | minimum interchange times between stops |

## Where the Swiss feed comes from

[opentransportdata.swiss](https://data.opentransportdata.swiss/dataset/timetable-2026-gtfs2020),
the official Swiss open-data platform. The whole national timetable, published weekly, free, no key:

```text
https://data.opentransportdata.swiss/dataset/timetable-2026-gtfs2020/permalink
```

It is a **224 MB zip whose `stop_times.txt` alone is 2.9 GB uncompressed**, so nothing is ever loaded
whole. `python -m app.prepare_data` streams every file straight out of the archive and filters as it
goes, in about 35 seconds:

```text
stops.txt        →  1,437 stations inside the extraction box
stop_times.txt   →  33,182,263 rows scanned in one pass, 200,696 local trips kept
trips.txt        →  the route and calendar of those trips
routes.txt       →  246 routes
calendar*.txt    →  only the calendars those trips use
transfers.txt    →  official interchange times between kept stops
```

The result is cached as `data/processed/basel_transit.npz` — 6 MB, loads in 0.3 s. The server never
reads the big archive.

## The extraction boundary

```text
latitude  47.42 – 47.68
longitude  7.40 –  7.90
```

Deliberately wider than Basel-Stadt. Basel's local network crosses three borders, and cutting at the
canton line would truncate perfectly ordinary journeys. The box covers Baselland out to Liestal and
Rheinfelden, Lörrach and Weil am Rhein in Germany, and Saint-Louis in France — roughly 37 × 29 km,
which is more than a 30-minute local journey can cross.

Platforms are collapsed into their parent station, because passengers change between platforms of
one station for free and it keeps the graph small: 1,437 stations rather than several thousand
platform records.

**One asymmetry worth knowing:** the timetable box is wider than the *walking* network, which covers
the canton only. You can therefore ride through and past the canton boundary, but you can only get
on or off where the pedestrian network exists — 283 of the 1,437 stations. Since every prepared
service is inside the canton too, nothing reachable is lost. It is reported in `/data/status`.

## Walk → Ride → Walk

A transit journey in this model is:

```text
origin
  ↓ walk            (pedestrian network, 4.8 km/h)
boarding stop
  ↓ wait            (until the next scheduled departure)
  ↓ ride            (the actual timetabled trip)
[exit stop → walk or platform interchange → wait → ride]     ← at most once
exit stop
  ↓ walk
destination
```

Every one of those is real time. **Waiting is never assumed to be zero.** The response reports the
breakdown for the nearest destinations, and the parts always add to the total:

```text
St. Jakobs-Apotheke — 6.8 min

Walk    3.8 min   to the stop
Board   Basel, Bankverein · Tram 11 → Basel, Dreispitz at 14:34
Wait    0.2 min
Ride    Tram 11 · 1.0 min · 1 stop
Exit    Basel, Aeschenplatz at 14:35
Walk    1.8 min   to the destination
```

### How it is computed

Three cheap phases, in `app/multimodal.py`:

1. **Dijkstra on the pedestrian graph from the origin** → walking time to every node, and therefore
   to every stop that has one.
2. **RAPTOR** (`app/transit_index.py`) — a round-based transit search. Round *k* holds the earliest
   arrival at every stop using at most *k* vehicles, so limiting transfers is just running fewer
   rounds. Each round boards the earliest departure at or after the moment the passenger is actually
   ready at that stop.
3. **A second, multi-source Dijkstra** seeded at the origin *and* at every stop transit reached, in
   elapsed seconds. That gives the earliest arrival at every pedestrian node, which is exactly what
   the reachable-services lookup needs. Destinations you could simply have walked to fall out of the
   same pass, because the origin is one of the sources.

## Departure time

Transit accessibility needs a departure moment. The app shows a time control only when *Walk +
Transit* is selected and defaults it to now.

Everything is **Europe/Zurich**, because that is the timezone the Swiss timetable is written in.
Accepted forms: `14:30`, `2026-08-20T14:30`, or nothing (meaning now).

If the requested date falls outside the prepared feed's window, the answer uses the nearest date the
feed does cover and says so in `notes` and in `service_date_is_requested_date` — it never silently
answers about a different day.

## After-midnight GTFS times

This is the classic GTFS trap. A tram leaving at 01:05 on Friday morning belongs to **Thursday's**
service day, and GTFS writes it as `25:05:00`. `datetime.strptime` refuses that string outright.

So the model keeps times as *seconds after that service day's midnight*, where values above 86,400
are normal. When you ask about a moment on day D, the search considers trips from day D and, shifted
by −86,400 seconds, the after-midnight tail of day D−1. Asking about 00:30 on Friday correctly
catches Thursday's 25:05 night bus at 01:05.

## Transfers

The default is **`max_transfers = 1`** — walk, ride, change once, ride, walk. It can be raised to 3
via the API, but 1 is the documented scope: correct and explainable matters more here than becoming
a complete journey planner.

Two kinds of interchange are modelled:

- **Platform interchange** at one station: costs `MIN_TRANSFER_SECONDS` (default 90 s), or the
  official `min_transfer_time` from `transfers.txt` where the feed gives one.
- **Walking transfer** between two stations within 300 m: costs the walk at 4.8 km/h. This one does
  not count as a "transfer" in the vehicle sense — riding one tram and then walking to a nearby
  destination is still a zero-transfer journey.

## What the map shows

Rendering all 246 Basel routes at full strength would say nothing. In transit mode the map shows:

- the **walking reach** from the origin, in the mode colour;
- the **ride segments actually used** by the result;
- the **stops reached by vehicle**, with their arrival times;
- and, when a destination is selected, its full itinerary: origin walk leg, ride legs, final walk.

The full walkable envelope around *every* reached stop is deliberately not drawn — at 30 minutes
that is most of Basel, several megabytes of GeoJSON, and a blue blob. The service profile still
counts all of it.

## Known limitations

- **Static timetable only.** No GTFS-Realtime, no delays, no disruptions, no vehicle positions.
- **One transfer by default**, and no bike + transit or park + ride.
- **No shapes.** Ride segments are drawn as straight lines between stops, not along the real track.
  The times are exact; the drawn line is schematic.
- **No accessibility attributes** — step-free access, wheelchair boarding and low-floor vehicles are
  in the feed but not modelled.
- **No fares or zones.**
- **Boarding and alighting need pedestrian coverage**, which is canton-wide only (see above).
- **Trip ordering** is assumed not to overtake within a pattern, which is true in this feed and
  standard for RAPTOR.

## Refreshing

```bash
python -m app.prepare_data --transit-only            # reuse the cache if valid
python -m app.prepare_data --transit-only --refresh  # re-download and re-extract (~4 min)
```

`data/raw/gtfs/gtfs_ch.zip` is kept so a re-extract does not re-download. Delete it to force a fresh
copy of the feed.
