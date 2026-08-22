"""NewsDataSource: Eastmoney 7×24 + CLS telegraph (keyless mainland feed)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .client import (
    ClsNewsClient,
    EastmoneyNewsClient,
    EastmoneyRequestError,
    bare_a_share,
    normalize_cls,
    normalize_fast_news,
    normalize_stock_news,
)

logger = logging.getLogger(__name__)


class EastmoneyNewsSource:
    """Mainland finance news. General feed merges 东财 7×24 + 财联社; tickers
    hit Eastmoney CMS search for A-share codes only."""

    def __init__(self) -> None:
        self._em = EastmoneyNewsClient()
        self._cls = ClsNewsClient()

    async def get_news(
        self,
        tickers: list[str] | None = None,
        limit: int = 20,
        published_after: str | None = None,
        published_before: str | None = None,
        cursor: str | None = None,
        order: str | None = None,
        sort: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        ignored = {
            k: v
            for k, v in {
                "published_after": published_after,
                "published_before": published_before,
                "cursor": cursor,
                "order": order,
                "sort": sort,
            }.items()
            if v is not None
        }
        if ignored:
            logger.debug("eastmoney.news: ignoring unsupported params: %s", ignored)

        if tickers:
            a_pairs = [
                (t, code) for t in tickers if (code := bare_a_share(t))
            ]
            results = await self._stock_news(a_pairs, limit) if a_pairs else []
        else:
            results = await self._general_feed(limit)

        return {"results": results, "count": len(results), "next_cursor": None}

    async def _general_feed(self, limit: int) -> list[dict[str, Any]]:
        em_rows, cls_rows = await asyncio.gather(
            self._safe_fast(limit),
            self._safe_cls(limit),
        )
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for article in em_rows + cls_rows:
            key = article.get("id") or article.get("title")
            if not key or key in seen or not article.get("title"):
                continue
            seen.add(str(key))
            merged.append(article)
        merged.sort(key=lambda a: a.get("published_at") or "", reverse=True)
        return merged[:limit]

    async def _stock_news(
        self, pairs: list[tuple[str, str]], limit: int
    ) -> list[dict[str, Any]]:
        per = max(limit // max(len(pairs), 1), 5)

        async def _one(ticker: str, code: str) -> list[dict[str, Any]]:
            try:
                raw = await self._em.get_stock_news(code, limit=per)
            except EastmoneyRequestError:
                logger.exception("eastmoney.news.stock_failed | code=%s", code)
                return []
            return [normalize_stock_news(r, ticker) for r in raw if r.get("title")]

        batches = await asyncio.gather(*[_one(t, c) for t, c in pairs])
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for batch in batches:
            for article in batch:
                key = article.get("id") or article.get("title")
                if not key or key in seen:
                    continue
                seen.add(str(key))
                merged.append(article)
        merged.sort(key=lambda a: a.get("published_at") or "", reverse=True)
        return merged[:limit]

    async def _safe_fast(self, limit: int) -> list[dict[str, Any]]:
        try:
            raw = await self._em.get_fast_news(limit=limit)
        except EastmoneyRequestError:
            logger.exception("eastmoney.news.fast_failed")
            return []
        return [normalize_fast_news(r) for r in raw if r.get("title")]

    async def _safe_cls(self, limit: int) -> list[dict[str, Any]]:
        try:
            raw = await self._cls.get_roll(limit=limit)
        except EastmoneyRequestError:
            logger.exception("eastmoney.news.cls_failed")
            return []
        return [normalize_cls(r) for r in raw if r.get("title") or r.get("brief")]

    async def get_news_article(
        self, article_id: str, user_id: str | None = None
    ) -> dict[str, Any] | None:
        try:
            for article in await self._general_feed(80):
                if article.get("id") == article_id:
                    return article
        except Exception:
            logger.exception("eastmoney.news.article_lookup_failed")
        return None

    async def close(self) -> None:
        await asyncio.gather(
            self._em.close(), self._cls.close(), return_exceptions=True
        )
