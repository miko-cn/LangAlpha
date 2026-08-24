import { describe, expect, it } from 'vitest';

import { isValidUuid, randomUuid } from '../uuid';

describe('isValidUuid', () => {
  it('accepts canonical UUID strings case-insensitively', () => {
    expect(isValidUuid('123e4567-e89b-12d3-a456-426614174000')).toBe(true);
    expect(isValidUuid('123E4567-E89B-12D3-A456-426614174000')).toBe(true);
  });

  it('rejects malformed ids and non-string values', () => {
    expect(isValidUuid('results')).toBe(false);
    expect(isValidUuid('notes.md')).toBe(false);
    expect(isValidUuid('123e4567e89b12d3a456426614174000')).toBe(false);
    expect(isValidUuid('')).toBe(false);
    expect(isValidUuid(undefined)).toBe(false);
  });
});

describe('randomUuid', () => {
  it('returns a canonical UUID', () => {
    expect(isValidUuid(randomUuid())).toBe(true);
  });

  it('falls back when randomUUID is missing (LAN HTTP is not a secure context)', () => {
    const desc = Object.getOwnPropertyDescriptor(globalThis.crypto, 'randomUUID');
    Object.defineProperty(globalThis.crypto, 'randomUUID', {
      value: undefined,
      configurable: true,
    });
    try {
      const id = randomUuid();
      expect(isValidUuid(id)).toBe(true);
      expect(id.charAt(14)).toBe('4');
    } finally {
      if (desc) Object.defineProperty(globalThis.crypto, 'randomUUID', desc);
      else delete (globalThis.crypto as { randomUUID?: unknown }).randomUUID;
    }
  });
});
