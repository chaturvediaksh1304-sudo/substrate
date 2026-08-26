from typing import Any

import anthropic

from app.agents.ollama import OllamaClient
from app.config import settings


class MissingAPIKeyError(RuntimeError):
    """The configured provider has no credentials. Hard-failing beats returning a non-answer that looks like one."""


# Keyed by provider so flipping LLM_PROVIDER can't hand back the other provider's client.
_CLIENTS: dict[str, Any] = {}


def _client() -> anthropic.Anthropic | OllamaClient:
    """Lazy singleton, like embed.py's model: importing without a key is fine, calling is not.

    LLM_PROVIDER picks the client. OllamaClient is duck-typed to the single method the six
    call sites use, so nothing downstream knows or cares which one it got.
    """
    provider = settings.LLM_PROVIDER
    if provider == "anthropic" and not settings.ANTHROPIC_API_KEY:
        raise MissingAPIKeyError("ANTHROPIC_API_KEY is not configured")
    if provider not in _CLIENTS:
        _CLIENTS[provider] = (
            OllamaClient()
            if provider == "ollama"
            else anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        )
    return _CLIENTS[provider]


def unavailable(exc: Exception, consequence: str) -> str:
    """503 detail for a failed LLM call: which provider failed, why, and what didn't happen.

    The routes used to hardcode "Claude"; with a provider switch that would misname the
    thing that's actually down, and hide Ollama's own message (which says how to fix it).
    """
    provider = "Ollama" if settings.LLM_PROVIDER == "ollama" else "Claude"
    return f"{provider} is unavailable: {exc}; {consequence}"
