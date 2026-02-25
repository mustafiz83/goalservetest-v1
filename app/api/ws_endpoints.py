"""
WebSocket and companion REST endpoints for the live soccer feed.

WebSocket endpoint:
    ws://<host>/ws/soccer
        Connect to receive a stream of live soccer match updates.
        Each frame is a JSON object with the shape:
        {
            "updated":      "<timestamp string>",
            "updated_ts":   <unix ms int>,
            "total_events": <int>,
            "events": [
                {
                    "event_id": "...",
                    "match": { "id", "name", "league", "period", "score", "minute", ... },
                    "home_team": { "name", "score", "kit_color" },
                    "away_team": { "name", "score", "kit_color" },
                    "core": { "stopped", "blocked", "finished", "updated" },
                    "stats": {
                        "goals":            { "home": int, "away": int },
                        "corners":          { "home": int, "away": int },
                        "yellow_cards":     { "home": int, "away": int },
                        "red_cards":        { "home": int, "away": int },
                        "possession":       { "home": int, "away": int },
                        "attacks":          { "home": int, "away": int },
                        "dangerous_attacks":{ "home": int, "away": int },
                        "shots_on_target":  { "home": int, "away": int },
                        "shots_off_target": { "home": int, "away": int },
                        ... (more stat keys)
                    },
                    "timeline": [ { "code", "minute", "description" }, ... ]
                },
                ...
            ]
        }

REST endpoints:
    GET /api/v1/ws/soccer/status   – connection health & metrics
"""

from fastapi import APIRouter, WebSocket
from fastapi.responses import JSONResponse

from app.services.ws_soccer_service import soccer_ws_service

router = APIRouter(tags=["soccer-websocket"])


@router.websocket("/ws/soccer")
async def ws_soccer(websocket: WebSocket) -> None:
    """
    Live soccer WebSocket feed.

    Connect and receive real-time match updates pushed from Goalserve.
    The upstream connection to Goalserve is shared; this endpoint fans
    every incoming frame out to all connected clients simultaneously.
    """
    await soccer_ws_service.handle_client(websocket)


@router.get("/api/v1/ws/soccer/status")
async def ws_soccer_status() -> JSONResponse:
    """
    Returns the health and metrics of the upstream Goalserve WebSocket connection.

    Response fields:
    - connected         : whether the upstream WS is currently open
    - sport             : sport type (always "soccer" here)
    - url               : upstream URL being used (token redacted)
    - reconnect_attempts: number of reconnection attempts since last clean connect
    - last_message_at   : ISO-8601 timestamp of the last received frame (or null)
    - subscribed_clients: number of API clients currently connected to /ws/soccer
    """
    status = soccer_ws_service.status
    # Redact the token from the URL in the response
    safe_url = status["url"].split("?")[0] + "?tkn=***"
    return JSONResponse(content={**status, "url": safe_url})
