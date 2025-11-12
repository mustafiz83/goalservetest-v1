from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/", include_in_schema=False)
async def serve_heatmap_page(request: Request):
    """Serves the main HTML page with the football field visualization."""
    # You would typically populate context variables here, like a list of matches
    context = {
        "request": request,
        "title": "Soccer Player Heatmap",
        "default_league_id": "1204",  # UEFA Champions League
        "default_match_id": "3838001" # Example Match ID
    }
    return templates.TemplateResponse("index.html", context)