import re
import requests
from typing import Any, Dict, List, Optional, Set, Tuple
import datetime 
from collections import Counter # Use Counter for efficient frequency calculation
from app.core.config import settings
from app.services.goalserve_http_cache import GoalserveHttpError, fetch_goalserve_json
import json # Used for error printing/debugging

# 🚨 IMPORTANT: Replace "YOUR_API_KEY" with your actual Goalserve API key
API_KEY = settings.GOALSERVE_API_KEY
BASE_URL = "https://www.goalserve.com/getfeed/" 
REQUEST_TIMEOUT = settings.GOALSERVE_REQUEST_TIMEOUT_SECONDS

LEAGUE_DATA_CACHE: Dict[str, Dict[str, Any]] = {}
FIXTURES_CACHE: Dict[str, Dict[str, Any]] = {}  # cache_key -> fixtures payload
_FIXTURES_CACHE_VERSION = 2  # bump when payload shape changes
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

def process_team_heatmaps(team_heatmaps: Any) -> Dict[str, Any]:
    """
    Processes the 'heatmaps' structure for a single team, reading the raw heatmap string 
    from the '@heatmap' attribute.
    """
    if not isinstance(team_heatmaps, dict):
        return {}

    player_map = {}
    players = team_heatmaps.get("player") or []
    if not isinstance(players, list):
        players = [players] if players else []

    for player in players:
        if not isinstance(player, dict):
            continue
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
    lid = str(league_id).strip()
    cached = LEAGUE_DATA_CACHE.get(lid)
    if cached and isinstance(cached, dict) and "all_players" in cached:
        return cached
    if cached:
        del LEAGUE_DATA_CACHE[lid]

    try:
        data, _from_cache = fetch_goalserve_json(f"soccerleague/{lid}")

        league_info = data.get("league") if isinstance(data, dict) else None
        if not isinstance(league_info, dict):
            league_info = {}

        details = {
            "league_name": (league_info.get("@name") or "").strip() or "Unknown League",
            "all_players": {},
        }

        teams = league_info.get("team") or []
        if not isinstance(teams, list):
            teams = [teams] if teams else []

        for team in teams:
            if not isinstance(team, dict):
                continue
            squad = team.get("squad")
            if not isinstance(squad, dict):
                continue
            players = squad.get("player") or []
            if not isinstance(players, list):
                players = [players] if players else []

            for player in players:
                if not isinstance(player, dict):
                    continue
                player_id = player.get("@id")
                player_name = player.get("@name")

                if player_id:
                    details["all_players"][player_id] = player_name or f"Player ID {player_id}"

        LEAGUE_DATA_CACHE[lid] = details
        return details

    except GoalserveHttpError as e:
        print(f"League data API Error: {e}")
        return {
            "league_name": f"League {lid}",
            "all_players": {},
            "roster_warning": f"Failed to fetch league data: {e}",
        }
    except requests.exceptions.RequestException as e:
        print(f"League data API Error (Timeout={REQUEST_TIMEOUT}s): {e}")
        return {
            "league_name": f"League {lid}",
            "all_players": {},
            "roster_warning": f"Failed to fetch league data: {e}",
        }
    except Exception as e:
        print(f"Error processing Goalserve league data: {e}")
        return {
            "league_name": f"League {lid}",
            "all_players": {},
            "roster_warning": f"Error processing Goalserve league data: {e}",
        }


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


def _fixtures_feed_paths(league_id: str, season: Optional[str]) -> Tuple[List[str], Dict[str, Any]]:
    """
    Ordered feed paths to try. Meta explains which feed is used (per goalserve-api-docs).
    """
    meta: Dict[str, Any] = {
        "season_requested": season,
        "season_resolved": None,
        "feed": "soccerfixtures",
    }
    current_path = f"soccerfixtures/leagueid/{league_id}"

    if not season:
        return [current_path], meta

    history_token = _resolve_soccerhistory_season(league_id, season)
    if history_token:
        meta["season_resolved"] = history_token
        meta["feed"] = "soccerhistory"
        history_path = f"soccerhistory/leagueid/{league_id}-{history_token}"
        return [history_path, current_path], meta

    meta["note"] = (
        f"Season '{season}' not in Goalserve history list for league {league_id}; "
        "using current soccerfixtures feed."
    )
    return [current_path], meta


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
        cached = FIXTURES_CACHE[cache_key]
        if cached.get("_cache_v") == _FIXTURES_CACHE_VERSION:
            payload = dict(cached)
            fixtures = list(payload.get("fixtures") or [])
            fixtures, live_rows = _annotate_fixtures_heatmap_availability(league_id, fixtures)
            payload["fixtures"] = fixtures
            payload["heatmap_live_matches"] = _enrich_heatmap_live_matches(
                live_rows,
                fixtures,
                league_id=league_id,
                league_name=payload.get("league_name", ""),
                season_requested=season_norm,
                season_resolved=payload.get("season_resolved"),
                feed=payload.get("feed", "soccerfixtures"),
            )
            payload["heatmap_live_match_ids"] = sorted(
                m["match_id"] for m in payload["heatmap_live_matches"] if m.get("match_id")
            )
            return payload
        del FIXTURES_CACHE[cache_key]

    paths, meta = _fixtures_feed_paths(league_id, season_norm)
    last_error: Optional[str] = None

    try:
        for idx, feed_path in enumerate(paths):
            try:
                data, _from_cache = fetch_goalserve_json(feed_path)
            except GoalserveHttpError as err:
                last_error = f"{err.status_code} for {feed_path}"
                if idx < len(paths) - 1:
                    meta["fallback_from"] = meta.get("feed", "soccerhistory")
                    meta["feed"] = "soccerfixtures"
                    meta.setdefault(
                        "note",
                        "soccerhistory unavailable; loaded current season from soccerfixtures.",
                    )
                    continue
                return {
                    "error": (
                        f"Failed to fetch fixtures: {err.status_code} Server Error. "
                        f"Season '{season_norm}' may be invalid for league {league_id}. "
                        f"Check seasons on /leagues — use exact tokens (e.g. 2025 not 2025-2026 for some leagues)."
                    )
                }
            league_name, fixture_list = _parse_fixtures_payload(data)
            fixture_list, live_rows = _annotate_fixtures_heatmap_availability(
                league_id, fixture_list
            )
            heatmap_live_matches = _enrich_heatmap_live_matches(
                live_rows,
                fixture_list,
                league_id=league_id,
                league_name=league_name,
                season_requested=season_norm,
                season_resolved=meta.get("season_resolved"),
                feed=meta.get("feed", "soccerfixtures"),
            )

            payload = {
                "league_name": league_name,
                "fixtures": fixture_list,
                "heatmap_live_matches": heatmap_live_matches,
                "heatmap_live_match_ids": sorted(
                    m["match_id"] for m in heatmap_live_matches if m.get("match_id")
                ),
                **meta,
            }
            payload["_cache_v"] = _FIXTURES_CACHE_VERSION
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
    """Current-season ``soccerfixtures/leagueid/{id}`` → next/live matches (20s HTTP cache)."""
    lid = str(league_id).strip()
    try:
        data, _from_cache = fetch_goalserve_json(
            f"soccerfixtures/leagueid/{lid}",
            timeout=_NEXT_MATCHES_FETCH_TIMEOUT,
        )
        _, fixture_list = _parse_fixtures_payload(data)
        return pick_next_matches(fixture_list, limit=limit)
    except Exception as exc:
        print(f"Next matches fetch error ({lid}): {exc}")
        return []


def _parse_heatmap_feed_match_node(match: Dict[str, Any]) -> Dict[str, Any]:
    """One match row from ``commentaries/{league_id}_heatmap.xml``."""
    mid = _norm_match_id(match.get("@id"))
    local = match.get("localteam") or {}
    visitor = match.get("visitorteam") or {}
    if isinstance(local, list):
        local = local[0] if local else {}
    if isinstance(visitor, list):
        visitor = visitor[0] if visitor else {}

    home = (local.get("@name") or "Home").strip()
    away = (visitor.get("@name") or "Away").strip()
    score_raw = (match.get("@score") or "").strip()
    if score_raw:
        score = score_raw.replace(" - ", "-").replace(" ", "")
    else:
        lh = local.get("@score") or local.get("@ft_score") or ""
        va = visitor.get("@score") or visitor.get("@ft_score") or ""
        score = f"{lh}-{va}".strip("-") if lh or va else ""

    return {
        "match_id": mid,
        "date": (match.get("@date") or "").strip() or None,
        "time": (match.get("@time") or "").strip() or None,
        "status": (match.get("@status") or "").strip() or None,
        "minute": (match.get("@minute") or "").strip() or None,
        "home_team": home,
        "away_team": away,
        "score": score or None,
        "name": f"{home} vs {away}",
    }


def fetch_live_heatmap_matches(league_id: str) -> List[Dict[str, Any]]:
    """
    Live matches in ``commentaries/{league_id}_heatmap.xml`` with team names and status.
    """
    lid = str(league_id).strip()
    rows: List[Dict[str, Any]] = []
    try:
        data, _from_cache = fetch_goalserve_json(f"commentaries/{lid}_heatmap.xml")
        tournament = (data.get("commentaries") or {}).get("tournament") or {}
        league_from_feed = (tournament.get("@league") or "").strip()
        for m in _as_match_list(tournament.get("match")):
            row = _parse_heatmap_feed_match_node(m)
            if not row.get("match_id"):
                continue
            if league_from_feed:
                row["league_name_feed"] = league_from_feed
            rows.append(row)
    except Exception as exc:
        print(f"Live heatmap matches fetch error: {exc}")
    return rows


def fetch_live_heatmap_match_ids(league_id: str) -> Set[str]:
    """Match IDs currently in the live heatmap feed."""
    return {m["match_id"] for m in fetch_live_heatmap_matches(league_id) if m.get("match_id")}


def _current_season_fixture_index(league_id: str) -> Dict[str, Dict[str, Any]]:
    """Match id → row from current ``soccerfixtures/leagueid`` (for team names)."""
    try:
        data, _from_cache = fetch_goalserve_json(f"soccerfixtures/leagueid/{league_id}")
        _, fixtures = _parse_fixtures_payload(data)
        return {_norm_match_id(f.get("match_id")): f for f in fixtures if f.get("match_id")}
    except Exception:
        return {}


def _enrich_heatmap_live_matches(
    live_rows: List[Dict[str, Any]],
    fixture_list: List[Dict[str, Any]],
    *,
    league_id: str = "",
    league_name: str,
    season_requested: Optional[str],
    season_resolved: Optional[str],
    feed: str,
) -> List[Dict[str, Any]]:
    """Merge live heatmap feed rows with fixture list + UI season context."""
    season_label = (
        season_resolved
        or season_requested
        or "current season"
    )
    by_id = {_norm_match_id(f.get("match_id")): f for f in fixture_list if f.get("match_id")}
    current_by_id = _current_season_fixture_index(league_id) if league_id else {}

    enriched: List[Dict[str, Any]] = []
    for row in live_rows:
        mid = row.get("match_id")
        fix = by_id.get(mid) or current_by_id.get(mid) or {}
        home = (fix.get("localteam_name") or row.get("home_team") or "").strip()
        away = (fix.get("visitorteam_name") or row.get("away_team") or "").strip()
        date = row.get("date") or fix.get("date")
        time = row.get("time") or fix.get("time")
        status = row.get("status") or fix.get("status")
        score = row.get("score")
        if not score and fix:
            ls = fix.get("localteam_score") or ""
            vs = fix.get("visitorteam_score") or ""
            if ls or vs:
                score = f"{ls}-{vs}"

        if home and away:
            match_name = f"{home} vs {away}"
        else:
            match_name = f"Match {mid}" + (f" ({status})" if status else "")

        display = fix.get("display") or (
            f"{date or '—'} {time or ''} — {match_name}"
            + (f" ({score})" if score else "")
            + (f" [{status}]" if status else "")
        ).strip()

        enriched.append(
            {
                "match_id": mid,
                "season": season_label,
                "season_requested": season_requested,
                "season_resolved": season_resolved,
                "league_name": league_name or row.get("league_name_feed") or "",
                "feed": feed,
                "name": match_name,
                "home_team": home or None,
                "away_team": away or None,
                "date": date,
                "time": time,
                "status": status,
                "minute": row.get("minute"),
                "score": score,
                "display": display,
                "in_fixtures_feed": bool(fix),
            }
        )
    return enriched


def _league_has_heatmap_feed(league_id: str) -> bool:
    try:
        from app.services.league_catalog_service import get_league_by_id

        row = get_league_by_id(league_id)
        return bool(row and row.get("heatmap_league_id"))
    except Exception:
        return False


def _annotate_fixtures_heatmap_availability(
    league_id: str,
    fixture_list: List[Dict[str, Any]],
    live_rows: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if not _league_has_heatmap_feed(league_id):
        for f in fixture_list:
            f["heatmap_available"] = False
        return fixture_list, []

    if live_rows is None:
        live_rows = fetch_live_heatmap_matches(league_id)
    live_ids = {m["match_id"] for m in live_rows if m.get("match_id")}
    for f in fixture_list:
        mid = _norm_match_id(f.get("match_id"))
        f["heatmap_available"] = mid in live_ids
        if f["heatmap_available"]:
            f["display"] = f"{f.get('display', '')} [live heatmap]"
    return fixture_list, live_rows


# --- ASYNC FUNCTION: Fetch and Process Heatmap Data ---
async def fetch_and_process_heatmap(match_id: str, league_id: str, season: str | None = None) -> Dict[str, Any]:
    """
    Player heatmap from Goalserve ``commentaries/{league_id}_heatmap.xml`` (live matches only).
    Finished/historical fixtures from soccerhistory do not include heatmap data.
    """
    mid = _norm_match_id(match_id)

    league_details = fetch_league_data(league_id)
    if not isinstance(league_details, dict):
        league_details = {}
    all_players = league_details.get("all_players") or {}
    if not isinstance(all_players, dict):
        all_players = {}
    league_name = (league_details.get("league_name") or f"League {league_id}").strip()
    if league_details.get("roster_warning"):
        print(f"League roster warning ({league_id}): {league_details.get('roster_warning')}")
    league_details = {
        "league_name": league_name,
        "all_players": all_players,
    }

    live_rows = fetch_live_heatmap_matches(league_id)
    live_ids = {r["match_id"] for r in live_rows if r.get("match_id")}
    live_by_id = {r["match_id"]: r for r in live_rows if r.get("match_id")}

    fixtures_response = await fetch_fixtures(league_id, season)
    heatmap_live_matches = (
        fixtures_response.get("heatmap_live_matches", []) if isinstance(fixtures_response, dict) else []
    )

    if "error" in fixtures_response:
        if mid not in live_ids:
            return fixtures_response
        fixtures_response = {"fixtures": [], "heatmap_live_matches": heatmap_live_matches}

    target_fixture = next(
        (
            f
            for f in fixtures_response.get("fixtures", [])
            if _norm_match_id(f.get("match_id")) == mid
        ),
        None,
    )

    feed_row = live_by_id.get(mid)
    if not target_fixture and feed_row:
        target_fixture = {
            "match_id": mid,
            "date": feed_row.get("date") or "N/A",
            "time": feed_row.get("time") or "N/A",
            "status": feed_row.get("status") or "N/A",
            "localteam_name": feed_row.get("home_team", "Home"),
            "visitorteam_name": feed_row.get("away_team", "Away"),
            "localteam_score": "",
            "visitorteam_score": "",
        }
        if feed_row.get("score"):
            parts = str(feed_row["score"]).split("-", 1)
            if len(parts) == 2:
                target_fixture["localteam_score"] = parts[0].strip()
                target_fixture["visitorteam_score"] = parts[1].strip()

    if not target_fixture:
        return {
            "error": (
                f"Match ID {match_id} not found in the fixtures feed for league {league_id}"
                f"{f' (season {season})' if season else ''} and not in the live heatmap feed."
            ),
            "heatmap_live_matches": heatmap_live_matches,
            "heatmap_live_match_ids": sorted(live_ids),
        }

    if mid not in live_ids:
        ids_hint = ", ".join(sorted(live_ids)[:12]) if live_ids else "(none right now)"
        return {
            "error": (
                f"No live heatmap for match {match_id}. Goalserve only publishes heatmaps in "
                f"commentaries/{league_id}_heatmap.xml for matches currently in that live feed — "
                f"not for finished games from season/history fixtures. "
                f"Live heatmap match IDs now: {ids_hint}."
            ),
            "heatmap_live_matches": heatmap_live_matches,
            "heatmap_live_match_ids": sorted(live_ids),
            "match_status": target_fixture.get("status"),
            "match_date": target_fixture.get("date"),
        }

    try:
        data, _from_cache = fetch_goalserve_json(f"commentaries/{league_id}_heatmap.xml")

        tournament = (data.get("commentaries") or {}).get("tournament") or {}
        target_match = next(
            (m for m in _as_match_list(tournament.get("match")) if _norm_match_id(m.get("@id")) == mid),
            None,
        )

        if not target_match:
            return {
                "error": f"Match {match_id} left the live heatmap feed. Refresh fixtures and pick a live match.",
                "heatmap_live_matches": heatmap_live_matches,
                "heatmap_live_match_ids": sorted(live_ids),
            }

        heatmaps = target_match.get("heatmaps") if isinstance(target_match.get("heatmaps"), dict) else {}
        local_team_data = process_team_heatmaps(heatmaps.get("localteam"))
        visitor_team_data = process_team_heatmaps(heatmaps.get("visitorteam"))

        if not local_team_data and not visitor_team_data:
            return {
                "error": (
                    f"Match {match_id} is in the live feed but has no player heatmap points yet "
                    "(match may be pre-kickoff or data not populated)."
                ),
                "heatmap_live_matches": heatmap_live_matches,
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
            roster = league_details.get("all_players") or {}
            for player_id, pdata in player_data_map.items():
                name = roster.get(player_id, f"Player ID {player_id}")
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

    lineup_data: Dict[str, Any] = {}
    heatmap_data: Dict[str, Any] = {}

    try:
        c_data, _from_cache = fetch_goalserve_json(f"commentaries/{league_id}.xml")

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

    try:
        h_data, _from_cache = fetch_goalserve_json(f"commentaries/{league_id}_heatmap.xml")

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