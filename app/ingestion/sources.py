import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import httpx

from app.config import settings

log = logging.getLogger(__name__)

S2_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_FIELDS = "paperId,title,abstract,authors,year,url"
ARXIV_URL = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
TIMEOUT = 15.0


# A plain dataclass: this is an internal normalization struct we build ourselves,
# so Pydantic validation would only re-check values we just assigned.
@dataclass
class PaperRecord:
    source: str
    external_id: str
    title: str
    abstract: str
    authors: list[str]
    year: int | None
    url: str | None


def _get(url: str, params: dict, client: httpx.Client | None, headers: dict | None = None) -> httpx.Response | None:
    """GET, or None when the source is unreachable/erroring — callers degrade to []."""
    owned = client is None
    client = client or httpx.Client(timeout=TIMEOUT)
    try:
        response = client.get(url, params=params, headers=headers)
        response.raise_for_status()
        return response
    except httpx.HTTPError as exc:
        log.warning("%s failed, skipping this source: %s", url, exc)
        return None
    finally:
        if owned:
            client.close()


def fetch_semantic_scholar(topic: str, limit: int, client: httpx.Client | None = None) -> list[PaperRecord]:
    headers = {"x-api-key": settings.SEMANTIC_SCHOLAR_API_KEY} if settings.SEMANTIC_SCHOLAR_API_KEY else None
    response = _get(S2_URL, {"query": topic, "limit": limit, "fields": S2_FIELDS}, client, headers)
    if response is None:
        return []
    try:
        data = response.json().get("data") or []
    except ValueError as exc:
        log.warning("semantic scholar returned a non-JSON body, skipping this source: %s", exc)
        return []

    papers = []
    for item in data:
        abstract = (item.get("abstract") or "").strip()
        if not (item.get("paperId") and abstract):
            log.warning("skipping semantic scholar paper %r: no id or no abstract", item.get("title"))
            continue
        papers.append(
            PaperRecord(
                source="semantic_scholar",
                external_id=item["paperId"],
                title=item.get("title") or "",
                abstract=abstract,
                authors=[a.get("name", "") for a in item.get("authors") or []],
                year=item.get("year"),
                url=item.get("url"),
            )
        )
    return papers


def fetch_arxiv(topic: str, limit: int, client: httpx.Client | None = None) -> list[PaperRecord]:
    params = {"search_query": f"all:{topic}", "start": 0, "max_results": limit}
    response = _get(ARXIV_URL, params, client)
    if response is None:
        return []
    try:
        feed = ET.fromstring(response.text)
    except ET.ParseError as exc:
        log.warning("arxiv returned unparseable XML, skipping this source: %s", exc)
        return []

    papers = []
    for entry in feed.iter(f"{ATOM}entry"):
        entry_id = _text(entry, "id")
        abstract = _text(entry, "summary")
        if not (entry_id and abstract):
            log.warning("skipping arxiv entry %r: no id or no abstract", _text(entry, "title"))
            continue
        published = _text(entry, "published")
        papers.append(
            PaperRecord(
                source="arxiv",
                external_id=entry_id.rsplit("/", 1)[-1],  # http://arxiv.org/abs/1706.03762v7 -> 1706.03762v7
                title=_text(entry, "title"),
                abstract=abstract,
                authors=[_text(a, "name") for a in entry.iter(f"{ATOM}author")],
                year=int(published[:4]) if published[:4].isdigit() else None,
                url=entry_id,
            )
        )
    return papers


def _text(element: ET.Element, tag: str) -> str:
    """Child tag's text with arXiv's line wrapping collapsed."""
    child = element.find(f"{ATOM}{tag}")
    return " ".join((child.text or "").split()) if child is not None else ""
