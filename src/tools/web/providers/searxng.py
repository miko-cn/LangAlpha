"""SearXNG search provider — self-hosted / public instance, no API key.

Set ``SEARXNG_URL`` to the instance origin (e.g. ``https://searx.example``).
The instance must expose ``format=json``.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from langchain_core.tools import tool

from src.tools.web.providers._shared import (
    lazy,
    normalize_time_range,
    request_json,
    result_card,
)

logger = logging.getLogger(__name__)

_TIME_RANGE = {"d": "day", "w": "week", "m": "month", "y": "year", "h": "day"}


def _favicon_url(url: str) -> str:
    try:
        domain = urlparse(url).netloc.removeprefix("www.")
        if not domain:
            return ""
        return f"https://www.google.com/s2/favicons?domain={domain}&sz=32"
    except (ValueError, AttributeError):
        return ""


class SearxngAPI:
    def __init__(self, base_url: str | None = None):
        raw = (base_url or os.getenv("SEARXNG_URL") or "").rstrip("/")
        if not raw:
            raise ValueError("SEARXNG_URL not found in environment variables")
        self.base_url = raw

    async def search(
        self,
        query: str,
        count: int = 10,
        time_range: str | None = None,
        language: str = "zh-CN",
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        start = time.time()
        params: dict[str, Any] = {
            "q": query,
            "format": "json",
            "categories": "general",
            "language": language,
        }
        if time_range:
            params["time_range"] = time_range

        data = await request_json(
            "GET",
            f"{self.base_url}/search",
            provider="SearXNG",
            params=params,
            timeout=20.0,
        )
        elapsed = time.time() - start

        results: list[dict[str, Any]] = []
        cards: list[dict[str, Any]] = []
        for item in (data.get("results") or [])[:count]:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or ""
            url = item.get("url") or ""
            snippet = item.get("content") or ""
            if not title or not url:
                continue
            results.append(
                {
                    "type": "page",
                    "title": title,
                    "url": url,
                    "content": snippet,
                    "publish_time": item.get("publishedDate") or "",
                    "site_name": item.get("engine") or "",
                }
            )
            cards.append(
                result_card(
                    title=title,
                    url=url,
                    favicon=_favicon_url(url),
                    snippet=snippet,
                )
            )

        metadata = {
            "type": "web_search",
            "query": query,
            "search_engine": "searxng",
            "response_time": round(elapsed, 2),
            "total_results": len(results),
            "results": cards,
        }
        return results, metadata


def build_web_search_tool(
    max_results: int = 10,
    default_time_range: str | None = None,
    verbose: bool = True,
):
    """Build a per-request SearXNG web_search tool.

    Missing ``SEARXNG_URL`` is a per-call error, not a build crash.
    ``verbose`` is accepted for the uniform builder interface.
    """
    _get_api = lazy(SearxngAPI)

    @tool(response_format="content_and_artifact")
    async def web_search(
        query: str,
        time_range: (
            Literal["day", "week", "month", "year", "d", "w", "m", "y"] | None
        ) = None,
        language: str | None = None,
    ) -> tuple[list[dict[str, Any]] | str, dict[str, Any]]:
        """Search the web via a SearXNG instance (good for Chinese queries).

        Use when you need current information, news, or facts.

        Args:
            query: Search query (Chinese or English)
            time_range: Recency filter — day/week/month/year (or d/w/m/y)
            language: SearXNG language code (default zh-CN)
        """
        try:
            canonical = normalize_time_range(
                time_range, default_time_range, provider="SearXNG"
            )
            sx_range = _TIME_RANGE.get(canonical) if canonical else None
            api = _get_api()
            content, artifact = await api.search(
                query=query,
                count=max_results,
                time_range=sx_range,
                language=language or "zh-CN",
            )
            return content, artifact
        except (httpx.HTTPError, ValueError) as e:
            logger.exception("SearXNG search failed")
            return f"Search failed: {e}", {"error": str(e), "query": query}

    return web_search
