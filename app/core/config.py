import os
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()

_KEY_PLACEHOLDERS = frozenset(
    {
        "",
        "YOUR_FALLBACK_KEY",
        "YOUR_API_KEY_HERE",
        "your_api_key_here",
    }
)


class Settings:
    GOALSERVE_API_KEY: str = os.getenv("GOALSERVE_API_KEY", "")
    GOALSERVE_BASE_URL: str = "https://www.goalserve.com/getfeed/"
    GOALSERVE_WS_BASE_URL: str = "ws://live.goalserve.com/ws"
    GOALSERVE_WS_AUTH_URL: str = "http://live.goalserve.com/api/v1/auth/gettoken"
    INPLAY_SCHEDULER_JOB: bool = os.getenv("INPLAY_SCHEDULER_JOB", "false").lower() == "true"

    @property
    def inplay_soccer_feed_url(self) -> str:
        """
        Inplay gzip feed URL. Set GOALSERVE_INPLAY_SOCCER_URL in .env to the exact URL
        from Goalserve admin if it differs. Otherwise the key from GOALSERVE_API_KEY is
        appended as ?key= when present.
        """
        explicit = os.getenv("GOALSERVE_INPLAY_SOCCER_URL", "").strip()
        if explicit:
            return explicit
        base = "http://inplay.goalserve.com/inplay-soccer.gz"
        key = (self.GOALSERVE_API_KEY or "").strip()
        if key not in _KEY_PLACEHOLDERS:
            return f"{base}?key={quote(key, safe='')}"
        return base


settings = Settings()