from functools import lru_cache

from pydantic import BaseModel


class Settings(BaseModel):
    anthropic_api_key: str = ""
    google_maps_api_key: str = ""
    mapbox_token: str = ""
    demo_mode: bool = False
    fallback_to_haiku: bool = True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    import os

    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        google_maps_api_key=os.getenv("GOOGLE_MAPS_API_KEY", ""),
        mapbox_token=os.getenv("MAPBOX_TOKEN", ""),
        demo_mode=os.getenv("DEMO_MODE", "").lower() in ("1", "true", "yes"),
        fallback_to_haiku=os.getenv("FALLBACK_TO_HAIKU", "true").lower() in ("1", "true", "yes"),
    )
