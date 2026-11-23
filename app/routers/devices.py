"""Spotify Connect device management endpoints."""
import logging
from fastapi import APIRouter, HTTPException

from app.spotify_client import get_spotify_client

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/devices")
async def list_devices():
    """List available Spotify Connect devices."""
    sp = get_spotify_client()
    if not sp:
        raise HTTPException(status_code=401, detail="Not authenticated with Spotify")
    
    try:
        result = sp.devices()
        devices = result.get("devices", [])
        return {
            "devices": [
                {
                    "id": d.get("id"),
                    "name": d.get("name"),
                    "type": d.get("type"),
                    "is_active": d.get("is_active", False),
                    "volume_percent": d.get("volume_percent"),
                }
                for d in devices
            ]
        }
    except Exception as e:
        logger.error(f"Failed to get devices: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/devices/{device_id}/transfer")
async def transfer_playback(device_id: str):
    """Transfer playback to a specific device."""
    sp = get_spotify_client()
    if not sp:
        raise HTTPException(status_code=401, detail="Not authenticated with Spotify")
    
    try:
        sp.transfer_playback(device_id=device_id, force_play=True)
        return {"success": True, "device_id": device_id}
    except Exception as e:
        logger.error(f"Failed to transfer playback: {e}")
        raise HTTPException(status_code=500, detail=str(e))
