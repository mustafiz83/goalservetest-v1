from fastapi import APIRouter, HTTPException
from app.services.goalserve_service import fetch_and_process_heatmap, fetch_fixtures

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
        # Determine appropriate status code based on error message
        print("error in endpoints")
        error_msg = data["error"].lower()
        print(error_msg)
        status_code = 404 if "not found" in error_msg or "not yet available" in error_msg else 500
        raise HTTPException(status_code=status_code, detail=data["error"])
        
    return data


