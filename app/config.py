"""Application configuration using pydantic-settings."""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    spotipy_client_id: str = ""
    spotipy_client_secret: str = ""
    spotipy_redirect_uri: str = "http://localhost:8000/callback"
    gemini_api_key: str = ""
    
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    database_url: str = "sqlite+aiosqlite:///./data/spotify_ui.db"
    
    # Spotify OAuth scopes
    spotify_scopes: str = (
        "user-read-private user-read-email "
        "playlist-modify-public playlist-modify-private "
        "user-read-playback-state user-modify-playback-state "
        "streaming"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
