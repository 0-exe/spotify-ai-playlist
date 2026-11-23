"""Spotify OAuth2 authentication flow."""
import logging
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
import spotipy
from spotipy.oauth2 import SpotifyOAuth

from app.config import get_settings
from app.spotify_client import get_spotify_oauth, get_spotify_client, clear_spotify_cache

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/login")
async def login():
    """Redirect user to Spotify authorization page."""
    settings = get_settings()
    if not settings.spotipy_client_id or not settings.spotipy_client_secret:
        raise HTTPException(
            status_code=503,
            detail="Spotify credentials not configured. Set SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET in .env",
        )
    
    auth_manager = get_spotify_oauth()
    auth_url = auth_manager.get_authorize_url()
    return RedirectResponse(url=auth_url)


@router.get("/callback")
async def callback(request: Request, code: str = None, error: str = None):
    """Handle Spotify OAuth callback."""
    if error:
        return RedirectResponse(url=f"/?error={error}")
    
    if not code:
        raise HTTPException(status_code=400, detail="No authorization code received")
    
    auth_manager = get_spotify_oauth()
    token_info = auth_manager.get_access_token(code)
    
    if not token_info:
        raise HTTPException(status_code=400, detail="Failed to get access token")
    
    return RedirectResponse(url="/")


@router.get("/logout")
async def logout():
    """Clear Spotify cache and log out."""
    clear_spotify_cache()
    return RedirectResponse(url="/")


@router.get("/api/auth/status")
async def auth_status():
    """Check if user is authenticated with Spotify."""
    try:
        sp = get_spotify_client()
        if sp:
            user = sp.current_user()
            return {
                "authenticated": True,
                "user": {
                    "id": user.get("id"),
                    "display_name": user.get("display_name"),
                    "email": user.get("email"),
                    "image": user.get("images", [{}])[0].get("url") if user.get("images") else None,
                },
            }
    except Exception as e:
        logger.debug(f"Auth check failed: {e}")
    
    return {"authenticated": False, "user": None}
