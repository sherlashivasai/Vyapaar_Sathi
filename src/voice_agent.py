"""Backward-compatible shim for older Gemini imports.

The active runtime now uses the Saarvam implementation from
``src.voice_agent_sarvam``. This file remains as a compatibility alias so older
imports continue to resolve without reintroducing the Google Gemini client.
"""

from src.voice_agent_sarvam import SarvamLive


class GeminiLive(SarvamLive):
    """Compatibility alias retained for projects still importing GeminiLive."""

    pass
