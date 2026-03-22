from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

# Load backend/.env before any code reads os.environ (does not override existing env vars).
_BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(_BACKEND_DIR / ".env")


class Settings(BaseModel):
    anthropic_api_key: str = ""
    google_maps_api_key: str = ""
    mapbox_token: str = ""
    demo_mode: bool = False
    fallback_to_haiku: bool = True
    # OpenClaw cloud sub-agent augment (Tasks 7–8). Set OPENCLAW_ENABLED=true to activate.
    openclaw_enabled: bool = False
    openclaw_gateway_url: str = ""   # e.g. https://<your-cloud>.openclaw.ai
    openclaw_api_key: str = ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    import os

    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        google_maps_api_key=os.getenv("GOOGLE_MAPS_API_KEY", ""),
        mapbox_token=os.getenv("MAPBOX_TOKEN", ""),
        demo_mode=os.getenv("DEMO_MODE", "").lower() in ("1", "true", "yes"),
        fallback_to_haiku=os.getenv("FALLBACK_TO_HAIKU", "true").lower() in ("1", "true", "yes"),
        openclaw_enabled=os.getenv("OPENCLAW_ENABLED", "").lower() in ("1", "true", "yes"),
        openclaw_gateway_url=os.getenv("OPENCLAW_GATEWAY_URL", ""),
        openclaw_api_key=os.getenv("OPENCLAW_API_KEY", ""),
    )
