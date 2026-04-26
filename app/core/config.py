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


def _get_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() == "true"


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


class Settings:
    GOALSERVE_API_KEY: str = os.getenv("GOALSERVE_API_KEY", "")
    GOALSERVE_BASE_URL: str = "https://www.goalserve.com/getfeed/"
    GOALSERVE_WS_BASE_URL: str = "ws://live.goalserve.com/ws"
    GOALSERVE_WS_AUTH_URL: str = "http://live.goalserve.com/api/v1/auth/gettoken"
    INPLAY_SCHEDULER_JOB: bool = _get_bool("INPLAY_SCHEDULER_JOB", False)

    # Scheduler and polling controls
    INPLAY_SCHEDULER_INTERVAL_SECONDS: int = _get_int("INPLAY_SCHEDULER_INTERVAL_SECONDS", 1)
    SCHEDULER_LOOP_SLEEP_SECONDS: float = _get_float("SCHEDULER_LOOP_SLEEP_SECONDS", 1.0)

    # Request timeout controls
    GOALSERVE_REQUEST_TIMEOUT_SECONDS: int = _get_int("GOALSERVE_REQUEST_TIMEOUT_SECONDS", 30)
    GOALSERVE_INPLAY_TIMEOUT_SECONDS: int = _get_int("GOALSERVE_INPLAY_TIMEOUT_SECONDS", 5)
    GOALSERVE_INPLAY_SCHEDULER_TIMEOUT_SECONDS: int = _get_int("GOALSERVE_INPLAY_SCHEDULER_TIMEOUT_SECONDS", 2)
    GOALSERVE_LIVE_TIMEOUT_SECONDS: float = _get_float("GOALSERVE_LIVE_TIMEOUT_SECONDS", 15.0)
    GOALSERVE_WS_AUTH_TIMEOUT_SECONDS: float = _get_float("GOALSERVE_WS_AUTH_TIMEOUT_SECONDS", 10.0)

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