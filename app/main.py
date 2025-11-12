from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
# Import the router we just created
from app.api.endpoints import router as api_router

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

# --- Include the API router here ---
app.include_router(api_router)


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