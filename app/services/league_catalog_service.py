"""Goalserve league mapping + seasons catalog (see goalserve-api-docs/)."""

from __future__ import annotations

import datetime
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests

from app.services.goalserve_service import (
    API_KEY,
    BASE_URL,
    REQUEST_TIMEOUT,
    _parse_fixture_kickoff,
    fetch_next_matches_for_league,
)

DOCS_DIR = Path(__file__).resolve().parents[2] / "goalserve-api-docs"
FULLSOCCER_DOC = DOCS_DIR / "fullsoccer-wo (2).txt"

_CACHE: Dict[str, Any] = {"loaded_at": 0.0, "leagues": []}
_CACHE_TTL_SECONDS = 3600

_LIVE_CACHE: Dict[str, Any] = {"counts": {}, "loaded_at": 0.0, "feed_updated": None}
_LIVE_CACHE_TTL_SECONDS = 45


def _as_list(node: Any) -> List[Any]:
    if not node:
        return []
    if isinstance(node, list):
        return node
    return [node]


def _parse_commentary_league_ids_from_docs() -> Set[str]:
    """Leagues with live commentaries feed (heatmap uses same id: commentaries/{id}_heatmap.xml)."""
    ids: Set[str] = set()
    if FULLSOCCER_DOC.is_file():
        text = FULLSOCCER_DOC.read_text(encoding="utf-8", errors="ignore")
        ids.update(re.findall(r"commentaries/(\d+)\.xml", text))
        ids.update(re.findall(r"commentaries/(\d+)_heatmap", text))
    return ids


def _parse_season_names(block: Any) -> List[str]:
    seasons = _as_list((block or {}).get("season"))
    names: List[str] = []
    for s in seasons:
        if not isinstance(s, dict):
            continue
        name = (s.get("@name") or "").strip()
        if name:
            names.append(name)
    return names


def _fetch_json(path: str) -> Dict[str, Any]:
    url = f"{BASE_URL}{API_KEY}/{path}?json=1"
    response = requests.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {}


def _build_seasons_index() -> Dict[str, Dict[str, List[str]]]:
    data = _fetch_json("soccerfixtures/data/seasons")
    root = data.get("seasons", {})
    # Feed uses ``league`` nodes; older samples used ``mapping``
    mappings = _as_list(root.get("league") or root.get("mapping"))
    index: Dict[str, Dict[str, List[str]]] = {}
    for row in mappings:
        if not isinstance(row, dict):
            continue
        lid = str(row.get("@id", "")).strip()
        if not lid:
            continue
        index[lid] = {
            "results": _parse_season_names(row.get("results")),
            "standings": _parse_season_names(row.get("standings")),
        }
    return index


def _normalize_mapping_row(row: Dict[str, Any], commentary_ids: Set[str]) -> Dict[str, Any]:
    league_id = str(row.get("@id", "")).strip()
    has_commentary = league_id in commentary_ids
    heatmap_id: Optional[str] = league_id if has_commentary else None

    return {
        "league_id": league_id,
        "name": (row.get("@name") or "").strip() or "Unknown",
        "country": (row.get("@country") or "").strip(),
        "current_season": (row.get("@season") or "").strip(),
        "date_start": (row.get("@date_start") or "").strip(),
        "date_end": (row.get("@date_end") or "").strip(),
        "is_cup": str(row.get("@iscup", "")).lower() in ("true", "1", "yes"),
        "path": (row.get("@path") or "").strip(),
        "live_lineups": str(row.get("@live_lineups", "")).lower() == "true",
        "live_stats": str(row.get("@live_stats", "")).lower() == "true",
        "live_pbp": str(row.get("@live_pbp", "")).lower() == "true",
        "has_commentary_feed": has_commentary,
        "heatmap_league_id": heatmap_id,
        "heatmap_feed_path": (
            f"commentaries/{heatmap_id}_heatmap.xml" if heatmap_id else None
        ),
        "fixtures_url": f"soccerfixtures/leagueid/{league_id}",
        "history_url_pattern": f"soccerhistory/leagueid/{league_id}-{{season}}",
    }


def _fetch_live_match_counts_by_league() -> Dict[str, int]:
    """League id → number of matches in ``soccernew/live`` (short TTL cache)."""
    now = time.time()
    if (
        _LIVE_CACHE["counts"]
        and (now - _LIVE_CACHE["loaded_at"]) < _LIVE_CACHE_TTL_SECONDS
    ):
        return _LIVE_CACHE["counts"]

    counts: Dict[str, int] = {}
    feed_updated: Optional[str] = None
    try:
        data = _fetch_json("soccernew/live")
        scores = data.get("scores", {})
        if isinstance(scores, dict):
            feed_updated = scores.get("@updated")
        for category in _as_list(scores.get("category") if isinstance(scores, dict) else None):
            if not isinstance(category, dict):
                continue
            lid = str(category.get("@id", "")).strip()
            if not lid:
                continue
            matches_data = category.get("matches", {})
            if not isinstance(matches_data, dict):
                continue
            match_list = _as_list(matches_data.get("match"))
            n = sum(1 for m in match_list if isinstance(m, dict) and m)
            if n > 0:
                counts[lid] = counts.get(lid, 0) + n
    except Exception:
        # Keep last good snapshot if refresh fails
        if _LIVE_CACHE["counts"]:
            return _LIVE_CACHE["counts"]
        return {}

    _LIVE_CACHE["counts"] = counts
    _LIVE_CACHE["loaded_at"] = now
    _LIVE_CACHE["feed_updated"] = feed_updated
    return counts


def _apply_live_flags(leagues: List[Dict[str, Any]], live_counts: Dict[str, int]) -> None:
    for lg in leagues:
        lid = lg.get("league_id", "")
        n = live_counts.get(lid, 0)
        lg["has_live_match"] = n > 0
        lg["live_match_count"] = n


def _earliest_next_kickoff(league: Dict[str, Any]) -> datetime.datetime:
    """Sort key: live first, then soonest upcoming fixture in ``next_matches``."""
    for m in league.get("next_matches") or []:
        if m.get("is_live"):
            return datetime.datetime.min
    kickoffs: List[datetime.datetime] = []
    for m in league.get("next_matches") or []:
        dt = _parse_fixture_kickoff(m.get("date", ""), m.get("time", ""))
        if dt:
            kickoffs.append(dt)
    return min(kickoffs) if kickoffs else datetime.datetime.max


def _sort_leagues(leagues: List[Dict[str, Any]], mode: str = "default") -> None:
    """
    ``default`` — live, then heatmap, then name.
    ``live`` — live leagues first (by match count).
    ``heatmap`` — heatmap-capable leagues first.
    ``next_match`` — earliest next/live kickoff (requires ``next_matches`` on rows).
    """
    mode = (mode or "default").strip().lower()

    if mode == "live":
        leagues.sort(
            key=lambda x: (
                0 if x.get("has_live_match") else 1,
                -(x.get("live_match_count") or 0),
                (x.get("country") or "").lower(),
                (x.get("name") or "").lower(),
            )
        )
    elif mode == "heatmap":
        leagues.sort(
            key=lambda x: (
                0 if x.get("heatmap_league_id") else 1,
                (x.get("country") or "").lower(),
                (x.get("name") or "").lower(),
            )
        )
    elif mode == "next_match":
        leagues.sort(
            key=lambda x: (
                _earliest_next_kickoff(x),
                (x.get("country") or "").lower(),
                (x.get("name") or "").lower(),
            )
        )
    else:
        leagues.sort(
            key=lambda x: (
                0 if x.get("has_live_match") else 1,
                -(x.get("live_match_count") or 0),
                0 if x.get("heatmap_league_id") else 1,
                (x.get("country") or "").lower(),
                (x.get("name") or "").lower(),
            )
        )


def load_league_catalog(force: bool = False) -> List[Dict[str, Any]]:
    now = time.time()
    if (
        not force
        and _CACHE["leagues"]
        and (now - _CACHE["loaded_at"]) < _CACHE_TTL_SECONDS
    ):
        return _CACHE["leagues"]

    commentary_ids = _parse_commentary_league_ids_from_docs()
    mapping_data = _fetch_json("soccerfixtures/data/mapping")
    fixtures_root = mapping_data.get("fixtures", {})
    mappings = _as_list(fixtures_root.get("mapping"))

    seasons_index = _build_seasons_index()

    leagues: List[Dict[str, Any]] = []
    for row in mappings:
        if not isinstance(row, dict):
            continue
        entry = _normalize_mapping_row(row, commentary_ids)
        sid = entry["league_id"]
        seasons = seasons_index.get(sid, {})
        entry["seasons_results"] = seasons.get("results", [])
        entry["seasons_standings"] = seasons.get("standings", [])
        # Prefer results seasons for fixture/history picker; dedupe merged list
        merged: List[str] = []
        seen: Set[str] = set()
        for name in entry["seasons_results"] + entry["seasons_standings"]:
            if name not in seen:
                seen.add(name)
                merged.append(name)
        entry["seasons"] = merged
        leagues.append(entry)

    leagues.sort(
        key=lambda x: (
            (x.get("country") or "").lower(),
            (x.get("name") or "").lower(),
        )
    )

    _CACHE["leagues"] = leagues
    _CACHE["loaded_at"] = now
    _CACHE["commentary_league_count"] = len(commentary_ids)
    return leagues


DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 200


def _attach_next_matches(leagues: List[Dict[str, Any]], limit: int = 2) -> None:
    """Fetch next/live fixtures for visible rows (parallel, cached per league)."""
    if not leagues:
        return

    def enrich(league: Dict[str, Any]) -> None:
        lid = league.get("league_id", "")
        try:
            league["next_matches"] = fetch_next_matches_for_league(lid, limit=limit)
        except Exception:
            league["next_matches"] = []

    workers = min(8, len(leagues))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(enrich, lg) for lg in leagues]
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception:
                pass


def get_league_catalog(
    *,
    q: Optional[str] = None,
    country: Optional[str] = None,
    heatmap_only: bool = False,
    live_only: bool = False,
    sort: str = "default",
    page: int = 1,
    per_page: int = DEFAULT_PER_PAGE,
    include_next_matches: bool = True,
    next_matches_limit: int = 2,
) -> Dict[str, Any]:
    leagues = load_league_catalog()
    filtered = leagues
    sort_mode = (sort or "default").strip().lower()
    if sort_mode not in ("default", "live", "heatmap", "next_match"):
        sort_mode = "default"

    if heatmap_only:
        filtered = [lg for lg in filtered if lg.get("heatmap_league_id")]

    if country:
        c = country.strip().lower()
        filtered = [lg for lg in filtered if lg.get("country", "").lower() == c]

    if q:
        needle = q.strip().lower()
        filtered = [
            lg
            for lg in filtered
            if needle in lg.get("league_id", "").lower()
            or needle in lg.get("name", "").lower()
            or needle in lg.get("country", "").lower()
        ]

    live_counts = _fetch_live_match_counts_by_league()
    _apply_live_flags(filtered, live_counts)

    if live_only:
        filtered = [lg for lg in filtered if lg.get("has_live_match")]

    need_next_for_sort = sort_mode == "next_match"
    if need_next_for_sort:
        _attach_next_matches(filtered, limit=max(1, min(next_matches_limit, 2)))

    _sort_leagues(filtered, sort_mode)

    per_page = max(1, min(int(per_page), MAX_PER_PAGE))
    page = max(1, int(page))
    total = len(filtered)
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    if page > total_pages:
        page = total_pages
    start = (page - 1) * per_page
    end = start + per_page

    countries = sorted(
        {lg.get("country", "").strip() for lg in leagues if lg.get("country", "").strip()}
    )

    page_leagues = filtered[start:end]
    if include_next_matches and not need_next_for_sort:
        _attach_next_matches(page_leagues, limit=max(1, min(next_matches_limit, 2)))

    return {
        "total": total,
        "sort": sort_mode,
        "filters": {
            "live_only": live_only,
            "heatmap_only": heatmap_only,
        },
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "heatmap_league_count": sum(1 for lg in leagues if lg.get("heatmap_league_id")),
        "live_league_count": len(live_counts),
        "live_match_total": sum(live_counts.values()),
        "live_feed_updated": _LIVE_CACHE.get("feed_updated"),
        "countries": countries,
        "sources": {
            "mapping": "soccerfixtures/data/mapping",
            "seasons": "soccerfixtures/data/seasons",
            "live": "soccernew/live",
            "heatmap_docs": str(FULLSOCCER_DOC.name),
        },
        "leagues": page_leagues,
    }


def get_league_by_id(league_id: str) -> Optional[Dict[str, Any]]:
    lid = str(league_id).strip()
    for lg in load_league_catalog():
        if lg.get("league_id") == lid:
            row = dict(lg)
            live_counts = _fetch_live_match_counts_by_league()
            _apply_live_flags([row], live_counts)
            return row
    return None
