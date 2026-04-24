import requests
from typing import Any, Dict, List
import datetime 
from collections import Counter # Use Counter for efficient frequency calculation
from app.core.config import settings
import json # Used for error printing/debugging

# 🚨 IMPORTANT: Replace "YOUR_API_KEY" with your actual Goalserve API key
API_KEY = settings.GOALSERVE_API_KEY
BASE_URL = "https://www.goalserve.com/getfeed/" 
REQUEST_TIMEOUT = 30 

LEAGUE_DATA_CACHE: Dict[str, Dict[str, Any]] = {}
FIXTURES_CACHE: Dict[str, List[Dict[str, Any]]] = {} # Cache for fixtures


def _norm_match_id(value: Any) -> str:
    """Normalize IDs: path params are str; Goalserve JSON may use int or str for @id / mid."""
    if value is None:
        return ""
    return str(value).strip()


def _extract_tournament_root(data: Dict[str, Any]) -> Dict[str, Any]:
    """soccerhistory → results.tournament; soccerfixtures JSON often uses fixtures.tournament."""
    if not isinstance(data, dict):
        return {}
    for top in ("results", "fixtures"):
        block = data.get(top)
        if isinstance(block, dict):
            tour = block.get("tournament")
            if isinstance(tour, dict):
                return tour
    tour = data.get("tournament")
    return tour if isinstance(tour, dict) else {}


# --- Helper functions (kept outside of API fetch for readability) ---

def parse_heatmap_string(heatmap_string: str) -> List[Dict[str, Any]]:
    """
    Parses the raw pipe-separated heatmap string ("x=X;y=Y|...") from the '@heatmap' attribute 
    and calculates the frequency of each (x, y) point for heatmap intensity.
    """
    if not heatmap_string:
        return []

    points = heatmap_string.split('|')
    # List to hold (x, y) tuples for frequency counting
    coordinate_list = [] 

    for point_str in points:
        try:
            # Clean up the string (e.g., remove trailing '|' which can result in an empty string)
            if not point_str.strip():
                continue
                
            parts = point_str.split(';')
            # Extract X value (e.g., "x=4")
            x = int(parts[0].split('=')[1])
            # Extract Y value (e.g., "y=50")
            y = int(parts[1].split('=')[1])
            
            coordinate_list.append((x, y))
            
        except (IndexError, ValueError, TypeError):
            # Skip malformed points
            continue
            
    # Calculate frequency of each unique (x, y) pair
    frequency_map = Counter(coordinate_list)
            
    # Convert frequency map into the list format expected by the frontend
    data = []
    for (x, y), value in frequency_map.items():
        data.append({'x': x, 'y': y, 'value': value})
        
    return data

def process_team_heatmaps(team_heatmaps: Dict[str, Any]) -> Dict[str, Any]:
    """
    Processes the 'heatmaps' structure for a single team, reading the raw heatmap string 
    from the '@heatmap' attribute.
    """
    player_map = {}
    players = team_heatmaps.get('player', [])
    if not isinstance(players, list):
        # Handle case where only one player exists (comes as a dict)
        players = [players] 
        
    for player in players:
        player_id = player.get('@id')
        # CRITICAL FIX: Read the raw string from the '@heatmap' attribute
        raw_heatmap_string = player.get('@heatmap') 
        
        if player_id and raw_heatmap_string:
            player_map[player_id] = {
                # Use the updated parser function
                'heatmap_data': parse_heatmap_string(raw_heatmap_string) 
            }
            
    return player_map


def fetch_league_data(league_id: str) -> Dict[str, Any]:
    """Fetches team and player names for a given league (with caching and robust parsing)."""
    
    if league_id in LEAGUE_DATA_CACHE:
        return LEAGUE_DATA_CACHE[league_id]

    url = f"{BASE_URL}{API_KEY}/soccerleague/{league_id}?json=1"
    
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT) 
        response.raise_for_status()
        data = response.json()
        
        league_info = data.get('league', {})
        details = {
            'league_name': league_info.get('@name', 'Unknown League'),
            'all_players': {}
        }
        
        teams = league_info.get('team', [])
        if not isinstance(teams, list):
            teams = [teams]
            
        for team in teams:
            squad = team.get('squad', {})
            players = squad.get('player', [])
            
            if not isinstance(players, list):
                players = [players]

            for player in players:
                player_id = player.get('@id')
                player_name = player.get('@name') 
                
                if player_id: 
                    details['all_players'][player_id] = player_name or f"Player ID {player_id}"
                
        LEAGUE_DATA_CACHE[league_id] = details 
        return details

    except requests.exceptions.RequestException as e:
        print(f"League data API Error (Timeout={REQUEST_TIMEOUT}s): {e}")
        return {"error": f"Failed to fetch league data: {e}"}
    except Exception as e:
        print(f"Error processing Goalserve league data: {e}")
        return {"error": f"Error processing Goalserve league data: {e}"}


# --- ASYNC FUNCTION: Fetch and Process Fixtures ---
async def fetch_fixtures(league_id: str, season: str | None = None) -> Dict[str, Any]:
    """Fetches and processes fixtures for the League ID, optionally filtered by season (e.g., 2009-2010)."""
    
    cache_key = f"{league_id}-{season}" if season else league_id
    if cache_key in FIXTURES_CACHE:
        return {"fixtures": FIXTURES_CACHE[cache_key]}

    if season:
        # Use soccerhistory feed for past seasons
        url = f"{BASE_URL}{API_KEY}/soccerhistory/leagueid/{league_id}-{season}?json=1"
    else:
        # Use soccerfixtures feed for current or future fixtures
        url = f"{BASE_URL}{API_KEY}/soccerfixtures/leagueid/{league_id}?json=1"

    
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()

        tournament = _extract_tournament_root(data)
        
        league_name = tournament.get('@league', 'Unknown League')
        
        # Check if the tournament object is directly the match container (for single match feeds), otherwise look for 'week'
        if 'match' in tournament and not isinstance(tournament.get('match'), list):
             # Handle case where tournament directly contains a single match (unlikely for fixtures, but safe check)
             weeks = [{"match": [tournament.get('match')]}]
        elif 'week' in tournament:
             weeks = tournament.get('week', [])
        else:
             weeks = []

        if not isinstance(weeks, list):
            weeks = [weeks]
        
        fixture_list = []
        for week in weeks:
            matches = week.get('match', [])
            if not isinstance(matches, list):
                matches = [matches]

            for match in matches:
                match_id = _norm_match_id(match.get('@id'))
                localteam = match.get('localteam', {})
                visitorteam = match.get('visitorteam', {})
                
                if match_id:
                    # Score extraction needs to be robust for past games (FT score)
                    local_score = localteam.get('@ft_score') or localteam.get('@score', '')
                    visitor_score = visitorteam.get('@ft_score') or visitorteam.get('@score', '')

                    fixture_list.append({
                        "match_id": match_id,
                        "date": match.get('@date', 'N/A'),
                        "time": match.get('@time', 'N/A'),
                        "status": match.get('@status', 'N/A'),
                        "localteam_name": localteam.get('@name', 'N/A'),
                        "visitorteam_name": visitorteam.get('@name', 'N/A'),
                        "localteam_score": local_score,
                        "visitorteam_score": visitor_score,
                        # Format for display:
                        "display": f"{match.get('@date')} - {localteam.get('@name')} {local_score} vs {visitor_score} {visitorteam.get('@name')} ({match.get('@status')})"
                    })

        # Sort by date (oldest first, setting 'N/A' dates to the minimum possible datetime)
        fixture_list.sort(key=lambda x: datetime.datetime.strptime(x['date'], "%d.%m.%Y") if x['date'] != 'N/A' else datetime.datetime.min)
        
        FIXTURES_CACHE[cache_key] = fixture_list
        
        return {"league_name": league_name, "fixtures": fixture_list}

    except requests.exceptions.RequestException as e:
        print(f"Fixtures API Error: {e}")
        return {"error": f"Failed to fetch fixtures: {e}"}


# --- ASYNC FUNCTION: Fetch and Process Heatmap Data ---
async def fetch_and_process_heatmap(match_id: str, league_id: str, season: str | None = None) -> Dict[str, Any]:
    """Fetches and combines heatmap data with player/team names and match details."""

    print("fetch_and_process_heatmap ")
    mid = _norm_match_id(match_id)

    # 1. Fetch League Data (for names)
    league_details = fetch_league_data(league_id)
    if 'error' in league_details:
        return league_details
        
    # Get the specific match details from the fixtures list for metadata consistency
    fixtures_response = await fetch_fixtures(league_id, season)
    if "error" in fixtures_response:
        return fixtures_response

    target_fixture = next(
        (
            f
            for f in fixtures_response.get("fixtures", [])
            if _norm_match_id(f.get("match_id")) == mid
        ),
        None,
    )

    if not target_fixture:
        return {
            "error": (
                f"Match ID {match_id} not found in the fixtures feed for league {league_id}"
                f"{f' (season {season})' if season else ''}. "
                "Check the match id, league id, and season string (e.g. 2025-2026 vs what Goalserve lists in soccerfixtures/data/seasons)."
            )
        }

    # 2. Fetch Heatmap Data 
    # NOTE: The heatmap feed URL typically uses the league ID and is NOT season-specific in the URL itself.
    url = f"{BASE_URL}{API_KEY}/commentaries/{league_id}_heatmap.xml?json=1" 
    
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        
        # Navigate to the match containing the heatmap data
        tournament = data.get('commentaries', {}).get('tournament', {})
        matches = tournament.get('match')
        if not isinstance(matches, list):
            matches = [matches]
        target_match = next((m for m in matches if _norm_match_id(m.get("@id")) == mid), None)

        if not target_match:
            # Heatmap data not available for this match
            return {"error": f"Heatmap data not yet available for Match ID {match_id}."}
            
        # Extract heatmap data points
        heatmaps = target_match.get('heatmaps', {})
        # Updated to use the new parsing logic that reads the @heatmap string
        local_team_data = process_team_heatmaps(heatmaps.get('localteam', {}))
        visitor_team_data = process_team_heatmaps(heatmaps.get('visitorteam', {}))
        
        # 3. Use Fixture data for most metadata, supplement with live data from heatmap feed
        match_status = target_match.get('@status', target_fixture['status'])
        
        # Determine score from the heatmap feed's match node, falling back to fixtures
        live_score_str = target_match.get('@score')
        if live_score_str:
             final_score = live_score_str.replace(' - ', '-')
        else:
             final_score = target_fixture['localteam_score'] + '-' + target_fixture['visitorteam_score']

        live_minute = target_match.get('@minute', 'N/A')
        
        # 4. Enrich heatmap data with player names
        def enrich_player_data(player_data_map):
            enriched = {}
            for player_id, data in player_data_map.items():
                name = league_details['all_players'].get(player_id, f"Player ID {player_id}")
                data['name'] = name
                enriched[player_id] = data
            return enriched

        return {
            "match_id": mid,
            "match_date": target_fixture['date'],
            "league_name": league_details['league_name'],
            "localteam_name": target_fixture['localteam_name'],
            "visitorteam_name": target_fixture['visitorteam_name'],
            "final_score": final_score,
            "live_minute": live_minute,
            "match_status": match_status,
            "localteam_players": enrich_player_data(local_team_data),
            "visitorteam_players": enrich_player_data(visitor_team_data),
        }

    except requests.exceptions.RequestException as e:
        print(f"Heatmap API Error: {e}")
        return {"error": f"Failed to fetch heatmap data: {e}"}


# ---------------------------------------------------------------------------
# Match Positions  –  heatmap + lineup + ball_pos in one response
# ---------------------------------------------------------------------------

def _parse_player_lineup(player_raw: Any) -> Dict[str, Any]:
    """Normalise a single player node from the commentaries lineup."""
    if not isinstance(player_raw, dict):
        return {}
    return {
        "id":        player_raw.get("@id"),
        "name":      player_raw.get("@name"),
        "number":    player_raw.get("@number"),
        "pos":       player_raw.get("@pos"),       # e.g. "GK", "CB", "CM", "ST"
        "formation_pos": player_raw.get("@formation_place"),  # grid slot in formation
        "goals":     player_raw.get("@goals"),
        "assists":   player_raw.get("@assists"),
        "yellow":    player_raw.get("@yellowcards"),
        "red":       player_raw.get("@redcards"),
        "minutes":   player_raw.get("@minutes_played"),
        "substitute": player_raw.get("@substitute") == "True",
    }


def _extract_lineup(team_node: Dict[str, Any]) -> Dict[str, Any]:
    """Pull lineup players and subs from a commentary team node."""
    player_raw = team_node.get("player", [])
    if not isinstance(player_raw, list):
        player_raw = [player_raw] if player_raw else []

    starters = [_parse_player_lineup(p) for p in player_raw if p.get("@substitute") != "True"]
    subs     = [_parse_player_lineup(p) for p in player_raw if p.get("@substitute") == "True"]

    return {
        "name":       team_node.get("@name"),
        "id":         team_node.get("@id"),
        "formation":  team_node.get("@formation"),
        "starters":   starters,
        "substitutes": subs,
    }


async def fetch_match_positions(match_id: str, league_id: str, season: str | None = None) -> Dict[str, Any]:
    """
    Combined endpoint: returns for a given match —
      • lineup (formation position + stats for each player)
      • player_heatmaps  (aggregated pitch coverage density per player)
      • ball_pos         (current live ball position from the inplay feed, if available)

    Sources:
      • commentaries/{league_id}.xml?json=1         → lineup
      • commentaries/{league_id}_heatmap.xml?json=1 → heatmap
      • inplay.goalserve.com/inplay-soccer.gz        → ball_pos (best-effort)
    """
    import gzip, io

    mid = _norm_match_id(match_id)

    # 1. Fetch commentary data (lineups + live stats)
    commentary_url = f"{BASE_URL}{API_KEY}/commentaries/{league_id}.xml?json=1"
    heatmap_url    = f"{BASE_URL}{API_KEY}/commentaries/{league_id}_heatmap.xml?json=1"

    lineup_data: Dict[str, Any] = {}
    heatmap_data: Dict[str, Any] = {}

    try:
        r = requests.get(commentary_url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        c_data = r.json()

        tournament = c_data.get("commentaries", {}).get("tournament", {})
        matches = tournament.get("match", [])
        if not isinstance(matches, list):
            matches = [matches] if matches else []

        target = next((m for m in matches if _norm_match_id(m.get("@id")) == mid), None)

        if target:
            local_node   = target.get("localteam", {})
            visitor_node = target.get("visitorteam", {})
            lineup_data = {
                "match_id":    mid,
                "status":      target.get("@status"),
                "minute":      target.get("@minute"),
                "score":       target.get("@score"),
                "home_team":   _extract_lineup(local_node),
                "away_team":   _extract_lineup(visitor_node),
            }
    except Exception as e:
        lineup_data = {"error": f"Commentary fetch failed: {e}"}

    # 2. Fetch heatmap data
    try:
        r = requests.get(heatmap_url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        h_data = r.json()

        tournament = h_data.get("commentaries", {}).get("tournament", {})
        matches = tournament.get("match", [])
        if not isinstance(matches, list):
            matches = [matches] if matches else []

        target = next((m for m in matches if _norm_match_id(m.get("@id")) == mid), None)

        if target:
            heatmaps_node = target.get("heatmaps", {})
            local_hm   = process_team_heatmaps(heatmaps_node.get("localteam", {}))
            visitor_hm = process_team_heatmaps(heatmaps_node.get("visitorteam", {}))

            # Enrich with player names from league roster
            league_details = fetch_league_data(league_id)
            all_players = league_details.get("all_players", {})

            def _enrich(hm_map):
                return {
                    pid: {**v, "name": all_players.get(pid, f"Player {pid}")}
                    for pid, v in hm_map.items()
                }

            heatmap_data = {
                "home_players": _enrich(local_hm),
                "away_players": _enrich(visitor_hm),
                "note": "Heatmap is accumulated pitch coverage — not real-time position",
            }
        else:
            heatmap_data = {"note": "Heatmap not yet available for this match"}

    except Exception as e:
        heatmap_data = {"error": f"Heatmap fetch failed: {e}"}

    # 3. Fetch live ball_pos from inplay feed (best-effort)
    ball_pos = None
    inplay_event_id = None
    try:
        r = requests.get(settings.inplay_soccer_feed_url, timeout=5, stream=True)
        r.raise_for_status()
        try:
            raw = gzip.decompress(r.content).decode("utf-8")
        except OSError:
            raw = r.text
        inplay_json = json.loads(raw)

        for eid, event in (inplay_json.get("events") or {}).items():
            info = event.get("info") or {}
            if _norm_match_id(info.get("mid")) == mid:
                ball_pos = info.get("ball_pos")
                inplay_event_id = eid
                break
    except Exception:
        pass  # ball_pos stays None — inplay not available or match not live

    return {
        "match_id":       mid,
        "league_id":      league_id,
        "lineup":         lineup_data,
        "player_heatmap": heatmap_data,
        "ball_position": {
            "raw":            ball_pos,
            "inplay_event_id": inplay_event_id,
            "note": (
                "Coordinates are 'x,y'. Format varies: normalised 0.0–1.0 OR integer 0–100 percentage. "
                "null = match not currently live in the inplay feed."
            ),
        },
    }