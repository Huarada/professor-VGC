"""Short, accessible copy for the model settings control."""

from __future__ import annotations

PROVIDER_NAMES = {"gemini": "Gemini", "openai": "OpenAI"}
_KEY_VARIABLES = {"gemini": "PROFESSORVGC_GEMINI_API_KEY", "openai": "PROFESSORVGC_OPENAI_API_KEY"}


def key_status_text(provider: str, has_key: bool) -> str:
    """Explain availability without exposing the selected provider's key."""
    label = PROVIDER_NAMES.get(provider, provider.title())
    if has_key:
        return f"{label} ready"
    return f"{label} needs a key. Open Settings for setup."


def key_setup_text(provider: str) -> str:
    """Name the BYOK setting only inside the on-demand settings panel."""
    variable = _KEY_VARIABLES.get(provider, f"PROFESSORVGC_{provider.upper()}_API_KEY")
    return f"Set `{variable}` and restart the app."
