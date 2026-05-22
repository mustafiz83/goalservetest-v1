import re
import time
import requests
from typing import Any, Dict, List, Optional, Set, Tuple
import datetime 
from collections import Counter # Use Counter for efficient frequency calculation
from app.core.config import settings
import json # Used for error printing/debugging

# 🚨 IMPORTANT: Replace "YOUR_API_KEY" with your actual Goalserve API key
API_KEY = settings.GOALSERVE_API_KEY
BASE_URL = "https://www.goalserve.com/getfeed/" 
REQUEST_TIMEOUT = settings.GOALSERVE_REQUEST_TIMEOUT_SECONDS

LEAGUE_DATA_CACHE: Dict[str, Dict[str, Any]] = {}
FIXTURES_CACHE: Dict[str, Dict[str, Any]] = {}  # cache_key -> fixtures payload
_LIVE_HEATMAP_IDS_CACHE: Dict[str, Dict[str, Any]] = {}  # league_id -> {ids, loaded_at}
_LIVE_HEATMAP_TTL = 30

_NEXT_MATCHES_CACHE: Dict[str, Dict[str, Any]] = {}
_NEXT_MATCHES_TTL = 900
_NEXT_MATCHES_FETCH_TIMEOUT = min(12, REQUEST_TIMEOUT)

_FINISHED_STATUSES = frozenset(
    {
        "FT",
        "AET",
        "FT_PEN",
        "PEN",
        "PST",
        "POSTPONED",
        "CANC",
        "CANCELLED",
        "ABD",
        "ABANDONED",
        "AWD",
        "WO",
        "WOFF",
    }
)


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


def _league_season_names(league_id: str) -> List[str]:
    """Season tokens from ``soccerfixtures/data/seasons`` (via league catalog cache)."""
    try:
        from app.services.league_catalog_service import get_league_by_id

        row = get_league_by_id(league_id)
        if row:
            return list(row.get("seasons_results") or [])
    except Exception:
        pass
    return []


def _season_candidates(season: str) -> List[str]:
    """Build Goalserve season path tokens to try (docs: ``1204-2009-2010`` or single year ``1081-2025``)."""
    season = season.strip()
    if not season:
        return []
    out: List[str] = []
    seen: set[str] = set()

    def add(s: str) -> None:
        s = s.strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)

    add(season)
    if "-" in season:
        left, right = season.split("-", 1)
        if re.fullmatch(r"\d{4}", left) and re.fullmatch(r"\d{4}", right):
            add(left)
            add(right)
        elif re.fullmatch(r"\d{4}", left) and re.fullmatch(r"\d{2}", right):
            add(f"{left}-{right}")
    return out


def _resolve_soccerhistory_season(league_id: str, season: str) -> Optional[str]:
    """
    Map UI season (e.g. ``2025-2026``) to a token Goalserve accepts for ``soccerhistory/leagueid/{id}-{token}``.
    Returns None → use current ``soccerfixtures/leagueid/{id}`` instead.
    """
    available = set(_league_season_names(league_id))
    if not available:
        return None

    for candidate in _season_candidates(season):
        if candidate in available:
            return candidate

    # Loose match (e.g. mapping ``2025/2026`` vs ``2025-2026``)
    norm = season.replace("/", "-").lower()
    for name in available:
        if name.replace("/", "-").lower() == norm:
            return name

    try:
        from app.services.league_catalog_service import get_league_by_id

        row = get_league_by_id(league_id) or {}
        current = (row.get("current_season") or "").strip()
        if current and (season == current or season in current or current in season):
            return None
    except Exception:
        pass

    return None


def _fixtures_feed_urls(league_id: str, season: Optional[str]) -> Tuple[List[str], Dict[str, Any]]:
    """
    Ordered URLs to try. Meta explains which feed is used (per goalserve-api-docs).
    """
    meta: Dict[str, Any] = {
        "season_requested": season,
        "season_resolved": None,
        "feed": "soccerfixtures",
    }
    current_url = f"{BASE_URL}{API_KEY}/soccerfixtures/leagueid/{league_id}?json=1"

    if not season:
        return [current_url], meta

    history_token = _resolve_soccerhistory_season(league_id, season)
    if history_token:
        meta["season_resolved"] = history_token
        meta["feed"] = "soccerhistory"
        history_url = (
            f"{BASE_URL}{API_KEY}/soccerhistory/leagueid/{league_id}-{history_token}?json=1"
        )
        return [history_url, current_url], meta

    meta["note"] = (
        f"Season '{season}' not in Goalserve history list for league {league_id}; "
        "using current soccerfixtures feed."
    )
    return [current_url], meta


def _fixture_week_nodes(tournament: Dict[str, Any]) -> List[Any]:
    """Goalserve uses ``week`` (most leagues) or ``stage`` (e.g. some Americas feeds)."""
    if not tournament:
        return []
    if tournament.get("match") is not None:
        match = tournament.get("match")
        return [{"match": [match] if not isinstance(match, list) else match}]
    for key in ("week", "stage"):
        node = tournament.get(key)
        if node is None:
            continue
        if not isinstance(node, list):
            return [node]
        return node
    return []


def _parse_fixtures_payload(data: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    tournament = _extract_tournament_root(data)
    league_name = tournament.get("@league", "Unknown League")
    weeks = _fixture_week_nodes(tournament)

    fixture_list: List[Dict[str, Any]] = []
    for week in weeks:
        matches = week.get("match", [])
        if not isinstance(matches, list):
            matches = [matches]

        for match in matches:
            match_id = _norm_match_id(match.get("@id"))
            localteam = match.get("localteam", {})
            visitorteam = match.get("visitorteam", {})

            if match_id:
                local_score = localteam.get("@ft_score") or localteam.get("@score", "")
                visitor_score = visitorteam.get("@ft_score") or visitorteam.get("@score", "")

                fixture_list.append({
                    "match_id": match_id,
                    "date": match.get("@date", "N/A"),
                    "time": match.get("@time", "N/A"),
                    "status": match.get("@status", "N/A"),
                    "localteam_name": localteam.get("@name", "N/A"),
                    "visitorteam_name": visitorteam.get("@name", "N/A"),
                    "localteam_score": local_score,
                    "visitorteam_score": visitor_score,
                    "display": (
                        f"{match.get('@date')} - {localteam.get('@name')} {local_score} "
                        f"vs {visitor_score} {visitorteam.get('@name')} ({match.get('@status')})"
                    ),
                })

    fixture_list.sort(
        key=lambda x: (
            datetime.datetime.strptime(x["date"], "%d.%m.%Y")
            if x["date"] != "N/A"
            else datetime.datetime.min
        )
    )
    return league_name, fixture_list


# --- ASYNC FUNCTION: Fetch and Process Fixtures ---
async def fetch_fixtures(league_id: str, season: str | None = None) -> Dict[str, Any]:
    """
    Fetches fixtures for a league.

    - Current season: ``soccerfixtures/leagueid/{id}`` (goalserve-api-docs).
    - Past seasons: ``soccerhistory/leagueid/{id}-{season}`` when that season exists in
      ``soccerfixtures/data/seasons`` — season token may be ``2009-2010`` or a single year ``2025``.
    - On history 404/500, falls back to current fixtures feed.
    """
    season_norm = season.strip() if season else None
    cache_key = f"{league_id}-{season_norm}" if season_norm else league_id
    if cache_key in FIXTURES_CACHE:
        payload = dict(FIXTURES_CACHE[cache_key])
        fixtures = list(payload.get("fixtures") or [])
        payload["fixtures"] = _annotate_fixtures_heatmap_availability(league_id, fixtures)
        payload["heatmap_live_match_ids"] = sorted(fetch_live_heatmap_match_ids(league_id))
        return payload

    urls, meta = _fixtures_feed_urls(league_id, season_norm)
    last_error: Optional[str] = None

    try:
        for idx, url in enumerate(urls):
            response = requests.get(url, timeout=REQUEST_TIMEOUT)
            if response.status_code >= 400:
                last_error = f"{response.status_code} for {url.split(API_KEY)[-1][:80]}"
                if idx < len(urls) - 1:
                    meta["fallback_from"] = meta.get("feed", "soccerhistory")
                    meta["feed"] = "soccerfixtures"
                    meta.setdefault(
                        "note",
                        "soccerhistory unavailable; loaded current season from soccerfixtures.",
                    )
                    continue
                return {
                    "error": (
                        f"Failed to fetch fixtures: {response.status_code} Server Error. "
                        f"Season '{season_norm}' may be invalid for league {league_id}. "
                        f"Check seasons on /leagues — use exact tokens (e.g. 2025 not 2025-2026 for some leagues)."
                    )
                }

            data = response.json()
            league_name, fixture_list = _parse_fixtures_payload(data)
            fixture_list = _annotate_fixtures_heatmap_availability(league_id, fixture_list)
            live_ids = sorted(fetch_live_heatmap_match_ids(league_id))

            payload = {
                "league_name": league_name,
                "fixtures": fixture_list,
                "heatmap_live_match_ids": live_ids,
                **meta,
            }
            FIXTURES_CACHE[cache_key] = payload
            return payload

        return {"error": f"Failed to fetch fixtures: {last_error or 'unknown'}"}

    except requests.exceptions.RequestException as e:
        print(f"Fixtures API Error: {e}")
        return {"error": f"Failed to fetch fixtures: {e}"}
    except Exception as e:
        print(f"Fixtures parse error: {e}")
        return {"error": f"Failed to parse fixtures: {e}"}


def _as_match_list(node: Any) -> List[Dict[str, Any]]:
    if not node:
        return []
    if isinstance(node, list):
        return [m for m in node if isinstance(m, dict)]
    if isinstance(node, dict):
        return [node]
    return []


def _parse_fixture_kickoff(date_str: str, time_str: str) -> Optional[datetime.datetime]:
    if not date_str or date_str == "N/A":
        return None
    try:
        kickoff = datetime.datetime.strptime(date_str.strip(), "%d.%m.%Y")
    except ValueError:
        return None
    if time_str and time_str != "N/A":
        try:
            parts = time_str.strip().split(":")
            if len(parts) >= 2:
                kickoff = kickoff.replace(hour=int(parts[0]), minute=int(parts[1]))
        except (ValueError, TypeError):
            pass
    return kickoff


def _fixture_is_live(status: str) -> bool:
    s = (status or "").strip().upper()
    if s in _FINISHED_STATUSES:
        return False
    if s.isdigit():
        return True
    return s in {"HT", "LIVE", "1H", "2H", "ET", "BREAK", "INT"}


def _fixture_is_upcoming_or_live(fixture: Dict[str, Any], now: datetime.datetime) -> bool:
    status = (fixture.get("status") or "").strip()
    if _fixture_is_live(status):
        return True
    if status.upper() in _FINISHED_STATUSES:
        return False
    kickoff = _parse_fixture_kickoff(
        fixture.get("date", ""), fixture.get("time", "")
    )
    if not kickoff:
        return False
    return kickoff >= now.replace(hour=0, minute=0, second=0, microsecond=0)


def pick_next_matches(
    fixtures: List[Dict[str, Any]], limit: int = 2
) -> List[Dict[str, Any]]:
    """Return up to ``limit`` live or next upcoming matches (current season list)."""
    now = datetime.datetime.now()
    rows: List[Dict[str, Any]] = []

    for f in fixtures:
        if not _fixture_is_upcoming_or_live(f, now):
            continue
        status = (f.get("status") or "").strip()
        is_live = _fixture_is_live(status)
        kickoff = _parse_fixture_kickoff(f.get("date", ""), f.get("time", ""))
        home = f.get("localteam_name", "?")
        away = f.get("visitorteam_name", "?")
        ls = f.get("localteam_score", "")
        vs = f.get("visitorteam_score", "")
        score = None
        if is_live or (ls or vs):
            if ls or vs:
                score = f"{ls}-{vs}"

        rows.append(
            {
                "match_id": f.get("match_id"),
                "date": f.get("date"),
                "time": f.get("time"),
                "status": status,
                "is_live": is_live,
                "home_team": home,
                "away_team": away,
                "score": score,
                "kickoff_sort": kickoff or datetime.datetime.max,
            }
        )

    rows.sort(key=lambda r: (0 if r["is_live"] else 1, r["kickoff_sort"]))
    out: List[Dict[str, Any]] = []
    for r in rows[:limit]:
        item = dict(r)
        item.pop("kickoff_sort", None)
        out.append(item)
    return out


def fetch_next_matches_for_league(league_id: str, limit: int = 2) -> List[Dict[str, Any]]:
    """Current-season ``soccerfixtures/leagueid/{id}`` → next/live matches (cached)."""
    lid = str(league_id).strip()
    now = time.time()
    cached = _NEXT_MATCHES_CACHE.get(lid)
    if cached and (now - cached.get("loaded_at", 0)) < _NEXT_MATCHES_TTL:
        return list(cached.get("matches") or [])[:limit]

    url = f"{BASE_URL}{API_KEY}/soccerfixtures/leagueid/{lid}?json=1"
    matches: List[Dict[str, Any]] = []
    try:
        response = requests.get(url, timeout=_NEXT_MATCHES_FETCH_TIMEOUT)
        if response.status_code < 400:
            data = response.json()
            _, fixture_list = _parse_fixtures_payload(data)
            matches = pick_next_matches(fixture_list, limit=limit)
    except Exception as exc:
        print(f"Next matches fetch error ({lid}): {exc}")
        if cached:
            return list(cached.get("matches") or [])[:limit]

    _NEXT_MATCHES_CACHE[lid] = {"matches": matches, "loaded_at": now}
    return matches


def fetch_live_heatmap_match_ids(league_id: str) -> Set[str]:
    """
    Match IDs currently present in ``commentaries/{league_id}_heatmap.xml`` (live only).
    See goalserve-api-docs: heatmap is not stored for finished historical fixtures.
    """
    lid = str(league_id).strip()
    now = time.time()
    cached = _LIVE_HEATMAP_IDS_CACHE.get(lid)
    if cached and (now - cached.get("loaded_at", 0)) < _LIVE_HEATMAP_TTL:
        return set(cached.get("ids") or [])

    ids: Set[str] = set()
    url = f"{BASE_URL}{API_KEY}/commentaries/{lid}_heatmap.xml?json=1"
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        if response.status_code < 400:
            data = response.json()
            tournament = (data.get("commentaries") or {}).get("tournament") or {}
            for m in _as_match_list(tournament.get("match")):
                mid = _norm_match_id(m.get("@id"))
                if mid:
                    ids.add(mid)
    except Exception as exc:
        print(f"Live heatmap IDs fetch error: {exc}")
        if cached:
            return set(cached.get("ids") or [])

    _LIVE_HEATMAP_IDS_CACHE[lid] = {"ids": ids, "loaded_at": now}
    return ids


def _league_has_heatmap_feed(league_id: str) -> bool:
    try:
        from app.services.league_catalog_service import get_league_by_id

        row = get_league_by_id(league_id)
        return bool(row and row.get("heatmap_league_id"))
    except Exception:
        return False


def _annotate_fixtures_heatmap_availability(
    league_id: str, fixture_list: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    if not _league_has_heatmap_feed(league_id):
        for f in fixture_list:
            f["heatmap_available"] = False
        return fixture_list

    live_ids = fetch_live_heatmap_match_ids(league_id)
    for f in fixture_list:
        mid = _norm_match_id(f.get("match_id"))
        f["heatmap_available"] = mid in live_ids
        if f["heatmap_available"]:
            f["display"] = f"{f.get('display', '')} [live heatmap]"
    return fixture_list


# --- ASYNC FUNCTION: Fetch and Process Heatmap Data ---
async def fetch_and_process_heatmap(match_id: str, league_id: str, season: str | None = None) -> Dict[str, Any]:
    """
    Player heatmap from Goalserve ``commentaries/{league_id}_heatmap.xml`` (live matches only).
    Finished/historical fixtures from soccerhistory do not include heatmap data.
    """
    mid = _norm_match_id(match_id)

    league_details = fetch_league_data(league_id)
    if "error" in league_details:
        return league_details

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
                f"{f' (season {season})' if season else ''}."
            )
        }

    live_ids = fetch_live_heatmap_match_ids(league_id)
    if mid not in live_ids:
        ids_hint = ", ".join(sorted(live_ids)[:12]) if live_ids else "(none right now)"
        return {
            "error": (
                f"No live heatmap for match {match_id}. Goalserve only publishes heatmaps in "
                f"commentaries/{league_id}_heatmap.xml for matches currently in that live feed — "
                f"not for finished games from season/history fixtures. "
                f"Live heatmap match IDs now: {ids_hint}."
            ),
            "heatmap_live_match_ids": sorted(live_ids),
            "match_status": target_fixture.get("status"),
            "match_date": target_fixture.get("date"),
        }

    url = f"{BASE_URL}{API_KEY}/commentaries/{league_id}_heatmap.xml?json=1"

    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()

        tournament = (data.get("commentaries") or {}).get("tournament") or {}
        target_match = next(
            (m for m in _as_match_list(tournament.get("match")) if _norm_match_id(m.get("@id")) == mid),
            None,
        )

        if not target_match:
            return {
                "error": f"Match {match_id} left the live heatmap feed. Refresh fixtures and pick a live match.",
                "heatmap_live_match_ids": sorted(live_ids),
            }

        heatmaps = target_match.get("heatmaps", {})
        local_team_data = process_team_heatmaps(heatmaps.get("localteam", {}))
        visitor_team_data = process_team_heatmaps(heatmaps.get("visitorteam", {}))

        if not local_team_data and not visitor_team_data:
            return {
                "error": (
                    f"Match {match_id} is in the live feed but has no player heatmap points yet "
                    "(match may be pre-kickoff or data not populated)."
                ),
                "heatmap_live_match_ids": sorted(live_ids),
            }

        match_status = target_match.get("@status", target_fixture["status"])
        live_score_str = target_match.get("@score")
        if live_score_str:
            final_score = live_score_str.replace(" - ", "-")
        else:
            final_score = (
                f"{target_fixture['localteam_score']}-{target_fixture['visitorteam_score']}"
            )

        live_minute = target_match.get("@minute", "N/A")

        def enrich_player_data(player_data_map: Dict[str, Any]) -> Dict[str, Any]:
            enriched = {}
            for player_id, pdata in player_data_map.items():
                name = league_details["all_players"].get(player_id, f"Player ID {player_id}")
                pdata["name"] = name
                enriched[player_id] = pdata
            return enriched

        return {
            "match_id": mid,
            "match_date": target_fixture["date"],
            "league_name": league_details["league_name"],
            "localteam_name": target_fixture["localteam_name"],
            "visitorteam_name": target_fixture["visitorteam_name"],
            "final_score": final_score,
            "live_minute": live_minute,
            "match_status": match_status,
            "heatmap_source": "commentaries_live_heatmap",
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
        r = requests.get(settings.inplay_soccer_feed_url, timeout=settings.GOALSERVE_INPLAY_TIMEOUT_SECONDS, stream=True)
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