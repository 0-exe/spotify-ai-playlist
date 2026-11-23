"""Artist weight management endpoints."""
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.database import get_db, ArtistWeight

logger = logging.getLogger(__name__)
router = APIRouter()


class ArtistWeightUpdate(BaseModel):
    artist_id: str
    artist_name: str
    weight: float = Field(..., ge=0.0, le=2.0)


class ArtistWeightResponse(BaseModel):
    artist_id: str
    artist_name: str
    weight: float


@router.get("/artists/weights")
async def list_artist_weights(db: AsyncSession = Depends(get_db)):
    """List all saved artist weights."""
    result = await db.execute(select(ArtistWeight).order_by(ArtistWeight.artist_name))
    rows = result.scalars().all()
    return [
        {"artist_id": r.artist_id, "artist_name": r.artist_name, "weight": r.weight}
        for r in rows
    ]


@router.post("/artists/weights")
async def set_artist_weight(
    payload: ArtistWeightUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Set or update an artist's weight."""
    result = await db.execute(
        select(ArtistWeight).where(ArtistWeight.artist_id == payload.artist_id)
    )
    existing = result.scalar_one_or_none()
    
    if existing:
        existing.weight = payload.weight
        existing.artist_name = payload.artist_name
    else:
        db.add(
            ArtistWeight(
                artist_id=payload.artist_id,
                artist_name=payload.artist_name,
                weight=payload.weight,
            )
        )
    
    await db.commit()
    return {"artist_id": payload.artist_id, "artist_name": payload.artist_name, "weight": payload.weight}


@router.delete("/artists/weights/{artist_id}")
async def delete_artist_weight(
    artist_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Remove an artist weight (reset to default 1.0)."""
    await db.execute(delete(ArtistWeight).where(ArtistWeight.artist_id == artist_id))
    await db.commit()
    return {"deleted": artist_id}
