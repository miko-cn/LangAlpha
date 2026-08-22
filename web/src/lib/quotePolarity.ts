/**
 * Quote up/down color convention. Western = green up; CN = 红涨绿跌.
 * Stamped on <html data-quote-polarity> so CSS tokens (`--color-quote-up`)
 * and canvas readers pick it up. localStorage, same as theme / fontScale.
 */

export const QUOTE_POLARITIES = ['western', 'cn'] as const;
export type QuotePolarity = (typeof QUOTE_POLARITIES)[number];

const STORAGE_KEY = 'quote-polarity';

export function inferQuotePolarity(): QuotePolarity {
  if (typeof document !== 'undefined') {
    const m = document.cookie.match(/(?:^|;\s*)locale=([^;]+)/);
    if (m) {
      try {
        if (decodeURIComponent(m[1]).toLowerCase().startsWith('zh')) return 'cn';
      } catch {
        /* malformed cookie — fall through */
      }
    }
  }
  if (typeof navigator !== 'undefined' && navigator.language?.toLowerCase().startsWith('zh')) {
    return 'cn';
  }
  return 'western';
}

export function getQuotePolarity(): QuotePolarity {
  if (typeof localStorage === 'undefined') return 'western';
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === 'western' || stored === 'cn') return stored;
  return inferQuotePolarity();
}

export function stampQuotePolarity(value: QuotePolarity): void {
  document.documentElement.setAttribute('data-quote-polarity', value);
  localStorage.setItem(STORAGE_KEY, value);
}
