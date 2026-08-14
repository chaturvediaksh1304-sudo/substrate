import anthropic

from app.config import settings


class MissingAPIKeyError(RuntimeError):
    """ANTHROPIC_API_KEY is unset. Hard-failing beats returning a non-answer that looks like one."""


_CLIENT: anthropic.Anthropic | None = None


def _client() -> anthropic.Anthropic:
    """Lazy singleton, like embed.py's model: importing without a key is fine, calling is not."""
    global _CLIENT
    if not settings.ANTHROPIC_API_KEY:
        raise MissingAPIKeyError("ANTHROPIC_API_KEY is not set; Claude cannot be called")
    if _CLIENT is None:
        _CLIENT = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _CLIENT
