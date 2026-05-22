from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.openapi.utils import get_openapi
from app.api.endpoints import router as api_router
from app.api.live_endpoints import router as football_live_router
from app.api.today_result_endpoint import router as today_result
from app.api.ws_endpoints import router as ws_router
from app.api.league_endpoints import router as league_router
from app.services.inplay_service import start_scheduler, stop_scheduler
from app.services.ws_soccer_service import soccer_ws_service

# Project root (prev-works/goalservetest-v1) — paths must not depend on shell cwd
ROOT_DIR = Path(__file__).resolve().parent.parent

app = FastAPI()


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title="Goalserve API",
        version="1.0.0",
        routes=app.routes,
    )
    # Manually inject WebSocket endpoint — OpenAPI/Swagger hides WS routes by default
    schema["paths"]["/ws/soccer"] = {
        "get": {
            "tags": ["soccer-websocket"],
            "summary": "Live Soccer WebSocket Feed",
            "description": (
                "Connect via **WebSocket** (not HTTP GET) to receive a real-time stream "
                "of live soccer match updates from Goalserve.\n\n"
                "**URL:** `ws://<host>/ws/soccer`\n\n"
                "Each pushed frame contains a `mt` field indicating the message type:\n"
                "- `avl` – full snapshot of all live events (sent immediately on connect)\n"
                "- `updt` – incremental update for a single event\n\n"
                "Use **`GET /api/ws/soccer`** below to do a one-shot test and see the "
                "live data structure without opening a persistent WebSocket."
            ),
            "operationId": "ws_soccer_feed",
            "responses": {
                "101": {"description": "Switching Protocols — WebSocket connection established"}
            },
        }
    }
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi

app.mount("/static", StaticFiles(directory=ROOT_DIR / "static"), name="static")

templates = Jinja2Templates(directory=ROOT_DIR / "templates")

app.include_router(api_router)
app.include_router(football_live_router)
app.include_router(today_result)
app.include_router(ws_router)
app.include_router(league_router)


# Frontend Endpoint (serves the HTML)
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    # Default values for initial load
    default_league_id = "1204"  
    default_match_id = "3838001" 
    
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "default_league_id": default_league_id,
            "default_match_id": default_match_id,
        },
    )

@app.get("/live")
async def serve_live_stats():
    """Serve the live stats dashboard"""
    return FileResponse(ROOT_DIR / "templates" / "live_stats.html")


@app.get("/today")
async def serve_today_results():
    """Serve today's results page"""
    return FileResponse(ROOT_DIR / "templates" / "today_result.html")


@app.get("/leagues")
async def serve_leagues_catalog():
    """League mapping, heatmap ids, and seasons (Goalserve catalog UI)"""
    return FileResponse(ROOT_DIR / "templates" / "leagues.html")


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.on_event("startup")
async def startup_event():
    start_scheduler()
    soccer_ws_service.start()


@app.on_event("shutdown")
async def shutdown_event():
    stop_scheduler()
    await soccer_ws_service.stop()