from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
# Import the router we just created
from app.api.endpoints import router as api_router
from app.api.live_endpoints import router as football_live_router
from app.api.today_result_endpoint import router as today_result

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

# --- Include the API router here ---
app.include_router(api_router)
app.include_router(football_live_router)
app.include_router(today_result)


# Frontend Endpoint (serves the HTML)
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    # Default values for initial load
    default_league_id = "1204"  
    default_match_id = "3838001" 
    
    return templates.TemplateResponse(
        "index.html", 
        {
            "request": request,
            "default_league_id": default_league_id,
            "default_match_id": default_match_id
        }
    )

from fastapi.responses import FileResponse

@app.get("/live")
async def serve_live_stats():
    """Serve the live stats dashboard"""
    return FileResponse("templates/live_stats.html")


@app.get("/today")
async def serve_live_stats():
    """Serve the live stats dashboard"""
    return FileResponse("templates/today_result.html")


@app.get("/health")
async def health_check():
    return {"status": "ok"}