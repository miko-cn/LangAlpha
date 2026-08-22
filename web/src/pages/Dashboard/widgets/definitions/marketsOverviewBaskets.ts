import { INDEX_SYMBOLS, normalizeIndexSymbol } from '../../utils/api';
import { MARKETS_OVERVIEW_BASKETS } from '../framework/configSchemas';

export type OverviewBasket = (typeof MARKETS_OVERVIEW_BASKETS)[number];

export const OVERVIEW_BASKET_SYMBOLS: Record<OverviewBasket, readonly string[]> = {
  us: INDEX_SYMBOLS,
  cn: ['000001.SS', '399001.SZ', '399006.SZ', '000300.SS', '000016.SS'],
  // HSTECH is not a yfinance bare family (dropped on the US index chain).
  // HSTECH.HK resolves via Tencent snapshot + yfinance intraday.
  hk: ['HSI', 'HSCE', 'HSTECH.HK'],
};

export type MarketsOverviewConfig = {
  basket?: OverviewBasket;
  indices?: string[];
};

/** Custom `indices` wins; otherwise the named basket; empty config → US five. */
export function resolveOverviewSymbols(config: MarketsOverviewConfig = {}): string[] {
  const custom = (config.indices ?? [])
    .map((s) => String(s).trim())
    .filter(Boolean)
    .map(normalizeIndexSymbol);
  if (custom.length) return [...new Set(custom)];
  const basket: OverviewBasket =
    config.basket && config.basket in OVERVIEW_BASKET_SYMBOLS ? config.basket : 'us';
  return [...OVERVIEW_BASKET_SYMBOLS[basket]];
}

export function overviewTitleKey(config: MarketsOverviewConfig = {}): string {
  if (config.basket === 'cn') return 'dashboard.widgets.marketsOverview.title_cn';
  if (config.basket === 'hk') return 'dashboard.widgets.marketsOverview.title_hk';
  return 'dashboard.widgets.marketsOverview.title';
}
