"""Shared helpers for China/HK (A-share + HK) market data sources.

Symbol conversion and OHLCV normalization that the Tencent / Sina / Eastmoney
free sources and the Futu / Tushare official sources share. The app's internal
symbols are Yahoo-style (``600519.SS``, ``000001.SZ``, ``0700.HK``, bare US);
each vendor keys symbols, timestamps and volume units differently — this module
is the single place those differences are reconciled.
"""
