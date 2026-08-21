"""App-symbol (Yahoo-style) ↔ vendor symbol conversion for CN/HK markets.

App symbols: ``600519.SS`` / ``000001.SZ`` / ``0700.HK`` / ``AAPL`` (bare US).
Vendor conventions differ sharply:

- Futu:      ``SH.600519``, ``SZ.000001``, ``HK.00700`` (HK zero-padded to 5)
- Tushare:   ``600519.SH``, ``000001.SZ``, ``00700.HK`` (HK zero-padded to 5)
- Tencent:   ``sh600519``, ``sz000001``, ``hk00700`` (market prefix + code)
- Sina:      ``sh600519``, ``sz000001``, ``hk00700`` (same as Tencent)

The app never uses ``.SH`` (Shanghai is ``.SS``, matching Yahoo), so ``.SH``
symbols are treated as unsupported rather than silently remapped.
"""

from __future__ import annotations

_APP_MARKETS = {"SS": "sh", "SZ": "sz", "BJ": "bj", "HK": "hk", "US": "us"}
_MARKET_PREFIX = {"sh": "SH", "sz": "SZ", "bj": "BJ", "hk": "HK", "us": "US"}
_APP_SUFFIX = {"sh": "SS", "sz": "SZ", "bj": "BJ", "hk": "HK", "us": "US"}


class UnsupportedSymbolError(ValueError):
    """Raised when a symbol cannot be served by a CN/HK vendor."""


def split_app_symbol(symbol: str) -> tuple[str, str]:
    """Return ``(market, code)`` for an app symbol; market ∈ {sh, sz, bj, hk, us}.

    Raises :class:`UnsupportedSymbolError` for suffixes these vendors don't
    serve (e.g. ``.SH`` — the app spells Shanghai ``.SS``).
    """
    s = symbol.strip().upper()
    if "." in s:
        code, suffix = s.rsplit(".", 1)
        market = _APP_MARKETS.get(suffix)
        if market is None:
            raise UnsupportedSymbolError(f"unsupported market suffix .{suffix}")
        return market, code
    return "us", s


def pad_hk_code(code: str) -> str:
    """Zero-pad a HK code to 5 digits (Futu/Tushare): ``0700`` → ``00700``."""
    return code.zfill(5)


def app_hk_code(code: str) -> str:
    """App/Yahoo-style HK code (4 digits): ``00700`` → ``0700``, ``09988`` → ``9988``."""
    return str(int(code)).zfill(4)


def futu_symbol(symbol: str) -> str:
    """App symbol → Futu ``MARKET.CODE`` (``600519.SS`` → ``SH.600519``)."""
    market, code = split_app_symbol(symbol)
    code = pad_hk_code(code) if market == "hk" else code
    return f"{_MARKET_PREFIX[market]}.{code}"


def from_futu_symbol(futu_code: str) -> str:
    """Futu ``MARKET.CODE`` → app symbol (``HK.00700`` → ``0700.HK``)."""
    prefix, code = futu_code.split(".", 1)
    market = {"SH": "sh", "SZ": "sz", "BJ": "bj", "HK": "hk", "US": "us"}.get(prefix.upper())
    if market is None:
        raise UnsupportedSymbolError(f"unknown Futu market prefix {prefix!r}")
    if market == "hk":
        return f"{app_hk_code(code)}.HK"
    if market == "us":
        return code
    return f"{code}.{_APP_SUFFIX[market]}"


def tushare_code(symbol: str) -> str:
    """App symbol → Tushare ``ts_code`` (``600519.SS`` → ``600519.SH``).

    Tushare uses ``.SH`` for Shanghai — the one vendor where Shanghai is not
    ``.SS``. US symbols raise: Tushare's basic tier has no US daily.
    """
    market, code = split_app_symbol(symbol)
    if market == "us":
        raise UnsupportedSymbolError("Tushare basic tier has no US daily data")
    code = pad_hk_code(code) if market == "hk" else code
    suffix = {"sh": "SH", "sz": "SZ", "bj": "BJ", "hk": "HK"}[market]
    return f"{code}.{suffix}"


def tencent_symbol(symbol: str) -> str:
    """App symbol → Tencent key (``600519.SS`` → ``sh600519``, ``0700.HK`` → ``hk00700``)."""
    market, code = split_app_symbol(symbol)
    code = pad_hk_code(code) if market == "hk" else code
    return f"{market}{code}"


def sina_symbol(symbol: str) -> str:
    """App symbol → Sina key; identical layout to Tencent."""
    return tencent_symbol(symbol)
