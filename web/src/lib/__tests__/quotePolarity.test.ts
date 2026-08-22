import { afterEach, describe, expect, it } from 'vitest';
import { getQuotePolarity, inferQuotePolarity } from '../quotePolarity';

describe('inferQuotePolarity', () => {
  afterEach(() => {
    document.cookie = 'locale=; path=/; max-age=0';
  });

  it('returns cn when the locale cookie is zh-CN', () => {
    document.cookie = 'locale=zh-CN; path=/';
    expect(inferQuotePolarity()).toBe('cn');
  });

  it('returns western for en-US', () => {
    document.cookie = 'locale=en-US; path=/';
    expect(inferQuotePolarity()).toBe('western');
  });
});

describe('getQuotePolarity', () => {
  afterEach(() => {
    localStorage.removeItem('quote-polarity');
    document.cookie = 'locale=; path=/; max-age=0';
  });

  it('prefers a stored override over the locale cookie', () => {
    document.cookie = 'locale=zh-CN; path=/';
    localStorage.setItem('quote-polarity', 'western');
    expect(getQuotePolarity()).toBe('western');
  });

  it('infers from locale when nothing is stored', () => {
    document.cookie = 'locale=zh-CN; path=/';
    expect(getQuotePolarity()).toBe('cn');
  });
});
