import { describe, it, expect, vi, beforeEach } from 'vitest';

const getAccessToken = vi.fn();

vi.mock('@/lib/supabase', () => ({ supabase: null }));
vi.mock('@/api/client', () => ({
  api: { defaults: { baseURL: '' } },
  getAccessToken: (...args: unknown[]) => getAccessToken(...args),
}));

const { getAuthHeaders } = await import('../transport');

describe('getAuthHeaders without supabase (local / oss)', () => {
  beforeEach(() => {
    getAccessToken.mockReset();
  });

  it('uses the shared token getter', async () => {
    getAccessToken.mockResolvedValue('local-jwt');
    await expect(getAuthHeaders()).resolves.toEqual({
      Authorization: 'Bearer local-jwt',
    });
  });

  it('returns empty headers when no token is wired', async () => {
    getAccessToken.mockResolvedValue(null);
    await expect(getAuthHeaders()).resolves.toEqual({});
  });
});
