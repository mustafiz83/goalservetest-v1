import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    # Get the Goalserve API key from the .env file
    GOALSERVE_API_KEY: str = os.getenv("GOALSERVE_API_KEY", "YOUR_FALLBACK_KEY")
    GOALSERVE_BASE_URL: str = "https://www.goalserve.com/getfeed/"
    INPLAY_SCHEDULER_JOB: bool = os.getenv("INPLAY_SCHEDULER_JOB", "false").lower() == "true"

settings = Settings()