# Goalserve test API

FastAPI app that wraps **Goalserve** soccer data (fixtures, commentaries, heatmaps, live scores) and proxies the **Goalserve Inplay WebSocket** feed to browser clients.

---

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
```

Create a `.env` file in the project root (see [Environment variables](#environment-variables)), then run:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- **Interactive API docs (Swagger):** `http://localhost:8000/docs`  
- **ReDoc:** `http://localhost:8000/redoc`  
- **Health:** `GET http://localhost:8000/health`

---

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GOALSERVE_API_KEY` | Yes (for Goalserve feeds & WS auth) | Your Goalserve API key. |
| `GOALSERVE_INPLAY_SOCCER_URL` | No | Full URL for the inplay gzip feed if Goalserve gave you a custom link. If unset, the app uses `http://inplay.goalserve.com/inplay-soccer.gz` and appends `?key=<GOALSERVE_API_KEY>` when the key is set. |
| `INPLAY_SCHEDULER_JOB` | No | `true` / `false` — background poll of the inplay gzip feed and snapshot files (default `false`). |

Your server **IP must be whitelisted** in Goalserve for REST and WebSocket access to work.

---

## Web pages (HTML)

These return static or templated pages, not JSON.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Home: `index.html` (heatmap / match UI; default league & match ids in template context). |
| `GET` | `/live` | Live stats dashboard: `templates/live_stats.html`. |
| `GET` | `/today` | Today’s results page: `templates/today_result.html`. |
| `GET` | `/leagues` | League catalog UI: mapping ids, heatmap ids, seasons. |
| `GET` | `/static/...` | Static assets under the `static/` folder. |

---

## REST API — Soccer data (`/api/v1`)

All paths below are relative to your host (e.g. `http://localhost:8000`).

### Leagues (mapping + seasons)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/leagues` | Leagues from mapping + seasons. Query: `q`, `country`, `live_only`, `heatmap_only`, `sort` (`default`/`live`/`heatmap`/`next_match`), `page`, `per_page`. |
| `GET` | `/api/v1/leagues/{league_id}` | Single league row (ids, seasons, heatmap feed path). |

### Fixtures

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/fixtures/{league_id}` | Current season — `soccerfixtures/leagueid/{id}`. |
| `GET` | `/api/v1/fixtures/{league_id}/{season}` | Historical when available — `soccerhistory/leagueid/{id}-{season}`; season token must match `/leagues` (e.g. `2025` not `2025-2026` for some leagues). Falls back to current feed on error. |

### Match positions, heatmap, estimates

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/match-positions/{league_id}/{match_id}` | Lineup, heatmap block, and live **ball** position from the inplay gzip feed (when the match is live there). |
| `GET` | `/api/v1/match-positions/{league_id}/{match_id}/{season}` | Same with explicit season. |
| `GET` | `/api/v1/match-estimated-positions/{league_id}/{match_id}` | **Estimated** player positions (formation + heatmap + ball proximity — not true tracking). |
| `GET` | `/api/v1/match-estimated-positions/{league_id}/{match_id}/{season}` | Same with season. |
| `GET` | `/api/v1/heatmap/{league_id}/{match_id}` | Processed per-player heatmap for a match. |
| `GET` | `/api/v1/heatmap/{league_id}/{match_id}/{season}` | Recommended for historical matches. |

### Live & “today” football (Goalserve `soccernew` feeds)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/football/live` | All current **live** matches with stats. |
| `GET` | `/api/v1/football/live/{league_id}` | Live matches filtered by league id. |
| `GET` | `/api/v1/football/today` | Today’s schedule / results (service name: “today”). |
| `GET` | `/api/v1/football/today/{league_id}` | Filtered by league id. |

---

## Live inplay WebSocket (Goalserve proxy)

The app maintains one upstream WebSocket to Goalserve and **broadcasts** parsed JSON to every browser/client connected to your server.

### Persistent WebSocket (clients)

| | |
|--|--|
| **URL** | `ws://<host>:<port>/ws/soccer` (use `wss://` behind HTTPS) |
| **Behaviour** | After connect, you receive a stream of JSON objects. Each object has a `mt` field: `avl` (available events list) or `updt` (single-event update, includes ball position when present). The server may send the **last known frame** immediately on connect so you are not stuck waiting for the next message. |

Example (browser):

```javascript
const ws = new WebSocket("ws://localhost:8000/ws/soccer");
ws.onmessage = (ev) => console.log(JSON.parse(ev.data));
```

### Related HTTP endpoints

| Method | Path | Query | Description |
|--------|------|-------|-------------|
| `GET` | `/api/ws/soccer` | `timeout` (5–60 s, default `20`) | **One-shot** test: opens upstream WS, waits for first `avl`, returns JSON, closes. Use to verify key, IP whitelist, and message shape. |
| `GET` | `/api/v1/ws/soccer/status` | — | Upstream connection status: `connected`, `last_message_at`, `subscribed_clients`, etc. |

---

## Operational

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | `{"status":"ok"}` liveness check. |

---

## Background jobs

If `INPLAY_SCHEDULER_JOB=true`, a background thread polls the **inplay gzip** URL on an interval and writes snapshot files under `data/` (see `app/services/inplay_service.py` for league filters and file naming).

The **WebSocket upstream** is started automatically with the FastAPI app and does not depend on that flag.

---

## Troubleshooting

- **`504` on `GET /api/ws/soccer`** — No `avl` within `timeout`: check `GOALSERVE_API_KEY`, IP whitelist, and Goalserve status.  
- **`502` on `GET /api/ws/soccer`** — Upstream auth or connection error; see response body.  
- **Browser WS connects but no data** — Call `GET /api/v1/ws/soccer/status` and confirm `connected: true` and `last_message_at` updating.

For more detail on inplay JSON and fields, see `docs/inplay-live.md` in this repo.
