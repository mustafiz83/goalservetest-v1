from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from app.services.league_catalog_service import get_league_by_id, get_league_catalog

router = APIRouter(prefix="/api/v1", tags=["leagues"])


@router.get("/leagues")
async def list_leagues(
    q: Optional[str] = Query(None, description="Search league id, name, or country"),
    country: Optional[str] = Query(None, description="Filter by country name"),
    heatmap_only: bool = Query(
        False, description="Only leagues with commentaries/heatmap feed (per docs)"
    ),
    live_only: bool = Query(
        False, description="Only leagues with at least one live match in soccernew/live"
    ),
    sort: str = Query(
        "default",
        description="Sort: default | live | heatmap | next_match (earliest kickoff; slower)",
    ),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    per_page: int = Query(50, ge=1, le=200, description="Rows per page"),
    include_next_matches: bool = Query(
        False,
        description="Attach next fixtures per row (slow; use with sort=next_match)",
    ),
) -> JSONResponse:
    """
    Merged Goalserve league catalog from ``soccerfixtures/data/mapping`` and ``data/seasons``.

    Each league includes ``heatmap_league_id`` when a live commentaries (and heatmap) feed exists
    — same numeric id as ``league_id`` (``commentaries/{id}_heatmap.xml``).
    """
    try:
        payload = get_league_catalog(
            q=q,
            country=country,
            heatmap_only=heatmap_only,
            live_only=live_only,
            sort=sort,
            page=page,
            per_page=per_page,
            include_next_matches=include_next_matches,
        )
        return JSONResponse(status_code=200, content=payload)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to load league catalog: {exc}") from exc


@router.get("/leagues/{league_id}")
async def get_league(league_id: str) -> JSONResponse:
    try:
        row = get_league_by_id(league_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to load league: {exc}") from exc

    if not row:
        raise HTTPException(status_code=404, detail=f"League ID {league_id} not found in mapping feed")
    return JSONResponse(status_code=200, content=row)
