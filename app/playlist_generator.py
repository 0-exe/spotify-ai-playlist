"""Core playlist generation logic with scoring and artist weighting."""
import logging
import time
from typing import Any

import spotipy

logger = logging.getLogger(__name__)

# Max songs a soft-banned artist can contribute
SOFTBAN_MAX_SONGS = 2
# Max songs any single artist contributes in "balanced" mode
DEFAULT_ARTIST_CAP = 3


def compute_final_score(popularity: int, artist_weight: float) -> float:
    """
    Compute a final score for a track.
    
    Score = popularity * artist_weight
    Range: 0 (hard-banned) to 200 (max popularity + 2x boost)
    """
    return popularity * artist_weight


def apply_artist_cap(
    tracks: list[dict[str, Any]],
    artist_weights: dict[str, float],
    sort_mode: str = "popularity",
    playlist_limit: int = 40,
) -> list[dict[str, Any]]:
    """
    Apply artist weighting, caps, and sorting to a list of tracks.
    
    Args:
        tracks: List of track dicts with keys: id, name, artist_id, artist_name,
                popularity, album_name, album_image, preview_url, spotify_url
        artist_weights: Dict mapping artist_id -> weight (default 1.0)
        sort_mode: "popularity" | "balanced" | "hidden_gems"
        playlist_limit: Maximum number of tracks to return

    Returns:
        Sorted and capped list of tracks with final_score and artist_weight added.
    """
    # Deduplicate by track id
    seen_ids: set[str] = set()
    unique_tracks = []
    for t in tracks:
        if t["id"] not in seen_ids:
            seen_ids.add(t["id"])
            unique_tracks.append(t)

    # Annotate tracks with weight and score
    for track in unique_tracks:
        aid = track["artist_id"]
        weight = artist_weights.get(aid, 1.0)
        track["artist_weight"] = weight
        track["final_score"] = compute_final_score(track["popularity"], weight)

    # Remove hard-banned (weight == 0)
    eligible = [t for t in unique_tracks if t["artist_weight"] > 0]

    # Sort by mode
    if sort_mode == "hidden_gems":
        # Prefer lower popularity (< 50) with good weight
        eligible.sort(key=lambda t: (-(t["final_score"] if t["popularity"] < 55 else 0), t["popularity"]))
    elif sort_mode == "balanced":
        eligible.sort(key=lambda t: -t["final_score"])
    else:  # popularity (default)
        eligible.sort(key=lambda t: -t["final_score"])

    # Apply per-artist caps
    artist_song_count: dict[str, int] = {}
    result = []

    for track in eligible:
        aid = track["artist_id"]
        weight = track["artist_weight"]
        count = artist_song_count.get(aid, 0)

        # Determine cap for this artist
        if weight < 0.5:
            cap = SOFTBAN_MAX_SONGS  # soft-banned: max 2 songs only if score is very high
            # Only include if it was in top-tier by raw popularity
            if track["popularity"] < 70 and count >= 1:
                continue
        elif sort_mode == "balanced":
            cap = DEFAULT_ARTIST_CAP
        else:
            cap = playlist_limit  # no cap in pure popularity mode

        if count >= cap:
            continue

        result.append(track)
        artist_song_count[aid] = count + 1

        if len(result) >= playlist_limit:
            break

    return result


def search_spotify_tracks(
    sp: spotipy.Spotify,
    search_strategy: dict[str, Any],
    max_per_query: int = 20,
) -> list[dict[str, Any]]:
    """
    Search Spotify for tracks using the AI-generated search strategy.
    Returns a flat list of raw track dicts.
    """
    all_tracks = []
    seen_ids: set[str] = set()

    def _extract_tracks(items: list) -> None:
        for item in items or []:
            if not item or item.get("id") in seen_ids:
                continue
            seen_ids.add(item["id"])
            
            artists = item.get("artists", [{}])
            primary_artist = artists[0] if artists else {}
            album = item.get("album", {})
            images = album.get("images", [{}])
            
            all_tracks.append({
                "id": item["id"],
                "name": item["name"],
                "artist_id": primary_artist.get("id", ""),
                "artist_name": primary_artist.get("name", "Unknown"),
                "all_artists": [a.get("name", "") for a in artists],
                "popularity": item.get("popularity", 0),
                "album_name": album.get("name", ""),
                "album_image": images[0].get("url", "") if images else "",
                "preview_url": item.get("preview_url", ""),
                "spotify_url": item.get("external_urls", {}).get("spotify", ""),
                "uri": item.get("uri", ""),
                "duration_ms": item.get("duration_ms", 0),
            })

    # Search tracks by direct queries
    queries = search_strategy.get("search_queries", [])
    advanced_queries = search_strategy.get("search_track_queries", [])
    
    for query in queries + advanced_queries:
        try:
            results = sp.search(q=query, type="track", limit=max_per_query)
            _extract_tracks(results.get("tracks", {}).get("items", []))
            time.sleep(0.1)  # Rate limit courtesy
        except spotipy.exceptions.SpotifyException as e:
            if e.http_status == 429:
                retry_after = int(e.headers.get("Retry-After", 2))
                logger.warning(f"Rate limited. Waiting {retry_after}s...")
                time.sleep(retry_after)
            else:
                logger.error(f"Spotify search error for '{query}': {e}")

    # Search public playlists and extract their tracks
    playlist_queries = search_strategy.get("playlist_queries", [])
    for query in playlist_queries[:3]:  # Limit playlist queries
        try:
            results = sp.search(q=query, type="playlist", limit=5)
            playlists = results.get("playlists", {}).get("items", [])
            
            for pl in playlists[:3]:
                if not pl:
                    continue
                try:
                    pl_tracks = sp.playlist_tracks(
                        pl["id"],
                        fields="items(track(id,name,artists,album,popularity,preview_url,external_urls,uri,duration_ms))",
                        limit=30,
                    )
                    items = [
                        item["track"]
                        for item in pl_tracks.get("items", [])
                        if item and item.get("track")
                    ]
                    _extract_tracks(items)
                    time.sleep(0.1)
                except Exception as e:
                    logger.warning(f"Failed to get tracks from playlist {pl.get('id')}: {e}")
        except spotipy.exceptions.SpotifyException as e:
            if e.http_status == 429:
                retry_after = int(e.headers.get("Retry-After", 2))
                logger.warning(f"Rate limited. Waiting {retry_after}s...")
                time.sleep(retry_after)
            else:
                logger.error(f"Spotify playlist search error for '{query}': {e}")

    return all_tracks


def create_spotify_playlist(
    sp: spotipy.Spotify,
    tracks: list[dict[str, Any]],
    playlist_name: str,
    description: str = "",
) -> dict[str, Any]:
    """
    Create or update a Spotify playlist with the given tracks.
    Returns the created/updated playlist info.
    """
    user_id = sp.current_user()["id"]
    
    # Check if playlist with this name already exists
    existing_playlist = None
    offset = 0
    while True:
        playlists = sp.current_user_playlists(limit=50, offset=offset)
        for pl in playlists.get("items", []):
            if pl and pl.get("name") == playlist_name:
                existing_playlist = pl
                break
        if existing_playlist or not playlists.get("next"):
            break
        offset += 50
    
    if existing_playlist:
        # Update existing playlist
        pl_id = existing_playlist["id"]
        sp.playlist_change_details(pl_id, description=description)
        sp.playlist_replace_items(pl_id, [t["uri"] for t in tracks[:100]])
        logger.info(f"Updated existing playlist: {playlist_name}")
    else:
        # Create new playlist
        pl = sp.user_playlist_create(
            user=user_id,
            name=playlist_name,
            public=True,
            description=description,
        )
        pl_id = pl["id"]
        sp.playlist_add_items(pl_id, [t["uri"] for t in tracks[:100]])
        logger.info(f"Created new playlist: {playlist_name}")
    
    # Return playlist info
    result = sp.playlist(pl_id)
    return {
        "id": pl_id,
        "name": result.get("name"),
        "url": result.get("external_urls", {}).get("spotify", ""),
        "uri": result.get("uri", ""),
        "tracks_added": len(tracks),
    }


def start_playback(
    sp: spotipy.Spotify,
    playlist_uri: str,
    device_name: str | None = None,
) -> bool:
    """Start playback of a playlist on a device."""
    try:
        device_id = None
        if device_name:
            devices = sp.devices()
            for dev in devices.get("devices", []):
                if dev.get("name", "").lower() == device_name.lower():
                    device_id = dev["id"]
                    break
            if not device_id:
                logger.warning(f"Device '{device_name}' not found, using active device.")
        
        sp.start_playback(device_id=device_id, context_uri=playlist_uri)
        return True
    except Exception as e:
        logger.error(f"Failed to start playback: {e}")
        return False
