#!/usr/bin/env python3
"""
Startup helper - copies .env.example to .env if not present and launches the server.
"""
import os
import shutil
import subprocess
import sys

if not os.path.exists(".env"):
    shutil.copy(".env.example", ".env")
    print("✅ Created .env from .env.example — please fill in your API keys!")
    print("   Open .env and set SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, GEMINI_API_KEY")
    sys.exit(0)

os.makedirs("data", exist_ok=True)
subprocess.run([sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"])
