"""
Estimated Player Position Service
==================================
Derives approximate live player positions from available Goalserve data.

No true real-time player tracking exists in the Goalserve API.
This module combines three signals to produce the best possible estimate:

  Signal 1 – Formation slot mapping
    Each player has a formation_pos (slot index) and a formation string
    (e.g. "4-3-3"). We map each slot to a canonical x,y coordinate on a
    100×100 pitch grid.

  Signal 2 – Heatmap weighted centre-of-mass
    Accumulated heatmap data gives a weighted average position for each
    player since kick-off. More reliable for positionally disciplined
    players (defenders, GK) than for forwards who roam.

  Signal 3 – Possession-aware formation shift
    The state/sc code tells us which team is attacking. We shift the
    whole formation block forward/backward by a fixed offset to reflect
    pressing and defensive shape.

  Signal 4 – Ball proximity ranking
    With ball x,y from the inplay feed (ball_pos) or WebSocket (xy), we
    calculate distance from each player's estimated position to the ball
    and rank the nearest player as "likely in possession".

Output per player:
  estimated_x         – best-estimate x coordinate (0–100)
  estimated_y         – best-estimate y coordinate (0–100)
  source              – which signals contributed ("formation" | "heatmap" | "blended")
  confidence          – "high" | "medium" | "low"
  distance_to_ball    – Euclidean distance to current ball position (if available)
  likely_in_possession – True for the single player closest to the ball
"""

import math
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Pitch constants
# ---------------------------------------------------------------------------
PITCH_WIDTH  = 100   # x axis: left → right
PITCH_HEIGHT = 100   # y axis: top → bottom (home team attacks bottom→top)

# How far to shift the formation block when a team is in possession (0–100 units)
POSSESSION_PUSH = 10  # push forward
DEFENSIVE_DROP  = 5   # opposition drops back


# ---------------------------------------------------------------------------
# Formation slot → canonical (x, y) lookup tables
# ---------------------------------------------------------------------------
# All coordinates assume the HOME team attacks left→right (home GK on left).
# Away team coordinates are mirrored horizontally (x = 100 - x).
#
# Slot numbering: 1 = GK, then outfield left-to-right, back-to-front,
# matching Goalserve's formation_place attribute.

_FORMATION_SLOTS: Dict[str, List[Tuple[float, float]]] = {
    # slot index 0 is unused; index 1 = GK
    "4-4-2": [
        (0, 0),          # 0 unused
        (5,  50),        # 1 GK
        (25, 15),        # 2 RB
        (25, 37),        # 3 CB right
        (25, 63),        # 4 CB left
        (25, 85),        # 5 LB
        (50, 15),        # 6 RM
        (50, 37),        # 7 CM right
        (50, 63),        # 8 CM left
        (50, 85),        # 9 LM
        (75, 35),        # 10 ST right
        (75, 65),        # 11 ST left
    ],
    "4-3-3": [
        (0, 0),
        (5,  50),        # 1 GK
        (25, 15),        # 2 RB
        (25, 37),        # 3 CB right
        (25, 63),        # 4 CB left
        (25, 85),        # 5 LB
        (50, 25),        # 6 RM
        (50, 50),        # 7 CM
        (50, 75),        # 8 LM
        (75, 18),        # 9 RW
        (80, 50),        # 10 ST
        (75, 82),        # 11 LW
    ],
    "4-2-3-1": [
        (0, 0),
        (5,  50),        # 1 GK
        (25, 15),        # 2 RB
        (25, 37),        # 3 CB right
        (25, 63),        # 4 CB left
        (25, 85),        # 5 LB
        (42, 35),        # 6 DM right
        (42, 65),        # 7 DM left
        (60, 18),        # 8 RAM
        (60, 50),        # 9 CAM
        (60, 82),        # 10 LAM
        (78, 50),        # 11 ST
    ],
    "4-1-4-1": [
        (0, 0),
        (5,  50),
        (25, 15),
        (25, 37),
        (25, 63),
        (25, 85),
        (40, 50),        # 6 single DM
        (58, 15),
        (58, 38),
        (58, 62),
        (58, 85),
        (78, 50),
    ],
    "3-5-2": [
        (0, 0),
        (5,  50),
        (25, 25),
        (25, 50),
        (25, 75),
        (45, 10),        # 5 RWB
        (45, 32),
        (45, 50),
        (45, 68),
        (45, 90),        # 9 LWB
        (75, 35),
        (75, 65),
    ],
    "3-4-3": [
        (0, 0),
        (5,  50),
        (25, 25),
        (25, 50),
        (25, 75),
        (48, 15),
        (48, 42),
        (48, 58),
        (48, 85),
        (75, 18),
        (80, 50),
        (75, 82),
    ],
    "5-3-2": [
        (0, 0),
        (5,  50),
        (22, 10),
        (22, 30),
        (22, 50),
        (22, 70),
        (22, 90),
        (50, 25),
        (50, 50),
        (50, 75),
        (75, 35),
        (75, 65),
    ],
    "4-5-1": [
        (0, 0),
        (5,  50),
        (25, 15),
        (25, 37),
        (25, 63),
        (25, 85),
        (52, 10),
        (52, 30),
        (52, 50),
        (52, 70),
        (52, 90),
        (78, 50),
    ],
    "3-4-1-2": [
        (0, 0),
        (5,  50),
        (25, 25),
        (25, 50),
        (25, 75),
        (45, 15),
        (45, 38),
        (45, 62),
        (45, 85),
        (62, 50),
        (78, 35),
        (78, 65),
    ],
    "4-3-2-1": [
        (0, 0),
        (5,  50),
        (25, 15),
        (25, 37),
        (25, 63),
        (25, 85),
        (48, 25),
        (48, 50),
        (48, 75),
        (65, 35),
        (65, 65),
        (80, 50),
    ],
}

# Fallback: generic 4-4-2 for unknown formations
_FALLBACK_FORMATION = "4-4-2"


# ---------------------------------------------------------------------------
# State code → possession side mapping
# ---------------------------------------------------------------------------
# Goalserve state codes starting with "1" = home action, "2" = away action.
# Codes 11xxx / 21xxx are typically attack states.

def _possession_side(state_code: Optional[str]) -> Optional[str]:
    """Return 'home', 'away', or None from a Goalserve state code string."""
    if not state_code:
        return None
    s = str(state_code).strip()
    if s.startswith("1"):
        return "home"
    if s.startswith("2"):
        return "away"
    return None


# ---------------------------------------------------------------------------
# Heatmap centre-of-mass
# ---------------------------------------------------------------------------

def heatmap_centre(heatmap_points: List[Dict[str, Any]]) -> Optional[Tuple[float, float]]:
    """
    Compute weighted centre-of-mass from a player's heatmap data.

    heatmap_points: [{"x": int, "y": int, "value": int}, ...]
    Returns (x, y) in 0–100 space, or None if no data.
    """
    if not heatmap_points:
        return None
    total_weight = sum(p.get("value", 1) for p in heatmap_points)
    if total_weight == 0:
        return None
    cx = sum(p["x"] * p.get("value", 1) for p in heatmap_points) / total_weight
    cy = sum(p["y"] * p.get("value", 1) for p in heatmap_points) / total_weight
    return (round(cx, 1), round(cy, 1))


# ---------------------------------------------------------------------------
# Formation slot → pitch coordinates
# ---------------------------------------------------------------------------

def formation_xy(
    formation: Optional[str],
    slot: Optional[int],
    is_away: bool = False,
) -> Optional[Tuple[float, float]]:
    """
    Look up canonical (x, y) for a formation slot.

    formation : e.g. "4-3-3"
    slot      : formation_place integer (1 = GK)
    is_away   : mirror x axis (100 - x) for the away team
    """
    if not slot:
        return None

    # Normalise formation string
    fmt = (formation or "").strip()
    table = _FORMATION_SLOTS.get(fmt) or _FORMATION_SLOTS.get(_FALLBACK_FORMATION, [])

    if slot >= len(table):
        return None

    x, y = table[slot]
    if is_away:
        x = PITCH_WIDTH - x
    return (x, y)


# ---------------------------------------------------------------------------
# Possession shift
# ---------------------------------------------------------------------------

def apply_possession_shift(
    x: float,
    is_away: bool,
    possessing_side: Optional[str],
) -> float:
    """
    Nudge x-coordinate based on which team has possession.

    Home team attacks left→right (higher x = attacking).
    Away team attacks right→left (lower x = attacking).
    """
    if possessing_side is None:
        return x

    if possessing_side == "home":
        # Home has ball → home pushes right (+x), away drops (+x too, tracking)
        delta = POSSESSION_PUSH if not is_away else DEFENSIVE_DROP
        return min(x + delta, PITCH_WIDTH - 5)
    else:
        # Away has ball → away pushes left (-x), home drops
        delta = -POSSESSION_PUSH if is_away else -DEFENSIVE_DROP
        return max(x + delta, 5)


# ---------------------------------------------------------------------------
# Ball proximity
# ---------------------------------------------------------------------------

def euclidean(x1: float, y1: float, x2: float, y2: float) -> float:
    return round(math.hypot(x1 - x2, y1 - y2), 2)


def parse_ball_pos(raw: Optional[str]) -> Optional[Tuple[float, float]]:
    """
    Parse "x,y" ball position string into (x, y) floats on the 0–100 scale.

    Handles both formats:
      "0.32,0.97"  → normalised 0.0–1.0 → multiply by 100
      "32,97"      → already 0–100
    """
    if not raw:
        return None
    try:
        parts = raw.split(",")
        bx, by = float(parts[0]), float(parts[1])
        # If both values are ≤ 1.0 treat as normalised fractions
        if bx <= 1.0 and by <= 1.0:
            bx, by = bx * 100, by * 100
        return (round(bx, 1), round(by, 1))
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------------------
# Main estimation function
# ---------------------------------------------------------------------------

def estimate_player_positions(
    lineup: Dict[str, Any],
    heatmap: Dict[str, Any],
    ball_pos_raw: Optional[str],
    state_code: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Combine all available signals to estimate player positions.

    Parameters
    ----------
    lineup       : output of fetch_match_positions()["lineup"]
    heatmap      : output of fetch_match_positions()["player_heatmap"]
    ball_pos_raw : raw "x,y" string from ball_position.raw
    state_code   : current state/sc code string (for possession shift)

    Returns
    -------
    {
      "ball": {"x": ..., "y": ...} | null,
      "possession_side": "home" | "away" | null,
      "home_team": {
        "name": ...,
        "formation": ...,
        "players": [EstimatedPlayer, ...]
      },
      "away_team": { ... },
      "method_notes": [...]
    }

    EstimatedPlayer fields:
      id, name, pos, formation_pos,
      estimated_x, estimated_y,
      source,               "formation" | "heatmap" | "blended"
      confidence,           "high" | "medium" | "low"
      distance_to_ball,     float | null
      likely_in_possession  bool
    """
    ball_xy = parse_ball_pos(ball_pos_raw)
    possessing_side = _possession_side(state_code)
    method_notes: List[str] = []

    home_hm: Dict[str, Any] = heatmap.get("home_players") or {}
    away_hm: Dict[str, Any] = heatmap.get("away_players") or {}

    def _estimate_team(
        team_lineup: Dict[str, Any],
        hm_players: Dict[str, Any],
        is_away: bool,
    ) -> List[Dict[str, Any]]:
        formation = team_lineup.get("formation")
        players_out = []

        for player in team_lineup.get("starters", []):
            pid    = player.get("id") or ""
            slot_s = player.get("formation_pos")
            slot   = int(slot_s) if slot_s and str(slot_s).isdigit() else None

            # --- Signal 1: formation slot ---
            form_pos = formation_xy(formation, slot, is_away)

            # --- Signal 2: heatmap centre of mass ---
            hm_data  = (hm_players.get(pid) or {}).get("heatmap_data") or []
            heat_pos = heatmap_centre(hm_data)

            # --- Blend or choose ---
            if form_pos and heat_pos:
                # Blend 60% heatmap (reflects actual movement), 40% formation (structural anchor)
                bx = 0.4 * form_pos[0] + 0.6 * heat_pos[0]
                by = 0.4 * form_pos[1] + 0.6 * heat_pos[1]
                source     = "blended"
                confidence = "medium"
            elif heat_pos:
                bx, by     = heat_pos
                source     = "heatmap"
                confidence = "medium"
            elif form_pos:
                bx, by     = form_pos
                source     = "formation"
                confidence = "low"
            else:
                players_out.append({
                    **player,
                    "estimated_x": None,
                    "estimated_y": None,
                    "source": "unavailable",
                    "confidence": "none",
                    "distance_to_ball": None,
                    "likely_in_possession": False,
                })
                continue

            # --- Signal 3: possession shift ---
            bx = apply_possession_shift(bx, is_away, possessing_side)
            bx = round(max(0, min(PITCH_WIDTH,  bx)), 1)
            by = round(max(0, min(PITCH_HEIGHT, by)), 1)

            # Upgrade confidence if heatmap has many points
            if source in ("blended", "heatmap") and len(hm_data) >= 20:
                confidence = "high"

            # --- Signal 4: distance to ball ---
            dist = None
            if ball_xy:
                dist = euclidean(bx, by, ball_xy[0], ball_xy[1])

            players_out.append({
                **player,
                "estimated_x": bx,
                "estimated_y": by,
                "source":      source,
                "confidence":  confidence,
                "distance_to_ball": dist,
                "likely_in_possession": False,   # filled in below
            })

        return players_out

    home_lineup = lineup.get("home_team") or {}
    away_lineup = lineup.get("away_team") or {}

    home_players = _estimate_team(home_lineup, home_hm, is_away=False)
    away_players = _estimate_team(away_lineup, away_hm, is_away=True)

    # --- Mark likely-in-possession player (closest to ball) ---
    all_players = home_players + away_players
    if ball_xy:
        eligible = [p for p in all_players if p.get("distance_to_ball") is not None]
        if eligible:
            nearest = min(eligible, key=lambda p: p["distance_to_ball"])
            nearest["likely_in_possession"] = True

    # --- Build method notes ---
    hm_count = len(home_hm) + len(away_hm)
    if hm_count:
        method_notes.append(f"Heatmap data available for {hm_count} players — used as primary position signal.")
    else:
        method_notes.append("No heatmap data; positions derived from formation slot mapping only (low confidence).")

    if possessing_side:
        method_notes.append(
            f"Possession-aware shift applied: {possessing_side} team attacking "
            f"(formation pushed {POSSESSION_PUSH} units forward, opposition dropped {DEFENSIVE_DROP} units)."
        )

    if ball_xy:
        method_notes.append(f"Ball at ({ball_xy[0]}, {ball_xy[1]}); nearest player marked as likely_in_possession.")
    else:
        method_notes.append("Ball position unavailable — likely_in_possession not set.")

    method_notes.append(
        "These are ESTIMATES only. Accuracy: ±10–25 pitch units typical. "
        "Not suitable for broadcast or betting analytics."
    )

    return {
        "ball": {"x": ball_xy[0], "y": ball_xy[1]} if ball_xy else None,
        "possession_side": possessing_side,
        "home_team": {
            "name":      home_lineup.get("name"),
            "formation": home_lineup.get("formation"),
            "players":   home_players,
        },
        "away_team": {
            "name":      away_lineup.get("name"),
            "formation": away_lineup.get("formation"),
            "players":   away_players,
        },
        "method_notes": method_notes,
    }
