"""Data source health status endpoint.

Returns the configured providers, their availability, market coverage,
and rate-limit settings — without making live API calls to each source.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.config.settings import get_rate_limit_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["data-sources"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class DataSourceRateLimit(BaseModel):
    """Rate limit settings for a single data source."""

    rate_per_sec: float
    burst: int


class DataSourceStatus(BaseModel):
    """Status of a single data source provider."""

    name: str
    available: bool
    markets: list[str] = Field(default_factory=list)
    intraday_markets: list[str] | None = None
    daily_markets: list[str] | None = None
    snapshot_markets: list[str] | None = None
    rate_limit: DataSourceRateLimit | None = None


class DataSourcesStatusResponse(BaseModel):
    """Response model for the data sources status endpoint."""

    sources: list[DataSourceStatus]
    total: int
    available: int


# ---------------------------------------------------------------------------
# Availability checks (mirrors src/data_client/registry.py)
# ---------------------------------------------------------------------------


def _check_ginlix_data() -> bool:
    return bool(os.getenv("GINLIX_DATA_URL"))


def _check_fmp() -> bool:
    return bool(os.getenv("FMP_API_KEY"))


def _check_yfinance() -> bool:
    try:
        import yfinance  # noqa: F401
        return True
    except ImportError:
        return False


def _check_futu() -> bool:
    from src.config.env import FUTU_ENABLED
    return FUTU_ENABLED


def _check_tushare() -> bool:
    from src.config.env import TUSHARE_ENABLED
    return TUSHARE_ENABLED


def _check_tencent() -> bool:
    return True  # free, keyless API


def _check_sina() -> bool:
    return True  # free, keyless API


_AVAILABILITY: dict[str, tuple[str, ...]] = {
    "ginlix-data": (),
    "fmp": (),
    "yfinance": (),
    "futu": (),
    "tushare": (),
    "tencent": (),
    "sina": (),
}

_CHECK_FN: dict[str, callable] = {
    "ginlix-data": _check_ginlix_data,
    "fmp": _check_fmp,
    "yfinance": _check_yfinance,
    "futu": _check_futu,
    "tushare": _check_tushare,
    "tencent": _check_tencent,
    "sina": _check_sina,
}


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/data-sources/status",
    response_model=DataSourcesStatusResponse,
    summary="Get data source status",
    description="List all configured market data providers with their availability, "
    "market coverage, and rate-limit settings.",
)
async def get_data_sources_status() -> DataSourcesStatusResponse:
    """Return the status of all configured market data sources.

    Checks each source's availability (credentials, imports) and reads
    its market coverage and rate-limit settings from config.yaml.  No
    live API calls are made — this is a fast, read-only snapshot.
    """
    from src.config.settings import get_infrastructure_config

    cfg = get_infrastructure_config()
    provider_configs = cfg.market_data.providers
    rate_cfg = get_rate_limit_config()

    sources: list[DataSourceStatus] = []
    seen: set[str] = set()

    for pc in provider_configs:
        name = pc.name
        if name in seen:
            continue
        seen.add(name)

        check = _CHECK_FN.get(name)
        available = check() if check else False

        rl = rate_cfg.sources.get(name)
        rate_limit = (
            DataSourceRateLimit(rate_per_sec=rl.rate_per_sec, burst=rl.burst)
            if rl
            else None
        )

        sources.append(
            DataSourceStatus(
                name=name,
                available=available,
                markets=list(pc.markets),
                intraday_markets=list(pc.intraday_markets) if pc.intraday_markets is not None else None,
                daily_markets=list(pc.daily_markets) if pc.daily_markets is not None else None,
                snapshot_markets=list(pc.snapshot_markets) if pc.snapshot_markets is not None else None,
                rate_limit=rate_limit,
            )
        )

    available_count = sum(1 for s in sources if s.available)

    return DataSourcesStatusResponse(
        sources=sources,
        total=len(sources),
        available=available_count,
    )