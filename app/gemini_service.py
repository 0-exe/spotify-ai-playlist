"""Gemini AI service for translating prompts into Spotify search strategies."""
import json
import logging
import re
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a music expert and playlist curator. 
Your task is to translate a user's mood/vibe description into structured Spotify search strategies.
Return ONLY valid JSON, no markdown, no explanation."""

SEARCH_STRATEGY_PROMPT = """Given this playlist mood/vibe description: "{prompt}"

Generate a JSON object with these fields:
{{
  "search_queries": ["query1", "query2", ...],  // 5-8 Spotify track search queries
  "playlist_queries": ["query1", "query2", ...],  // 3-5 queries to find relevant public playlists
  "genres": ["genre1", "genre2", ...],  // 3-6 relevant genres
  "decade_tags": ["2010s", "2000s", ...],  // relevant decades if applicable
  "mood_description": "brief mood summary",
  "energy_level": "low|medium|high",
  "search_track_queries": ["artist:name genre:pop year:2010-2020", ...]  // 3-5 advanced Spotify search queries
}}

Be creative and specific. For example, "White Girl Music 2010s Throwback" might yield queries like 
"Taylor Swift 2012", "Katy Perry teenage dream", "indie pop 2012 hits", etc.
For "Dark Driving Techno": "dark techno berlin", "industrial techno driving", etc."""


async def get_search_strategy(prompt: str) -> dict[str, Any]:
    """Use Gemini to generate a search strategy for the given mood/vibe prompt."""
    settings = get_settings()
    
    if not settings.gemini_api_key:
        logger.warning("Gemini API key not set, using fallback search strategy")
        return _fallback_strategy(prompt)
    
    try:
        from google import genai
        from google.genai import types
        
        client = genai.Client(api_key=settings.gemini_api_key)
        
        full_prompt = SEARCH_STRATEGY_PROMPT.format(prompt=prompt)
        
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=full_prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.7,
                max_output_tokens=1024,
            ),
        )
        
        text = response.text.strip()
        # Strip markdown code fences if present
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        
        result = json.loads(text)
        logger.info(f"Gemini search strategy generated for: {prompt!r}")
        return result
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Gemini response as JSON: {e}")
        return _fallback_strategy(prompt)
    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        return _fallback_strategy(prompt)


def _fallback_strategy(prompt: str) -> dict[str, Any]:
    """Fallback search strategy when Gemini is unavailable."""
    words = prompt.lower().split()
    return {
        "search_queries": [prompt, f"{prompt} hits", f"best {prompt}"],
        "playlist_queries": [prompt, f"{prompt} playlist"],
        "genres": [],
        "decade_tags": [],
        "mood_description": prompt,
        "energy_level": "medium",
        "search_track_queries": [prompt],
    }


def parse_gemini_response(text: str) -> dict[str, Any]:
    """Parse a Gemini JSON response, stripping markdown if needed."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```\s*$", "", text, flags=re.MULTILINE)
    return json.loads(text)
