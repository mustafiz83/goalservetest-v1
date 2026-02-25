import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    GOALSERVE_API_KEY: str = os.getenv("GOALSERVE_API_KEY", "YOUR_FALLBACK_KEY")
    GOALSERVE_BASE_URL: str = "https://www.goalserve.com/getfeed/"
    GOALSERVE_WS_BASE_URL: str = "ws://live.goalserve.com/ws"
    GOALSERVE_WS_AUTH_URL: str = "http://live.goalserve.com/api/v1/auth/gettoken"
    INPLAY_SCHEDULER_JOB: bool = os.getenv("INPLAY_SCHEDULER_JOB", "false").lower() == "true"

settings = Settings()