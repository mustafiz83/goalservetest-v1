"""
Goalserve Inplay WebSocket service for soccer.

Authentication flow (per Goalserve docs):
  1. POST http://live.goalserve.com/api/v1/auth/gettoken
     Body: {"apiKey": "<GOALSERVE_API_KEY>"}
     Response: {"token": "<JWT>"}   (valid 60 min)

  2. Connect: ws://live.goalserve.com/ws/soccer?tkn=<JWT>

Message types (field "mt"):
  "avl"  – full snapshot of currently available live events
  "updt" – incremental update for a single event

Public surface used by the rest of the app:
  soccer_ws_service.start()              – called on FastAPI startup
  soccer_ws_service.stop()              – called on FastAPI shutdown
  soccer_ws_service.handle_client(ws)   – used by WS proxy endpoint
  soccer_ws_service.snapshot(timeout)  – used by REST test endpoint
  soccer_ws_service.status             – used by status endpoint
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import httpx
import websockets
from fastapi import WebSocket, WebSocketDisconnect
from websockets.exceptions import ConnectionClosed

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stat name normalisation  (Goalserve "I" prefix → friendly key)
# ---------------------------------------------------------------------------
_STAT_MAP: Dict[str, str] = {
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


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Token Manager  – fetches and caches the Goalserve JWT
# ---------------------------------------------------------------------------

class TokenManager:
    """
    Fetches a JWT from Goalserve auth endpoint and caches it.
    Automatically refreshes 5 minutes before the 60-minute expiry.
    """

    _TTL_SECONDS = 60 * 60      # tokens are valid 60 min
    _REFRESH_BUFFER = 5 * 60    # refresh 5 min before expiry

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._fetched_at: float = 0.0
        self._lock = asyncio.Lock()

    def _is_expired(self) -> bool:
        age = time.monotonic() - self._fetched_at
        return age >= (self._TTL_SECONDS - self._REFRESH_BUFFER)

    async def get_token(self) -> str:
        """Return a valid JWT, fetching/refreshing as needed."""
        async with self._lock:
            if self._token is None or self._is_expired():
                await self._fetch()
            return self._token  # type: ignore[return-value]

    async def invalidate(self) -> None:
        """Force a fresh token on the next call (e.g. after a 401)."""
        async with self._lock:
            self._token = None
            self._fetched_at = 0.0

    async def _fetch(self) -> None:
        logger.info("ws_soccer: fetching new auth token …")
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                settings.GOALSERVE_WS_AUTH_URL,
                json={"apiKey": settings.GOALSERVE_API_KEY},
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            payload = resp.json()

        token = payload.get("token")
        if not token:
            raise ValueError(f"Auth response missing 'token': {payload}")

        self._token = token
        self._fetched_at = time.monotonic()
        logger.info("ws_soccer: auth token obtained (valid ~60 min)")


token_manager = TokenManager()


# ---------------------------------------------------------------------------
# Parsers  –  avl / updt message handlers
# ---------------------------------------------------------------------------

def _parse_stats(raw_stats: Dict[str, Any]) -> Dict[str, Any]:
    stats: Dict[str, Any] = {}
    for _, stat in raw_stats.items():
        if not isinstance(stat, dict):
            continue
        raw_name = stat.get("name", "")
        key = _STAT_MAP.get(raw_name, raw_name.lstrip("I").lower())
        home_val = stat.get("home")
        away_val = stat.get("away")
        h_str = str(home_val).lstrip("-")
        a_str = str(away_val).lstrip("-")
        if h_str.isdigit() and a_str.isdigit():
            stats[key] = {"home": _safe_int(home_val), "away": _safe_int(away_val)}
        else:
            stats[key] = {"home": home_val, "away": away_val}
    return stats


def _parse_timeline(raw_extra: Dict[str, Any]) -> List[Dict[str, Any]]:
    timeline = []
    for _, entry in raw_extra.items():
        if isinstance(entry, dict) and entry.get("value"):
            timeline.append({
                "code": entry.get("code"),
                "minute": _safe_int(entry.get("minute")),
                "description": entry.get("value"),
            })
    return timeline


def _parse_event(event_id: str, event_data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise a single event dict."""
    info = event_data.get("info") or {}
    team_info = event_data.get("team_info") or {}
    core = event_data.get("core") or {}
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
        "stats": _parse_stats(event_data.get("stats") or {}),
        "timeline": _parse_timeline(event_data.get("extra") or {}),
    }


def _parse_avl(data: Dict[str, Any]) -> Dict[str, Any]:
    """Parse an 'avl' (available events snapshot) message."""
    raw_events: Dict[str, Any] = data.get("events") or {}
    events = [
        _parse_event(eid, edata)
        for eid, edata in raw_events.items()
        if isinstance(edata, dict)
    ]
    return {
        "mt": "avl",
        "updated": data.get("updated"),
        "updated_ts": data.get("updated_ts"),
        "total_events": len(events),
        "events": events,
    }


def _parse_updt(data: Dict[str, Any]) -> Dict[str, Any]:
    """Parse an 'updt' (single event update) message."""
    raw_events: Dict[str, Any] = data.get("events") or {}
    # updt usually contains a single event
    events = [
        _parse_event(eid, edata)
        for eid, edata in raw_events.items()
        if isinstance(edata, dict)
    ]
    return {
        "mt": "updt",
        "updated": data.get("updated"),
        "updated_ts": data.get("updated_ts"),
        "events": events,
    }


def parse_message(raw: str) -> Optional[Dict[str, Any]]:
    """
    Dispatch a raw Goalserve WS frame to the correct parser by 'mt' field.
    Returns None on parse failure.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("ws_soccer: JSON decode error: %s", exc)
        return None

    if not isinstance(data, dict):
        return None

    mt = data.get("mt")
    if mt == "avl":
        return _parse_avl(data)
    if mt == "updt":
        return _parse_updt(data)

    # Unknown / no mt field — attempt avl-style parse as fallback
    if "events" in data:
        return _parse_avl(data)

    logger.debug("ws_soccer: unrecognised message type: %s", mt)
    return None


# ---------------------------------------------------------------------------
# Connection Manager  –  fan-out broadcaster to API clients
# ---------------------------------------------------------------------------

class ConnectionManager:
    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        logger.info("ws_soccer: client connected (total=%d)", len(self._clients))

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)
        logger.info("ws_soccer: client disconnected (total=%d)", len(self._clients))

    async def broadcast(self, message: str) -> None:
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
# Goalserve upstream WS client  –  persistent listener with token auth
# ---------------------------------------------------------------------------

class GoalserveWSClient:
    _MAX_BACKOFF: float = 60.0
    _BASE_BACKOFF: float = 2.0

    def __init__(self, sport: str, manager: ConnectionManager) -> None:
        self._sport = sport
        self._manager = manager
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._connected = False
        self._last_message_at: Optional[datetime] = None
        self._reconnect_attempts = 0
        self._token_manager = token_manager

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(
            self._listen_loop(), name=f"gs_ws_{self._sport}"
        )
        logger.info("ws_soccer: upstream listener task started")

    async def stop(self) -> None:
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
            "upstream_url": f"{settings.GOALSERVE_WS_BASE_URL}/{self._sport}?tkn=***",
            "reconnect_attempts": self._reconnect_attempts,
            "last_message_at": (
                self._last_message_at.isoformat() if self._last_message_at else None
            ),
            "subscribed_clients": self._manager.client_count,
        }

    async def _listen_loop(self) -> None:
        backoff = self._BASE_BACKOFF
        while self._running:
            try:
                await self._connect_and_receive()
                backoff = self._BASE_BACKOFF
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
        token = await self._token_manager.get_token()
        url = f"{settings.GOALSERVE_WS_BASE_URL}/{self._sport}?tkn={token}"
        logger.info("ws_soccer: connecting upstream …")

        async with websockets.connect(
            url, ping_interval=30, ping_timeout=10, close_timeout=5
        ) as ws:
            self._connected = True
            self._reconnect_attempts = 0
            logger.info("ws_soccer: upstream connected")

            async for raw in ws:
                if not self._running:
                    break

                self._last_message_at = datetime.now(tz=timezone.utc)
                parsed = parse_message(raw)
                if parsed is None:
                    continue

                if self._manager.client_count > 0:
                    await self._manager.broadcast(json.dumps(parsed))


# ---------------------------------------------------------------------------
# One-shot snapshot  –  used by the REST test endpoint
# ---------------------------------------------------------------------------

async def fetch_snapshot(sport: str = "soccer", timeout: float = 20.0) -> Dict[str, Any]:
    """
    Connect to Goalserve WS, wait for the first 'avl' message, return it, disconnect.
    Raises asyncio.TimeoutError if no avl message arrives within `timeout` seconds.
    """
    token = await token_manager.get_token()
    url = f"{settings.GOALSERVE_WS_BASE_URL}/{sport}?tkn={token}"
    logger.info("ws_soccer: snapshot connect to %s", url.split("?")[0])

    async with websockets.connect(
        url, ping_interval=30, ping_timeout=10, close_timeout=5
    ) as ws:
        async def _receive_avl() -> Dict[str, Any]:
            async for raw in ws:
                parsed = parse_message(raw)
                if parsed and parsed.get("mt") == "avl":
                    return parsed
            raise RuntimeError("WebSocket closed before receiving avl message")

        return await asyncio.wait_for(_receive_avl(), timeout=timeout)


# ---------------------------------------------------------------------------
# Singleton service façade
# ---------------------------------------------------------------------------

class SoccerWSService:
    def __init__(self) -> None:
        self.manager = ConnectionManager()
        self._client = GoalserveWSClient(sport="soccer", manager=self.manager)

    def start(self) -> None:
        self._client.start()

    async def stop(self) -> None:
        await self._client.stop()

    async def handle_client(self, ws: WebSocket) -> None:
        """Accept a client WebSocket and keep it alive; messages are pushed via broadcast."""
        await self.manager.connect(ws)
        try:
            while True:
                await ws.receive_text()
        except (WebSocketDisconnect, ConnectionClosed):
            pass
        finally:
            self.manager.disconnect(ws)

    async def snapshot(self, timeout: float = 20.0) -> Dict[str, Any]:
        """One-shot: fetch the current avl snapshot and return it."""
        return await fetch_snapshot(sport="soccer", timeout=timeout)

    @property
    def status(self) -> Dict[str, Any]:
        return self._client.status


soccer_ws_service = SoccerWSService()
