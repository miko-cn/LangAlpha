import { describe, expect, it } from 'vitest';
import { INDEX_SYMBOLS } from '../../../utils/api';
import {
  OVERVIEW_BASKET_SYMBOLS,
  overviewTitleKey,
  resolveOverviewSymbols,
} from '../marketsOverviewBaskets';

describe('resolveOverviewSymbols', () => {
  it('defaults empty config to the US five', () => {
    expect(resolveOverviewSymbols({})).toEqual([...INDEX_SYMBOLS]);
    expect(resolveOverviewSymbols()).toEqual([...INDEX_SYMBOLS]);
  });

  it('resolves named baskets', () => {
    expect(resolveOverviewSymbols({ basket: 'cn' })).toEqual([...OVERVIEW_BASKET_SYMBOLS.cn]);
    expect(resolveOverviewSymbols({ basket: 'hk' })).toEqual([...OVERVIEW_BASKET_SYMBOLS.hk]);
    expect(resolveOverviewSymbols({ basket: 'us' })).toEqual([...INDEX_SYMBOLS]);
  });

  it('lets a custom indices list win over basket', () => {
    expect(resolveOverviewSymbols({ basket: 'cn', indices: ['^HSI', '000300.ss'] })).toEqual([
      'HSI',
      '000300.SS',
    ]);
  });

  it('ignores a blank custom list and falls back to the basket', () => {
    expect(resolveOverviewSymbols({ basket: 'hk', indices: [] })).toEqual([
      ...OVERVIEW_BASKET_SYMBOLS.hk,
    ]);
  });
});

describe('overviewTitleKey', () => {
  it('keeps the original title for US / missing basket', () => {
    expect(overviewTitleKey({})).toBe('dashboard.widgets.marketsOverview.title');
    expect(overviewTitleKey({ basket: 'us' })).toBe('dashboard.widgets.marketsOverview.title');
  });

  it('suffixes CN / HK instance titles', () => {
    expect(overviewTitleKey({ basket: 'cn' })).toBe('dashboard.widgets.marketsOverview.title_cn');
    expect(overviewTitleKey({ basket: 'hk' })).toBe('dashboard.widgets.marketsOverview.title_hk');
  });
});
