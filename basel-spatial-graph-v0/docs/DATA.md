# Data and provenance

## Intended live datasets

| Entity | Basel-Stadt dataset | Role |
|---|---:|---|
| Area | 100042 | Statistical neighborhoods / polygons |
| School | 100029 | School locations in Basel |
| Accident | 100120 | Geocoded road accidents |

The code uses the Basel-Stadt Opendatasoft Explore API 2.1 endpoint.

## Fallback data

`app/fixtures.py` contains deliberately synthetic records positioned around Basel. They are not official observations and are labeled with `fixture: true`. The application automatically enters fixture mode when live ingestion fails; `/health` reports the mode and fallback reason.

## Provenance

Source nodes include dataset ID and source record ID. Derived edges contain their derivation method, and proximity edges also store the distance threshold and calculated distance.
