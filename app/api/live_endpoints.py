from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from app.services.football_live_service import FootballLiveService

router = APIRouter(prefix="/api/v1", tags=["football-live"])

@router.get("/football/live")
async def get_live_matches() -> JSONResponse:
    """
    Fetch all live football matches with real-time statistics
    
    Returns:
        - Live matches grouped by league/category
        - Detailed statistics for each match
        - Recent events (goals, cards, etc.)
    """
    result = await FootballLiveService.get_live_matches()
    
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    
    return JSONResponse(
        status_code=200,
        content={
            "updated": result["updated"],
            "total_matches": len(result["matches"]),
            "matches": result["matches"]
        }
    )

@router.get("/football/live/{league_id}")
async def get_live_matches_by_league(league_id: str) -> JSONResponse:
    """
    Get live matches filtered by specific league ID
    
    Args:
        league_id: The league ID to filter by
    
    Returns:
        Filtered live matches for the specified league
    """
    result = await FootballLiveService.get_live_matches()
    
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    
    filtered = [m for m in result["matches"] if m["league"]["id"] == league_id]
    
    if not filtered:
        return JSONResponse(
            status_code=200,
            content={
                "league_id": league_id,
                "total_matches": 0,
                "matches": []
            }
        )
    
    return JSONResponse(
        status_code=200,
        content={
            "updated": result["updated"],
            "league_id": league_id,
            "total_matches": len(filtered),
            "matches": filtered
        }
    )