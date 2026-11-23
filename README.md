# Spotify AI Playlist Generator 🎵

A fully local web app that generates mood-based Spotify playlists using **Gemini 2.5 Flash AI** and **Spotipy**.

## Features

- 🧠 **AI-powered search** – Describe any mood/vibe and Gemini translates it into smart Spotify search queries
- 🎚️ **Artist weights** – Soft-ban, hard-ban, or boost artists with a slider (persisted in SQLite)
- 🔥 **Sort modes** – Popularity, Balanced, or Hidden Gems
- 🔊 **Spotify Connect** – Play directly on any active device
- 🏠 **Home Assistant ready** – Clean REST endpoint `POST /api/generate`
- 🐳 **Docker ready** – One-command deploy to Proxmox

---

## Quick Start

### Prerequisites

- Python 3.12+
- Spotify Developer App ([create one here](https://developer.spotify.com/dashboard))
- Gemini API Key ([get one here](https://aistudio.google.com/))

### 1. Set up Spotify App

In your [Spotify Developer Dashboard](https://developer.spotify.com/dashboard):
1. Create a new app
2. Add `http://localhost:8000/callback` to **Redirect URIs**
3. Copy your **Client ID** and **Client Secret**

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in your keys:
# SPOTIPY_CLIENT_ID=...
# SPOTIPY_CLIENT_SECRET=...
# GEMINI_API_KEY=...
```

### 3. Install & run locally

```bash
# Create virtual environment
python -m venv .venv

# Activate (Windows)
.venv\Scripts\activate

# Activate (macOS/Linux)
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create data directory and start server
mkdir data
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Open **http://localhost:8000** in your browser and click **Connect Spotify**.

---

## Project Structure

```
spotify-ui/
├── main.py                    # FastAPI app entry point
├── requirements.txt           # Python dependencies
├── .env.example               # Environment variable template
├── pytest.ini                 # Test configuration
├── start.py                   # Convenience startup script
├── Dockerfile                 # Docker build
├── docker-compose.yml         # Docker Compose for Proxmox
│
├── app/
│   ├── config.py              # Pydantic settings (reads .env)
│   ├── database.py            # SQLAlchemy async models (ArtistWeight)
│   ├── spotify_client.py      # Spotipy OAuth client singleton
│   ├── gemini_service.py      # Gemini AI search strategy generation
│   ├── playlist_generator.py  # Core scoring, sorting & cap logic
│   │
│   ├── routers/
│   │   ├── auth.py            # /login, /callback, /logout, /api/auth/status
│   │   ├── playlist.py        # POST /api/generate
│   │   ├── artists.py         # GET/POST/DELETE /api/artists/weights
│   │   └── devices.py         # GET /api/devices
│   │
│   ├── templates/
│   │   └── index.html         # Main UI
│   └── static/
│       ├── style.css          # Dark mode CSS
│       └── app.js             # Frontend JavaScript
│
├── tests/
│   └── test_playlist.py       # pytest test suite
│
└── data/                      # Created at runtime
    └── spotify_ui.db          # SQLite database (artist weights)
```

---

## Running Tests

```bash
# Activate venv first
.venv\Scripts\activate  # Windows

# Run all tests
pytest -v

# Run with coverage
pytest --cov=app --cov-report=term-missing
```

---

## Home Assistant Integration

Add to your `configuration.yaml`:

```yaml
rest_command:
  generate_spotify_playlist:
    url: "http://your-server:8000/api/generate"
    method: POST
    content_type: "application/json"
    payload: >
      {
        "prompt": "{{ prompt }}",
        "limit": 40,
        "create_on_spotify": true,
        "playlist_name": "HA Playlist",
        "device_name": "{{ device_name }}",
        "start_playback": true
      }
```

Then call it in an automation:
```yaml
service: rest_command.generate_spotify_playlist
data:
  prompt: "Chill Sunday Morning Acoustic"
  device_name: "Living Room Speaker"
```

---

## Docker / Proxmox Deploy

```bash
# Copy and configure .env
cp .env.example .env
# Edit .env with your keys

# Build and start
docker compose up -d

# View logs
docker compose logs -f
```

> **Note**: After first run, visit `http://your-server:8000/login` to authorize Spotify.
> The OAuth token is cached in the `spotify_cache` Docker volume.

---

## API Reference

### `POST /api/generate`

```json
{
  "prompt": "Dark Driving Techno",
  "limit": 40,
  "sort_mode": "popularity",
  "create_on_spotify": true,
  "playlist_name": "My Techno Playlist",
  "device_name": "NVIDIA SHIELD",
  "start_playback": false
}
```

Sort modes: `popularity` | `balanced` | `hidden_gems`

### `GET /api/devices`
Returns active Spotify Connect devices.

### `GET/POST/DELETE /api/artists/weights`
Manage per-artist weight factors (0.0–2.0).

### `GET /health`
Returns config status (safe for monitoring).

---

## Artist Weight System

| Weight | Effect |
|--------|--------|
| `0.0` | Hard ban — artist never appears |
| `0.1–0.4` | Soft ban — max 1 high-popularity song |
| `0.5–0.9` | Downweighted — fewer songs, lower ranking |
| `1.0` | Normal (default) |
| `1.1–1.5` | Slight boost |
| `1.6–2.0` | Strong boost — artist ranks much higher |

Final Score = `Popularity (0–100) × Artist Weight`
