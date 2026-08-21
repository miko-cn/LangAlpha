#!/usr/bin/env python3
"""端到端验证多级数据源链路。

运行方式：
  uv run python scripts/verify_data_sources.py

对每个测试用例依次尝试链中所有数据源，报告哪个源实际提供了数据。
"""

import asyncio
import logging
import sys
from datetime import datetime, timedelta

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s | %(name)s | %(message)s",
)
# 只关注数据源链的日志
logging.getLogger("src.data_client").setLevel(logging.INFO)


async def main():
    # ---------- 初始化 ----------
    from src.data_client._ratelimit import init_ratelimit
    from src.config.settings import get_rate_limit_config

    rate_cfg = get_rate_limit_config()
    config_dict = {
        name: {"rate_per_sec": s.rate_per_sec, "burst": s.burst}
        for name, s in rate_cfg.sources.items()
    }
    init_ratelimit(config=config_dict or None)
    print("✓ RateLimiter 初始化完成")
    print(f"  配置源: {list(rate_cfg.sources.keys())}")
    print()

    # ---------- 构建 Provider 链 ----------
    from src.data_client.registry import get_market_data_provider

    provider = await get_market_data_provider()

    # 打印链中每个 source 的 market 覆盖
    print("数据源链（按优先级）:")
    chain_info = []
    async with asyncio.Lock():  # 确保打印不乱
        from src.data_client.market_data_provider import MarketDataProvider

        mdp = provider  # type: MarketDataProvider
        for entry in mdp.entries:
            chain_info.append(
                f"  {entry.name:<12} markets={entry.markets}"
                f"  intraday={entry.intraday_markets}"
                f"  daily={entry.daily_markets}"
                f"  snapshot={entry.snapshot_markets}"
            )
    for line in chain_info:
        print(line)
    print()

    # ---------- 测试用例 ----------
    tests = [
        # (label, method, symbol, kwargs)
        ("A 股 日线", "get_daily_with_source", "600519.SS",
         {"from_date": "2025-08-14", "to_date": "2025-08-20"}),
        ("港股 日线", "get_daily_with_source", "0700.HK",
         {"from_date": "2025-08-14", "to_date": "2025-08-20"}),
        ("A 股 分钟线", "get_intraday_with_source", "600519.SS",
         {"interval": "5min", "from_date": "2025-08-20", "to_date": "2025-08-20"}),
        ("港股 分钟线", "get_intraday_with_source", "0700.HK",
         {"interval": "5min", "from_date": "2025-08-20", "to_date": "2025-08-20"}),
        ("A 股 实时快照", "get_snapshots", ["600519.SS"], {}),
        ("港股 实时快照", "get_snapshots", ["0700.HK"], {}),
    ]

    passed = 0
    failed = 0
    for label, method, symbol, kwargs in tests:
        print(f"── {label} ──")
        try:
            if method == "get_snapshots":
                result = await getattr(provider, method)(symbol, **kwargs)
                if result:
                    src = result[0].get("source", "unknown")
                    print(f"  ✓ 成功 | 来源={src} | 字段={list(result[0].keys())[:8]}")
                    print(f"    最新价={result[0].get('price', 'N/A')}  "
                          f"涨跌幅={result[0].get('change_pct', 'N/A')}%")
                    passed += 1
                else:
                    print(f"  ✗ 返回空列表（所有源均无数据）")
                    failed += 1
            else:
                bars, source, truncated = await getattr(provider, method)(symbol, **kwargs)
                if bars:
                    print(f"  ✓ 成功 | 来源={source} | 条数={len(bars)} "
                          f"{'[截断]' if truncated else ''}")
                    print(f"    首条: {bars[0]}")
                    print(f"    末条: {bars[-1]}")
                    passed += 1
                else:
                    print(f"  ✗ 返回空（所有源均无数据）")
                    failed += 1
        except Exception as e:
            print(f"  ✗ 异常: {e}")
            failed += 1
        print()

    # ---------- 总结 ----------
    total = passed + failed
    print(f"{'='*40}")
    print(f"结果: {passed}/{total} 通过, {failed}/{total} 失败")
    if failed:
        print("⚠️  有失败项，请检查日志")
    else:
        print("✓ 全部通过")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))