"""YouTube transcript fetcher using youtube-transcript-api.

Usage:
    from src.youtube import fetch_transcript

    result = fetch_transcript("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    # {"video_id": "dQw4w9WgXcQ", "text": "...full transcript text..."}
    # or {"error": "..."}
"""

import re

from youtube_transcript_api import YouTubeTranscriptApi


def extract_video_id(url: str) -> str | None:
    """Extract an 11-character YouTube video ID from a URL or bare ID."""
    patterns = [
        r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})",
        r"^([A-Za-z0-9_-]{11})$",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def fetch_transcript(url: str) -> dict:
    """Fetch transcript for a YouTube URL and return plain text.

    Returns:
        {"video_id": str, "text": str}   on success
        {"error": str}                    on failure
    """
    video_id = extract_video_id(url.strip())
    if not video_id:
        return {"error": f"Could not extract video ID from: {url}"}

    api = YouTubeTranscriptApi()
    transcript = None

    # Try English first, then fall back to any available language
    try:
        transcript = api.fetch(video_id, languages=["en"])
    except Exception:
        pass

    if transcript is None:
        try:
            for t in api.list(video_id):
                try:
                    transcript = t.fetch()
                    break
                except Exception:
                    continue
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    if transcript is None:
        return {"error": "No transcripts available for this video"}

    text = " ".join(s.text.strip() for s in transcript.snippets if s.text.strip())
    return {"video_id": video_id, "text": text}
