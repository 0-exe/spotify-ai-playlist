"""Playlist generation API endpoints."""
import logging
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db, ArtistWeight
from app.spotify_client import get_spotify_client
from app.gemini_service import get_search_strategy
from app.playlist_generator import (
    search_spotify_tracks,
    apply_artist_cap,
    create_spotify_playlist,
    start_playback,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class GenerateRequest(BaseModel):
    prompt: str = Field(..., description="Mood/vibe description for the playlist")
    limit: int = Field(40, ge=5, le=100, description="Number of tracks")
    sort_mode: str = Field("popularity", description="popularity | balanced | hidden_gems")
    create_on_spotify: bool = Field(False, description="Create playlist on Spotify account")
    playlist_name: str = Field("", description="Playlist name (auto-generated if empty)")
    device_name: str = Field("", description="Spotify Connect device name to play on")
    start_playback: bool = Field(False, description="Start playback after creating playlist")


class GenerateResponse(BaseModel):
    tracks: list[dict[str, Any]]
    playlist: dict[str, Any] | None = None
    search_strategy: dict[str, Any]
    total_found: int
    mood_description: str


@router.post("/generate", response_model=GenerateResponse)
async def generate_playlist(
    request: GenerateRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Generate an AI-curated playlist based on mood/vibe prompt.
    
    This endpoint can be called from Home Assistant as a rest_command.
    """
    sp = get_spotify_client()
    if not sp:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated with Spotify. Please visit /login first.",
        )
    
    # 1. Get AI search strategy
    logger.info(f"Generating playlist for prompt: {request.prompt!r}")
    strategy = await get_search_strategy(request.prompt)
    
    # 2. Search Spotify
    raw_tracks = search_spotify_tracks(sp, strategy)
    logger.info(f"Found {len(raw_tracks)} raw tracks")
    
    # 3. Load artist weights from DB
    result = await db.execute(select(ArtistWeight))
    weight_rows = result.scalars().all()
    artist_weights = {row.artist_id: row.weight for row in weight_rows}
    
    # 4. Apply scoring, caps, and sorting
    scored_tracks = apply_artist_cap(
        raw_tracks,
        artist_weights,
        sort_mode=request.sort_mode,
        playlist_limit=request.limit,
    )
    
    # 5. Optionally create Spotify playlist
    playlist_info = None
    if request.create_on_spotify:
        name = request.playlist_name or f"🎵 {request.prompt[:40]}"
        mood_desc = strategy.get("mood_description", request.prompt)
        description = f"AI-generated playlist: {mood_desc} | Made with Spotify AI Playlist Generator"
        
        playlist_info = create_spotify_playlist(sp, scored_tracks, name, description)
        
        if request.start_playback and playlist_info:
            start_playback(
                sp,
                playlist_info["uri"],
                request.device_name or None,
            )
    
    return GenerateResponse(
        tracks=scored_tracks,
        playlist=playlist_info,
        search_strategy=strategy,
        total_found=len(raw_tracks),
        mood_description=strategy.get("mood_description", request.prompt),
    )
