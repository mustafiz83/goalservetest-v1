from typing import Any

from fastapi import APIRouter, HTTPException
from app.services.goalserve_service import fetch_and_process_heatmap, fetch_fixtures, fetch_match_positions
from app.services.position_estimation_service import estimate_player_positions

# Initialize the router with a common prefix
router = APIRouter(prefix="/api/v1")

@router.get("/fixtures/{league_id}")
@router.get("/fixtures/{league_id}/{season}")
async def get_fixtures(league_id: str, season: str | None = None):
    """
    Fetches and returns the list of fixtures for a given league ID, optionally filtered by season (e.g., 2009-2010).
    URL Paths: 
    - /api/v1/fixtures/{league_id} 
    - /api/v1/fixtures/{league_id}/{season}
    """
    data = await fetch_fixtures(league_id, season)
    
    if "error" in data:
        # Typically 404 or 500 for external service errors
        raise HTTPException(status_code=404, detail=data["error"])
        
    return data

@router.get("/match-positions/{league_id}/{match_id}")
@router.get("/match-positions/{league_id}/{match_id}/{season}")
async def get_match_positions(league_id: str, match_id: str, season: str | None = None):
    """
    Combined player & ball position endpoint.

    Returns three blocks for the requested match:

    **lineup**
    Formation, starters and substitutes for both teams — includes each player's
    tactical position code (`pos`), shirt number, and live stats (goals, assists,
    cards, minutes played). Source: `commentaries/{league_id}.xml`.

    **player_heatmap**
    Per-player accumulated pitch-coverage density map.
    Each player entry contains a `heatmap_data` list of `{x, y, value}` objects
    where `x`/`y` are pitch coordinates (0–100) and `value` is the visit frequency.
    This is **not** real-time — it shows where a player has been over the whole match.
    Source: `commentaries/{league_id}_heatmap.xml`. Top leagues only.

    **ball_position**
    Current live ball coordinates from the inplay feed.
    `raw` is `"x,y"` (normalised 0.0–1.0 **or** integer 0–100 depending on provider).
    `null` when the match is not currently in the live inplay feed.
    Source: `inplay.goalserve.com/inplay-soccer.gz`.
    """
    data = await fetch_match_positions(match_id, league_id, season)
    if "error" in data:
        raise HTTPException(status_code=500, detail=data["error"])
    return data


@router.get("/match-estimated-positions/{league_id}/{match_id}")
@router.get("/match-estimated-positions/{league_id}/{match_id}/{season}")
async def get_match_estimated_positions(league_id: str, match_id: str, season: str | None = None):
    """
    Estimated live player positions for a match.

    **No true real-time player tracking exists in the Goalserve API.**
    This endpoint combines four available signals to produce the best
    possible approximation:

    1. **Formation slot mapping** — each player's `formation_pos` is mapped
       to a canonical x,y coordinate on the pitch based on their formation.
    2. **Heatmap weighted centre-of-mass** — accumulated heatmap data gives
       a weighted average position for each player since kick-off (top leagues only).
    3. **Possession-aware shift** — the current state code is used to push the
       formation block forward or backward based on which team has the ball.
    4. **Ball proximity ranking** — Euclidean distance from each player's
       estimated position to the live ball is calculated; the nearest player
       is flagged as `likely_in_possession`.

    Each player entry includes:
    - `estimated_x`, `estimated_y` — position on a 0–100 pitch grid
    - `source` — `"formation"` | `"heatmap"` | `"blended"`
    - `confidence` — `"high"` | `"medium"` | `"low"`
    - `distance_to_ball` — Euclidean units from ball (if ball_pos available)
    - `likely_in_possession` — `true` for the single player nearest the ball

    **Accuracy:** ±10–25 pitch units typical. Suitable for visualisation
    only — not for broadcast or betting analytics.
    """
    raw = await fetch_match_positions(match_id, league_id, season)
    if "error" in raw:
        raise HTTPException(status_code=500, detail=raw["error"])

    lineup  = raw.get("lineup") or {}
    heatmap = raw.get("player_heatmap") or {}
    ball_raw = (raw.get("ball_position") or {}).get("raw")
    state_code = (lineup.get("home_team") or {}).get("state_code") or \
                 raw.get("state_code")

    estimated = estimate_player_positions(
        lineup=lineup,
        heatmap=heatmap,
        ball_pos_raw=ball_raw,
        state_code=state_code,
    )

    return {
        "match_id":  match_id,
        "league_id": league_id,
        "match_info": {
            "status":  lineup.get("status"),
            "minute":  lineup.get("minute"),
            "score":   lineup.get("score"),
        },
        "estimated_positions": estimated,
        "raw_ball_position": raw.get("ball_position"),
        "disclaimer": (
            "Positions are ESTIMATED from formation mapping, heatmap centre-of-mass, "
            "possession state, and ball proximity. Goalserve does not provide real-time "
            "player tracking data."
        ),
    }


@router.get("/heatmap/{league_id}/{match_id}")
@router.get("/heatmap/{league_id}/{match_id}/{season}") # Added new optional path for season
async def get_match_heatmap(league_id: str, match_id: str, season: str | None = None):
    """
    Fetches and returns processed heatmap data for a specific match.
    URL Paths: 
    - /api/v1/heatmap/{league_id}/{match_id}
    - /api/v1/heatmap/{league_id}/{match_id}/{season} (recommended for historical matches)
    """
    data = await fetch_and_process_heatmap(match_id, league_id, season) # Pass season
    print("After fetch")
    if "error" in data:
        error_msg = data["error"].lower()
        status_code = 404 if (
            "not found" in error_msg
            or "not yet available" in error_msg
            or "no live heatmap" in error_msg
            or "left the live" in error_msg
        ) else 500
        detail: Any = {
            "message": data["error"],
            "heatmap_live_match_ids": data.get("heatmap_live_match_ids", []),
        }
        raise HTTPException(status_code=status_code, detail=detail)

    return data


