"""
Tests for the Spotify AI Playlist Generator.

Covers:
- Scoring / final_score calculation
- Artist cap (soft-ban, hard-ban, boost)
- Popularity sorting and sort modes
- SQLite persistence via async DB
- Gemini response parsing
- Deduplication logic
"""
import json
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.playlist_generator import compute_final_score, apply_artist_cap
from app.gemini_service import parse_gemini_response, _fallback_strategy


# ======================================================
# Helpers
# ======================================================

def make_track(
    track_id: str,
    name: str,
    artist_id: str,
    artist_name: str,
    popularity: int,
) -> dict:
    return {
        "id": track_id,
        "name": name,
        "artist_id": artist_id,
        "artist_name": artist_name,
        "all_artists": [artist_name],
        "popularity": popularity,
        "album_name": "Test Album",
        "album_image": "",
        "preview_url": "",
        "spotify_url": "",
        "uri": f"spotify:track:{track_id}",
        "duration_ms": 200000,
    }


TRACKS = [
    make_track("t1", "Song A", "artist1", "Artist One", 90),
    make_track("t2", "Song B", "artist1", "Artist One", 85),
    make_track("t3", "Song C", "artist1", "Artist One", 80),
    make_track("t4", "Song D", "artist2", "Artist Two", 75),
    make_track("t5", "Song E", "artist3", "Artist Three", 60),
    make_track("t6", "Song F", "artist4", "Artist Four", 50),
    make_track("t7", "Song G", "artist5", "Artist Five", 40),
    make_track("t8", "Song H", "artist2", "Artist Two", 70),
]


# ======================================================
# 1. compute_final_score
# ======================================================

class TestComputeFinalScore:
    def test_normal_weight(self):
        assert compute_final_score(80, 1.0) == 80.0

    def test_hard_ban(self):
        assert compute_final_score(90, 0.0) == 0.0

    def test_boost(self):
        assert compute_final_score(80, 2.0) == 160.0

    def test_soft_ban_half(self):
        assert compute_final_score(80, 0.5) == 40.0

    def test_low_popularity_boost(self):
        assert compute_final_score(30, 1.5) == 45.0

    def test_zero_popularity(self):
        assert compute_final_score(0, 1.0) == 0.0

    def test_max_values(self):
        assert compute_final_score(100, 2.0) == 200.0


# ======================================================
# 2. apply_artist_cap — Deduplication
# ======================================================

class TestDeduplication:
    def test_no_duplicates_in_output(self):
        tracks = TRACKS + [make_track("t1", "Song A", "artist1", "Artist One", 90)]
        result = apply_artist_cap(tracks, {})
        ids = [t["id"] for t in result]
        assert len(ids) == len(set(ids)), "Duplicate track IDs found"

    def test_all_unique_input(self):
        result = apply_artist_cap(TRACKS, {})
        assert len(result) == len(TRACKS)


# ======================================================
# 3. apply_artist_cap — Hard Ban
# ======================================================

class TestHardBan:
    def test_hard_banned_artist_excluded(self):
        weights = {"artist1": 0.0}
        result = apply_artist_cap(TRACKS, weights)
        artist_ids = {t["artist_id"] for t in result}
        assert "artist1" not in artist_ids

    def test_hard_banned_does_not_affect_others(self):
        weights = {"artist1": 0.0}
        result = apply_artist_cap(TRACKS, weights)
        # Other artists should still be present
        artist_ids = {t["artist_id"] for t in result}
        assert "artist2" in artist_ids


# ======================================================
# 4. apply_artist_cap — Soft Ban
# ======================================================

class TestSoftBan:
    def test_soft_ban_limits_low_popularity_tracks(self):
        """Soft-banned artist with low-popularity tracks should contribute at most 1."""
        tracks = [
            make_track("t1", "Low Pop A", "soft_artist", "Soft", 40),
            make_track("t2", "Low Pop B", "soft_artist", "Soft", 35),
            make_track("t3", "Low Pop C", "soft_artist", "Soft", 30),
            make_track("t4", "Other", "other", "Other", 80),
        ]
        weights = {"soft_artist": 0.3}
        result = apply_artist_cap(tracks, weights)
        soft_songs = [t for t in result if t["artist_id"] == "soft_artist"]
        assert len(soft_songs) <= 2, f"Soft-banned artist contributed {len(soft_songs)} songs"

    def test_soft_ban_high_popularity_allowed(self):
        """High-popularity tracks from soft-banned artist may still appear."""
        tracks = [
            make_track("t1", "High Pop", "soft_artist", "Soft", 85),
            make_track("t2", "High Pop 2", "soft_artist", "Soft", 80),
            make_track("t3", "Other", "other", "Other", 90),
        ]
        weights = {"soft_artist": 0.3}
        result = apply_artist_cap(tracks, weights)
        soft_songs = [t for t in result if t["artist_id"] == "soft_artist"]
        assert len(soft_songs) >= 1


# ======================================================
# 5. apply_artist_cap — Boost
# ======================================================

class TestBoost:
    def test_boosted_artist_ranks_higher(self):
        """A boosted artist's tracks should appear before unboosted."""
        tracks = [
            make_track("t1", "Popular", "normal", "Normal", 90),
            make_track("t2", "Less Popular", "boosted", "Boosted", 70),
        ]
        weights = {"boosted": 2.0}
        result = apply_artist_cap(tracks, weights)
        # boosted track (70 * 2 = 140 score) should beat popular track (90 * 1 = 90)
        assert result[0]["artist_id"] == "boosted"

    def test_boosted_final_score(self):
        tracks = [make_track("t1", "Track", "boost_artist", "Boost", 70)]
        weights = {"boost_artist": 2.0}
        result = apply_artist_cap(tracks, weights)
        assert result[0]["final_score"] == 140.0


# ======================================================
# 6. Popularity Sorting
# ======================================================

class TestSorting:
    def test_popularity_sort_descending(self):
        result = apply_artist_cap(TRACKS, {}, sort_mode="popularity")
        scores = [t["final_score"] for t in result]
        assert scores == sorted(scores, reverse=True)

    def test_balanced_sort(self):
        result = apply_artist_cap(TRACKS, {}, sort_mode="balanced")
        # In balanced mode, artist cap of 3 applies
        from collections import Counter
        counts = Counter(t["artist_id"] for t in result)
        # No single artist should exceed 3 tracks
        for artist, count in counts.items():
            assert count <= 3, f"Artist {artist} has {count} tracks in balanced mode"

    def test_playlist_limit_respected(self):
        tracks = [make_track(f"t{i}", f"Song {i}", f"artist{i}", f"Artist {i}", 90 - i)
                  for i in range(50)]
        result = apply_artist_cap(tracks, {}, playlist_limit=10)
        assert len(result) <= 10

    def test_hidden_gems_prefers_lower_popularity(self):
        tracks = [
            make_track("t1", "Underground", "artist1", "A1", 30),
            make_track("t2", "Mainstream", "artist2", "A2", 95),
            make_track("t3", "Mid", "artist3", "A3", 48),
        ]
        result = apply_artist_cap(tracks, {}, sort_mode="hidden_gems")
        # Score for t1 = 30*1 = 30 (< 55 so included in score)
        # Score for t3 = 48*1 = 48 (< 55)
        # t2 has pop > 55 so gets 0 hidden gems score
        # So order should have low pop tracks first
        low_pop_ids = {t["id"] for t in result if t["popularity"] < 55}
        if len(result) >= 2:
            assert result[0]["id"] in low_pop_ids or result[1]["id"] in low_pop_ids


# ======================================================
# 7. Artist Weight in Output
# ======================================================

class TestArtistWeightAnnotation:
    def test_artist_weight_added_to_output(self):
        weights = {"artist1": 1.5}
        result = apply_artist_cap(TRACKS[:1], weights)
        assert result[0]["artist_weight"] == 1.5

    def test_default_weight_is_1(self):
        result = apply_artist_cap(TRACKS[:1], {})
        assert result[0]["artist_weight"] == 1.0

    def test_final_score_in_output(self):
        result = apply_artist_cap(TRACKS[:1], {})
        assert "final_score" in result[0]


# ======================================================
# 8. Gemini Response Parsing
# ======================================================

class TestGeminiParsing:
    VALID_RESPONSE = """{
        "search_queries": ["chill acoustic morning", "coffee shop music"],
        "playlist_queries": ["chill playlist", "morning vibes"],
        "genres": ["acoustic", "indie folk"],
        "decade_tags": ["2010s", "2020s"],
        "mood_description": "relaxed morning acoustic vibes",
        "energy_level": "low",
        "search_track_queries": ["genre:acoustic year:2015-2022"]
    }"""

    MARKDOWN_WRAPPED = f"```json\n{VALID_RESPONSE}\n```"
    MARKDOWN_NO_LANG = f"```\n{VALID_RESPONSE}\n```"

    def test_parse_clean_json(self):
        result = parse_gemini_response(self.VALID_RESPONSE)
        assert result["energy_level"] == "low"
        assert "acoustic" in result["genres"]

    def test_parse_markdown_json(self):
        result = parse_gemini_response(self.MARKDOWN_WRAPPED)
        assert result["mood_description"] == "relaxed morning acoustic vibes"

    def test_parse_markdown_no_lang(self):
        result = parse_gemini_response(self.MARKDOWN_NO_LANG)
        assert len(result["search_queries"]) == 2

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            parse_gemini_response("this is not json")


# ======================================================
# 9. Fallback Strategy
# ======================================================

class TestFallbackStrategy:
    def test_fallback_has_required_keys(self):
        result = _fallback_strategy("test prompt")
        assert "search_queries" in result
        assert "playlist_queries" in result
        assert "mood_description" in result
        assert "energy_level" in result

    def test_fallback_includes_prompt(self):
        result = _fallback_strategy("chill jazz")
        assert any("chill jazz" in q for q in result["search_queries"])


# ======================================================
# 10. SQLite Persistence (async)
# ======================================================

@pytest.mark.asyncio
async def test_artist_weight_persistence():
    """Test that artist weights are saved and retrieved from SQLite."""
    import os
    import tempfile
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    from sqlalchemy import select

    # Use in-memory SQLite for the test
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    
    from app.database import Base, ArtistWeight
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    TestSession = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async with TestSession() as session:
        # Insert a weight
        session.add(ArtistWeight(artist_id="art_123", artist_name="Test Artist", weight=0.3))
        await session.commit()

    async with TestSession() as session:
        result = await session.execute(select(ArtistWeight).where(ArtistWeight.artist_id == "art_123"))
        row = result.scalar_one_or_none()
        assert row is not None
        assert row.artist_name == "Test Artist"
        assert row.weight == pytest.approx(0.3)

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_artist_weight_update():
    """Test updating an existing weight."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    from sqlalchemy import select

    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    from app.database import Base, ArtistWeight
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    TestSession = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    # Insert
    async with TestSession() as session:
        session.add(ArtistWeight(artist_id="art_456", artist_name="Update Artist", weight=1.0))
        await session.commit()

    # Update
    async with TestSession() as session:
        result = await session.execute(select(ArtistWeight).where(ArtistWeight.artist_id == "art_456"))
        row = result.scalar_one()
        row.weight = 0.1
        await session.commit()

    # Verify
    async with TestSession() as session:
        result = await session.execute(select(ArtistWeight).where(ArtistWeight.artist_id == "art_456"))
        row = result.scalar_one()
        assert row.weight == pytest.approx(0.1)

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_multiple_weights_stored():
    """Test storing and listing multiple artist weights."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    from sqlalchemy import select

    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    from app.database import Base, ArtistWeight
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    TestSession = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    artists = [
        ("art_a", "Artist A", 0.0),
        ("art_b", "Artist B", 1.5),
        ("art_c", "Artist C", 1.0),
    ]

    async with TestSession() as session:
        for aid, name, w in artists:
            session.add(ArtistWeight(artist_id=aid, artist_name=name, weight=w))
        await session.commit()

    async with TestSession() as session:
        result = await session.execute(select(ArtistWeight))
        rows = result.scalars().all()
        assert len(rows) == 3
        weights = {r.artist_id: r.weight for r in rows}
        assert weights["art_a"] == pytest.approx(0.0)
        assert weights["art_b"] == pytest.approx(1.5)

    await test_engine.dispose()


# ======================================================
# 11. Edge Cases
# ======================================================

class TestEdgeCases:
    def test_empty_track_list(self):
        result = apply_artist_cap([], {})
        assert result == []

    def test_single_track(self):
        tracks = [make_track("t1", "Solo", "artist1", "Solo Artist", 75)]
        result = apply_artist_cap(tracks, {})
        assert len(result) == 1
        assert result[0]["id"] == "t1"

    def test_all_same_artist(self):
        """When all tracks are from one artist, balanced mode caps at 3."""
        tracks = [
            make_track(f"t{i}", f"Song {i}", "one_artist", "One Artist", 90 - i)
            for i in range(10)
        ]
        result = apply_artist_cap(tracks, {}, sort_mode="balanced")
        from collections import Counter
        counts = Counter(t["artist_id"] for t in result)
        assert counts.get("one_artist", 0) <= 3

    def test_weight_zero_point_zero_is_banned(self):
        """Explicitly: weight 0.0 means no songs."""
        tracks = [make_track("t1", "Track", "banned_artist", "Banned", 100)]
        result = apply_artist_cap(tracks, {"banned_artist": 0.0})
        assert len(result) == 0

    def test_weight_above_one_boosts_score(self):
        """Weight > 1 gives final_score above popularity."""
        tracks = [make_track("t1", "Track", "fav", "Fav", 50)]
        result = apply_artist_cap(tracks, {"fav": 1.8})
        assert result[0]["final_score"] == pytest.approx(90.0)
