import logging

from groq import Groq

import config

log = logging.getLogger(__name__)

# whisper-large-v3-turbo: fast and cheap with strong accuracy. Swap to
# "whisper-large-v3" if you need maximum accuracy on noisy/accented audio.
_MODEL = "whisper-large-v3-turbo"

# Content types / extensions Discord uses for voice memos and audio uploads.
AUDIO_EXTENSIONS = (".ogg", ".oga", ".mp3", ".m4a", ".wav", ".webm", ".flac", ".mp4")

_client: Groq | None = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        if not config.GROQ_API_KEY:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Add it to your .env to enable voice memos."
            )
        _client = Groq(api_key=config.GROQ_API_KEY)
    return _client


def transcribe(audio_bytes: bytes, filename: str) -> str:
    """Transcribe audio bytes (e.g. a Discord voice memo) into plain text."""
    client = _get_client()
    resp = client.audio.transcriptions.create(
        file=(filename, audio_bytes),
        model=_MODEL,
        response_format="text",
    )
    # With response_format="text" the SDK returns the raw string; be defensive
    # in case a future version returns an object with a .text attribute.
    text = resp if isinstance(resp, str) else getattr(resp, "text", "")
    return (text or "").strip()
