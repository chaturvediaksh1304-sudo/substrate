"""A local-model stand-in for the Anthropic client, so Substrate runs with no API key.

Duck-typed on purpose. The six call sites use exactly one method —
`.messages.create(model=, max_tokens=, system=, messages=)` returning `.content` blocks
with `.type` and `.text` — so reproducing that surface keeps the seam at
`claude._client()` and leaves six already-tested agent modules untouched.

Failures are raised as `anthropic.APIError`: see `_error`.
"""

import logging
from dataclasses import dataclass

import anthropic
import httpx

from app.config import settings

log = logging.getLogger(__name__)

# A 7B on an M3 Pro answers in tens of seconds, not the ~1s a hosted API takes; connecting,
# though, either works at once or not at all.
TIMEOUT = httpx.Timeout(300.0, connect=5.0)
# Ollama defaults to a 4096-token window, which the 2000-token synthesis and graph prompts
# can silently overrun — and silent truncation is the one failure Rules.md won't degrade to.
# ponytail: one number for every call site; make it a setting if a prompt ever outgrows it.
NUM_CTX = 8192


@dataclass(frozen=True)
class TextBlock:
    text: str
    type: str = "text"


@dataclass(frozen=True)
class Message:
    content: list[TextBlock]


def _error(message: str, request: httpx.Request) -> anthropic.APIError:
    """Every Ollama failure leaves here as an `anthropic.APIError`.

    The six call sites and main.py's five routes already catch that type and turn it into
    a 503 with the server's own message. Raising raw httpx errors instead would escape as
    unhandled 500s, so the alternative to this line is fifteen widened `except` clauses.
    """
    return anthropic.APIError(message, request, body=None)


def _decoded(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return None


def _why(response: httpx.Response) -> str:
    payload = _decoded(response)
    detail = str(payload.get("error") or "") if isinstance(payload, dict) else ""
    if response.status_code == 404:
        # Ollama 404s a model it hasn't pulled. That is a fixable setup step, not an outage.
        return f"{detail or f'model {settings.OLLAMA_MODEL!r} not found'} — run `ollama pull {settings.OLLAMA_MODEL}`"
    return detail or f"HTTP {response.status_code} from {response.request.url}"


class _Messages:
    def __init__(self, http: httpx.Client):
        self._http = http

    def create(
        self, *, model: str, max_tokens: int, system: str, messages: list[dict]
    ) -> Message:
        """`model` is the caller's Anthropic model and is deliberately discarded.

        Only `OLLAMA_MODEL` names a model this server has, and substituting here rather
        than at the six call sites is what lets them stay provider-agnostic.
        """
        url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/chat"
        payload = {
            "model": settings.OLLAMA_MODEL,
            # Anthropic takes the system prompt as a kwarg; Ollama takes it as a message.
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": False,
            "options": {"num_predict": max_tokens, "num_ctx": NUM_CTX},
        }
        try:
            response = self._http.post(url, json=payload)
        except httpx.RequestError as exc:
            raise _error(
                f"cannot reach {settings.OLLAMA_BASE_URL} ({exc}) — is `ollama serve` running?",
                exc.request,
            ) from exc
        if response.is_error:
            raise _error(_why(response), response.request)

        body = _decoded(response)
        text = ""
        if isinstance(body, dict):
            text = str((body.get("message") or {}).get("content") or "")
        if not text:
            # No text is a non-answer, not a crash: every call site already treats an
            # unparseable reply as a dropped item (Rules.md: degrade, log, continue).
            log.warning("ollama: reply carried no text: %.200s", response.text)
        return Message(content=[TextBlock(text=text)])


class OllamaClient:
    """Only `.messages.create` exists, because only `.messages.create` is called."""

    def __init__(self, http: httpx.Client | None = None):
        self.messages = _Messages(http or httpx.Client(timeout=TIMEOUT))
