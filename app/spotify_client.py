"""Spotify client singleton and helpers."""
import os
import logging
import spotipy
from spotipy.oauth2 import SpotifyOAuth

from app.config import get_settings

logger = logging.getLogger(__name__)

CACHE_PATH = ".spotify_cache"


def get_spotify_oauth() -> SpotifyOAuth:
    """Create a SpotifyOAuth manager."""
    settings = get_settings()
    return SpotifyOAuth(
        client_id=settings.spotipy_client_id,
        client_secret=settings.spotipy_client_secret,
        redirect_uri=settings.spotipy_redirect_uri,
        scope=settings.spotify_scopes,
        cache_path=CACHE_PATH,
        open_browser=False,
    )


def get_spotify_client() -> spotipy.Spotify | None:
    """Return an authenticated Spotify client or None."""
    settings = get_settings()
    if not settings.spotipy_client_id or not settings.spotipy_client_secret:
        return None
    
    try:
        auth_manager = get_spotify_oauth()
        token_info = auth_manager.get_cached_token()
        
        if not token_info:
            return None
        
        if auth_manager.is_token_expired(token_info):
            token_info = auth_manager.refresh_access_token(token_info["refresh_token"])
        
        return spotipy.Spotify(auth_manager=auth_manager)
    except Exception as e:
        logger.error(f"Failed to create Spotify client: {e}")
        return None


def clear_spotify_cache():
    """Remove the Spotify token cache."""
    if os.path.exists(CACHE_PATH):
        os.remove(CACHE_PATH)
        logger.info("Spotify cache cleared.")
