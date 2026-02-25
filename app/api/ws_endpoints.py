"""
WebSocket and REST endpoints for the live soccer feed.

Endpoints
─────────
GET  /api/ws/soccer
    One-shot REST test: authenticates, connects to Goalserve WS, waits for the
    first "avl" (available events) message, returns the parsed JSON, disconnects.
    Use this to verify connectivity and inspect the live event structure without
    keeping a persistent connection open.

    Query params:
        timeout (float, default 20) – seconds to wait for the avl message

WS   /ws/soccer
    Persistent WebSocket proxy.
    Connect and receive a continuous stream of live soccer match updates.
    Each frame is a JSON object; field "mt" indicates the type:

        mt = "avl"   – full snapshot of all live events (sent on connection)
        mt = "updt"  – incremental update for a single event

    Frame shape:
    {
        "mt": "avl" | "updt",
        "updated": "<timestamp>",
        "updated_ts": <unix ms>,
        "total_events": <int>,          // avl only
        "events": [
            {
                "event_id": "...",
                "match": {
                    "id", "mid", "name", "sport", "league",
                    "start_time", "start_date", "period",
                    "score", "minute", "seconds",
                    "state", "ball_pos", "state_info"
                },
                "home_team": { "name", "score", "kit_color" },
                "away_team": { "name", "score", "kit_color" },
                "core": { "stopped", "blocked", "finished", "updated" },
                "stats": {
                    "goals":             { "home": int, "away": int },
                    "corners":           { "home": int, "away": int },
                    "yellow_cards":      { "home": int, "away": int },
                    "red_cards":         { "home": int, "away": int },
                    "possession":        { "home": int, "away": int },
                    "attacks":           { "home": int, "away": int },
                    "dangerous_attacks": { "home": int, "away": int },
                    "shots_on_target":   { "home": int, "away": int },
                    "shots_off_target":  { "home": int, "away": int },
                    ... (throw_ins, free_kicks, goal_kicks, penalties, substitutions)
                },
                "timeline": [ { "code", "minute", "description" }, ... ]
            }
        ]
    }

GET  /api/v1/ws/soccer/status
    Returns upstream connection health and client metrics.
"""

import asyncio

from fastapi import APIRouter, Query, WebSocket
from fastapi.responses import JSONResponse

from app.services.ws_soccer_service import soccer_ws_service

router = APIRouter(tags=["soccer-websocket"])


# ---------------------------------------------------------------------------
# REST test endpoint  –  one-shot snapshot
# ---------------------------------------------------------------------------

@router.get("/api/ws/soccer")
async def get_soccer_snapshot(
    timeout: float = Query(default=20.0, ge=5.0, le=60.0, description="Seconds to wait for avl message"),
) -> JSONResponse:
    """
    One-shot test endpoint.

    Authenticates with Goalserve, opens a WebSocket connection, waits for the
    first **avl** (available events) message, returns the parsed result as JSON,
    then disconnects.  The connection is not kept alive between calls.

    Use this to:
    - Verify your API key and IP whitelist are configured correctly
    - Inspect the live event/match structure
    - Quickly pull the current live soccer snapshot
    """
    try:
        data = await soccer_ws_service.snapshot(timeout=timeout)
        return JSONResponse(content=data)
    except asyncio.TimeoutError:
        return JSONResponse(
            status_code=504,
            content={
                "error": "timeout",
                "message": f"No 'avl' message received within {timeout}s. "
                           "Ensure your IP is whitelisted by Goalserve.",
            },
        )
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"error": "upstream_error", "message": str(exc)},
        )


# ---------------------------------------------------------------------------
# Persistent WebSocket proxy
# ---------------------------------------------------------------------------

@router.websocket("/ws/soccer")
async def ws_soccer(websocket: WebSocket) -> None:
    """
    Persistent live soccer WebSocket feed.

    The upstream Goalserve connection is shared across all clients.
    Every incoming frame (avl or updt) is broadcast to all connected clients.
    """
    await soccer_ws_service.handle_client(websocket)


# ---------------------------------------------------------------------------
# Status / health endpoint
# ---------------------------------------------------------------------------

@router.get("/api/v1/ws/soccer/status")
async def ws_soccer_status() -> JSONResponse:
    """
    Upstream connection health and metrics.

    Fields:
    - connected          : upstream WS is currently open
    - sport              : "soccer"
    - upstream_url       : URL in use (token redacted)
    - reconnect_attempts : attempts since last clean connect
    - last_message_at    : ISO-8601 UTC timestamp of last received frame (or null)
    - subscribed_clients : clients currently connected to /ws/soccer
    """
    return JSONResponse(content=soccer_ws_service.status)
