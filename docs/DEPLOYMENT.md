# Deploy the full 15-Minute Basel map

This is the reachability map: arbitrary origins, 5/10/15/30-minute budgets,
walking, cycling and timetable-based walk–transit–walk journeys. The original
Python routing engines and frozen snapshot remain the source of results.

## Backend

Deploy this repository using its `render.yaml` Blueprint:

https://render.com/deploy?repo=https://github.com/blackmath88/basel-spatial-graph

The Docker service includes the prepared data and serves both the map and its
API. No API keys or data download are needed. Check `/health` and open `/` to
verify the interactive map before deploying the Pages frontend.

`BASEL_CORS_ORIGINS=https://blackmath88.github.io` allows the Pages frontend to
read the routing API; other origins receive no CORS permission. Same-origin
local use is unaffected. The configured service uses Render's free plan.

## GitHub Pages frontend

1. In Settings → Pages, select GitHub Actions as the publishing source.
2. Set the repository Actions variable `BASEL_API_URL` to the actual HTTPS
   origin of the deployed backend (no trailing API path).
3. Run `Deploy 15-Minute Basel to GitHub Pages`, supplying that same backend URL.

The map URL will be https://blackmath88.github.io/basel-spatial-graph/.
Pushes to `main` use the repository variable for subsequent builds. The build
rejects a missing or malformed backend URL instead of publishing a map with
nonfunctional routing.

The snapshot dates and provenance stay visible in the existing map. Transit
uses the shipped timetable and is not a real-time service feed.
