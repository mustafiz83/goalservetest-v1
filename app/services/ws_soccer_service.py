"""
Goalserve Inplay WebSocket service for soccer.
Reference: https://documentation.goalserve.com/v1/

Authentication flow:
  1. POST http://live.goalserve.com/api/v1/auth/gettoken
     Body: {"apiKey": "<GOALSERVE_API_KEY>"}
     Response: {"token": "<JWT>"}   (valid 60 min)

  2. Connect: ws://live.goalserve.com/ws/soccer?tkn=<JWT>

Message types (field "mt") — official format:

  "avl"  – available events list.
           Shape: { mt, sp, dt, bm, evts: [{id, mid, cmp_id, cmp_name, t1, t2, pc, fi}] }
           NOTE: avl is a LIGHTWEIGHT list (team names + competition only).
                 It does NOT carry match stats, score, or ball position.

  "updt" – full update for ONE event.
           Shape: { mt, sp, id, mid, cmp_id, cmp_name, t1, t2,
                    et, stp, bl, xy, pc, sc,
                    cms, stats, stat, odds }
           KEY FIELD:  xy  – "x,y" ball coordinates (string | null)
                       This is the live ball position for that event.

Ball position fields by API:
  REST  inplay feed  → info.ball_pos  ("x,y" string or null)
  WS    updt message → xy             ("x,y" string or null)

Player x/y coordinates: NOT available in any Goalserve endpoint.
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
# Stat name maps
# ---------------------------------------------------------------------------

# WebSocket updt compact stat codes  (official docs)
_WS_STAT_MAP: Dict[str, str] = {
    "c":  "corners",
    "f":  "free_kicks",
    "o":  "offsides",
    "p":  "penalties",
    "r":  "red_cards",
    "t":  "throw_ins",
    "y":  "yellow_cards",
    "s":  "substitutions",
    "g":  "goal_kicks",
    "a":  "goals",
    "h1": "first_half_score",
}

# REST inplay feed stat names ("I" prefix style) – kept for fallback
_REST_STAT_MAP: Dict[str, str] = {
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
        async with httpx.AsyncClient(timeout=settings.GOALSERVE_WS_AUTH_TIMEOUT_SECONDS) as client:
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

def _kit(kit_obj: Any) -> Optional[str]:
    """Extract comma-separated shirt colors from a kit object."""
    if isinstance(kit_obj, dict):
        return kit_obj.get("si")
    return None


def _team_brief(team_obj: Any) -> Dict[str, Any]:
    """Parse a t1/t2 object from WS messages."""
    if not isinstance(team_obj, dict):
        return {}
    return {
        "name": team_obj.get("n"),
        "kit_colors": _kit(team_obj.get("kit")),
        "kit_shorts": (team_obj.get("kit") or {}).get("so"),
    }


# ---------------------------------------------------------------------------
# avl parser
# ---------------------------------------------------------------------------

def _parse_avl(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse an 'avl' message (official WS format).

    avl is a LIGHTWEIGHT available-events list — team names + competition only.
    It does NOT carry match stats, score, or ball position.
    Full event data arrives in subsequent 'updt' messages.

    Official shape:
      { mt, sp, dt, bm, evts: [{id, mid, cmp_id, cmp_name, t1, t2, pc, fi}] }
    """
    raw_evts = data.get("evts") or []
    if isinstance(raw_evts, dict):
        raw_evts = list(raw_evts.values())

    events = []
    for evt in raw_evts:
        if not isinstance(evt, dict):
            continue
        events.append({
            "event_id": evt.get("id"),
            "mid": evt.get("mid"),
            "competition": {
                "id": evt.get("cmp_id"),
                "name": evt.get("cmp_name"),
            },
            "home_team": _team_brief(evt.get("t1")),
            "away_team": _team_brief(evt.get("t2")),
            "period_code": evt.get("pc"),
            "provider_event_id": evt.get("fi"),
        })

    return {
        "mt": "avl",
        "sport": data.get("sp"),
        "updated": data.get("dt"),
        "bookmaker": data.get("bm"),
        "total_events": len(events),
        "events": events,
    }


# ---------------------------------------------------------------------------
# updt parser
# ---------------------------------------------------------------------------

def _parse_ws_stats(raw: Any) -> Dict[str, Any]:
    """
    Parse compact WS stats object, e.g. {"c": [1,2], "y": [0,1], ...}
    Each value is [home, away].
    """
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    for code, value in raw.items():
        key = _WS_STAT_MAP.get(code, code)
        if isinstance(value, list) and len(value) == 2:
            out[key] = {"home": value[0], "away": value[1]}
        else:
            out[key] = value
    return out


def _parse_ws_timeline(raw: Any) -> List[Dict[str, Any]]:
    """
    Parse cms (comments) array from updt messages.
    Shape: [{id, mt, p, tm, n}]
    """
    if not isinstance(raw, list):
        return []
    timeline = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        timeline.append({
            "id": entry.get("id"),
            "code": entry.get("mt"),
            "period": entry.get("p"),
            "time_seconds": entry.get("tm"),
            "description": entry.get("n"),
        })
    return timeline


def _parse_updt(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse an 'updt' message — full event update for a single match.

    Official shape:
      { mt, sp, ctry_id, bm, st, uptd, pt,
        id, mid, cmp_id, cmp_name,
        t1, t2,
        et, stp, bl,
        xy,       ← BALL POSITION ("x,y" string or null)
        pc, sc,
        cms, stats, stat, odds }

    Ball position note:
      xy is the real-time ball x,y coordinate pair (same as ball_pos in REST).
      Values are floats in range ~0.0–1.0 representing pitch percentage.
      null when tracking data is unavailable for the event.
    """
    t1 = data.get("t1") or {}
    t2 = data.get("t2") or {}

    xy_raw: Optional[str] = data.get("xy")
    ball_pos: Optional[Dict[str, Any]] = None
    if xy_raw:
        parts = xy_raw.split(",")
        if len(parts) == 2:
            try:
                ball_pos = {"x": float(parts[0]), "y": float(parts[1]), "raw": xy_raw}
            except ValueError:
                ball_pos = {"raw": xy_raw}

    return {
        "mt": "updt",
        "event_id": data.get("id"),
        "mid": data.get("mid"),
        "sport": data.get("sp"),
        "bookmaker": data.get("bm"),
        "competition": {
            "id": data.get("cmp_id"),
            "name": data.get("cmp_name"),
        },
        "home_team": _team_brief(t1),
        "away_team": _team_brief(t2),
        "match": {
            "updated": data.get("uptd"),
            "start_ts": data.get("st"),
            "elapsed_seconds": data.get("et"),
            "period_code": data.get("pc"),
            "state_code": data.get("sc"),
            "stopped": bool(data.get("stp")),
            "blocked": bool(data.get("bl")),
        },
        "ball_position": ball_pos,
        "stats": _parse_ws_stats(data.get("stats")),
        "timeline": _parse_ws_timeline(data.get("cms")),
    }


def parse_message(raw: str) -> Optional[Dict[str, Any]]:
    """
    Dispatch a raw Goalserve WS frame to the correct parser.
    Returns None on parse failure or unknown message type.
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
        self._last_broadcast_payload: Optional[str] = None

    @property
    def last_broadcast_payload(self) -> Optional[str]:
        """Most recent parsed upstream frame (JSON string), for new client handoff."""
        return self._last_broadcast_payload

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

                payload = json.dumps(parsed)
                self._last_broadcast_payload = payload
                if self._manager.client_count > 0:
                    await self._manager.broadcast(payload)


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
        last = self._client.last_broadcast_payload
        if last:
            try:
                await ws.send_text(last)
            except Exception:
                pass
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
