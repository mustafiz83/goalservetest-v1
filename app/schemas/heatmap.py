from fastapi import APIRouter, HTTPException
from app.services.goalserve_service import fetch_and_process_heatmap
router = APIRouter()

class HeatmapDataResponse:
    def __init__(self, match_id: str, league_id: str, localteam_heatmap: list, visitorteam_heatmap: list):
        self.match_id = match_id
        self.league_id = league_id
        self.localteam_heatmap = localteam_heatmap
        self.visitorteam_heatmap = visitorteam_heatmap

@router.get("/heatmap/{league_id}/{match_id}")
async def get_match_heatmap(league_id: str, match_id: str):
    """
    Fetches and returns processed heatmap data for a specific match.
    Example: league_id=1005 (Champions League), match_id=3954507
    """
    
    # Using the provided example IDs: Champions League 1005, Match ID 3954507
    data = await fetch_and_process_heatmap(match_id, league_id)

    if "error" in data:
        raise HTTPException(status_code=500, detail=data["error"])
        
    return data # Thi