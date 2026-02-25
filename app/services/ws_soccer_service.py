"""
WebSocket service that bridges Goalserve's live soccer WebSocket feed
(ws://live.goalserve.com/ws/soccer?tkn=...) to connected API clients.

Architecture:
  - GoalserveWSClient  : maintains one persistent upstream connection; auto-reconnects
  - ConnectionManager  : fan-out broadcaster to all subscribed FastAPI WebSocket clients
  - parse_event        : normalises raw Goalserve JSON into a clean structure

The singleton `soccer_ws_service` is the single entry-point used by the rest of
the app (startup, shutdown, endpoint handler).
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

import websockets
from fastapi import WebSocket, WebSocketDisconnect
from websockets.exceptions import ConnectionClosed, WebSocketException

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stat name normalisation map  (Goalserve key → friendly key)
# ---------------------------------------------------------------------------
_STAT_NAME_MAP: Dict[str, str] = {
    "ITeam": "team",
    "IGoal": "goals",
    "ICorner": "corners",
    "IYellowCard": "yellow_cards",
    "IRedCard": "red_cards",
    "IThrowIn": "throw_ins",
    "IFreeKick": "free_kicks",
    "IGoalKick": "goal_kicks",
    "IPenalty": "penalties",
    "ISubstitution": "substitutions",
    "IAttacks": "attacks",
    "IDangerousAttacks": "dangerous_attacks",
    "IOnTarget": "shots_on_target",
    "IOffTarget": "shots_off_target",
    "IPosession": "possession",
}


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_event(event_id: str, event_data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise a single Goalserve event dict into a clean structure."""
    info = event_data.get("info") or {}
    team_info = event_data.get("team_info") or {}
    core = event_data.get("core") or {}
    raw_stats = event_data.get("stats") or {}
    raw_extra = event_data.get("extra") or {}

    # ---- stats ----
    stats: Dict[str, Any] = {}
    for _, stat in raw_stats.items():
        if not isinstance(stat, dict):
            continue
        raw_name = stat.get("name", "")
        key = _STAT_NAME_MAP.get(raw_name, raw_name.lstrip("I").lower())
        home_val = stat.get("home")
        away_val = stat.get("away")
        # store as int when both sides are numeric, otherwise as-is
        if str(home_val).lstrip("-").isdigit() and str(away_val).lstrip("-").isdigit():
            stats[key] = {"home": _safe_int(home_val), "away": _safe_int(away_val)}
        else:
            stats[key] = {"home": home_val, "away": away_val}

    # ---- timeline events ----
    timeline = []
    for _, entry in raw_extra.items():
        if isinstance(entry, dict) and entry.get("value"):
            timeline.append({
                "code": entry.get("code"),
                "minute": _safe_int(entry.get("minute")),
                "description": entry.get("value"),
            })

    home_info = team_info.get("home") or {}
    away_info = team_info.get("away") or {}

    return {
        "event_id": event_id,
        "match": {
            "id": info.get("id"),
            "mid": info.get("mid"),
            "name": info.get("name"),
            "sport": info.get("sport"),
            "league": info.get("league"),
            "start_time": info.get("start_time"),
            "start_date": info.get("start_date"),
            "period": info.get("period"),
            "score": info.get("score"),
            "minute": info.get("minute"),
            "seconds": info.get("seconds"),
            "state": info.get("state"),
            "ball_pos": info.get("ball_pos"),
            "state_info": info.get("state_info"),
        },
        "home_team": {
            "name": home_info.get("name"),
            "score": _safe_int(home_info.get("score")),
            "kit_color": home_info.get("kit_color"),
        },
        "away_team": {
            "name": away_info.get("name"),
            "score": _safe_int(away_info.get("score")),
            "kit_color": away_info.get("kit_color"),
        },
        "core": {
            "stopped": core.get("stopped") == "1",
            "blocked": core.get("blocked") == "1",
            "finished": core.get("finished") == "1",
            "updated": core.get("updated"),
        },
        "stats": stats,
        "timeline": timeline,
    }


def parse_feed(raw: str) -> Optional[Dict[str, Any]]:
    """
    Parse a raw Goalserve WebSocket message.

    Returns a normalised dict:
        {
            "updated": "<timestamp>",
            "updated_ts": <int>,
            "total_events": <int>,
            "events": [ <parsed_event>, ... ]
        }
    or None if parsing fails.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("ws_soccer: JSON decode error: %s", exc)
        return None

    if not isinstance(data, dict):
        return None

    raw_events: Dict[str, Any] = data.get("events") or {}
    events = [
        parse_event(eid, edata)
        for eid, edata in raw_events.items()
        if isinstance(edata, dict)
    ]

    return {
        "updated": data.get("updated"),
        "updated_ts": data.get("updated_ts"),
        "total_events": len(events),
        "events": events,
    }


# ---------------------------------------------------------------------------
# Connection Manager  (fan-out to API clients)
# ---------------------------------------------------------------------------

class ConnectionManager:
    """Manages connected API WebSocket clients and broadcasts messages to them."""

    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        logger.info("ws_soccer: client connected  (total=%d)", len(self._clients))

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)
        logger.info("ws_soccer: client disconnected (total=%d)", len(self._clients))

    async def broadcast(self, message: str) -> None:
        """Send a text message to every connected client; silently drop dead connections."""
        dead: Set[WebSocket] = set()
        for ws in list(self._clients):
            try:
                await ws.send_text(message)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self._clients.discard(ws)

    @property
    def client_count(self) -> int:
        return len(self._clients)


# ---------------------------------------------------------------------------
# Goalserve upstream WebSocket client
# ---------------------------------------------------------------------------

class GoalserveWSClient:
    """
    Maintains a single persistent upstream WebSocket connection to Goalserve.
    Parses received frames and fans the result out via `ConnectionManager`.
    Auto-reconnects with exponential back-off on any error.
    """

    _MAX_BACKOFF: float = 60.0
    _BASE_BACKOFF: float = 2.0

    def __init__(self, sport: str, manager: ConnectionManager) -> None:
        self._sport = sport
        self._manager = manager
        self._url = (
            f"{settings.GOALSERVE_WS_BASE_URL}/{sport}"
            f"?tkn={settings.GOALSERVE_API_KEY}"
        )
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._connected = False
        self._last_message_at: Optional[datetime] = None
        self._reconnect_attempts = 0

    # ---- public interface ----

    def start(self) -> None:
        """Schedule the listener loop as an asyncio background task."""
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._listen_loop(), name=f"gs_ws_{self._sport}")
        logger.info("ws_soccer: upstream listener task started  url=%s", self._url)

    async def stop(self) -> None:
        """Cancel the listener task and wait for it to finish."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("ws_soccer: upstream listener task stopped")

    @property
    def status(self) -> Dict[str, Any]:
        return {
            "connected": self._connected,
            "sport": self._sport,
            "url": self._url,
            "reconnect_attempts": self._reconnect_attempts,
            "last_message_at": (
                self._last_message_at.isoformat() if self._last_message_at else None
            ),
            "subscribed_clients": self._manager.client_count,
        }

    # ---- internal ----

    async def _listen_loop(self) -> None:
        backoff = self._BASE_BACKOFF
        while self._running:
            try:
                await self._connect_and_receive()
                backoff = self._BASE_BACKOFF  # reset after clean disconnect
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._connected = False
                self._reconnect_attempts += 1
                logger.warning(
                    "ws_soccer: upstream error (%s). Reconnecting in %.0fs …",
                    exc, backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._MAX_BACKOFF)

        self._connected = False

    async def _connect_and_receive(self) -> None:
        logger.info("ws_soccer: connecting to %s", self._url)
        async with websockets.connect(
            self._url,
            ping_interval=30,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            self._connected = True
            self._reconnect_attempts = 0
            logger.info("ws_soccer: upstream connected")

            async for raw_message in ws:
                if not self._running:
                    break

                self._last_message_at = datetime.now(tz=timezone.utc)

                parsed = parse_feed(raw_message)
                if parsed is None:
                    continue

                if self._manager.client_count > 0:
                    await self._manager.broadcast(json.dumps(parsed))


# ---------------------------------------------------------------------------
# Singleton service facade
# ---------------------------------------------------------------------------

class SoccerWSService:
    """
    Top-level façade used by main.py (lifecycle) and ws_endpoints.py (connect/status).
    """

    def __init__(self) -> None:
        self.manager = ConnectionManager()
        self._client = GoalserveWSClient(sport="soccer", manager=self.manager)

    def start(self) -> None:
        self._client.start()

    async def stop(self) -> None:
        await self._client.stop()

    async def handle_client(self, ws: WebSocket) -> None:
        """Accept a client WebSocket connection and keep it alive until disconnected."""
        await self.manager.connect(ws)
        try:
            # Keep the connection open; we only send (broadcast), never expect client messages.
            # Reading here lets us detect a disconnect immediately.
            while True:
                await ws.receive_text()
        except (WebSocketDisconnect, ConnectionClosed):
            pass
        finally:
            self.manager.disconnect(ws)

    @property
    def status(self) -> Dict[str, Any]:
        return self._client.status


soccer_ws_service = SoccerWSService()
