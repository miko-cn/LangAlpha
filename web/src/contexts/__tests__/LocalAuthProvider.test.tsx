import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { LocalAuthProvider } from '../LocalAuthProvider';
import { useAuth } from '../AuthContext';

const mockSetTokenGetter = vi.fn();
const mockSetTokenRefresher = vi.fn();

vi.mock('../../api/client', () => ({
  setTokenGetter: (...args: unknown[]) => mockSetTokenGetter(...args),
  setTokenRefresher: (...args: unknown[]) => mockSetTokenRefresher(...args),
}));

vi.mock('@/pages/MarketView/utils/flashWorkspace', () => ({
  clearFlashWorkspaceCache: vi.fn(),
}));
vi.mock('@/pages/ChatAgent/components/navExpansionStore', () => ({
  resetNavPanelExpansion: vi.fn(),
}));
vi.mock('@/pages/ChatAgent/hooks/useNavigationData', () => ({
  resetStableNavOrder: vi.fn(),
}));
vi.mock('@/lib/navThreadsStore', () => ({
  resetSharedWorkspaceThreads: vi.fn(),
}));
vi.mock('../../lib/authResets', () => ({
  runAuthResets: vi.fn(),
}));

function futureJwt(sub = 'local-dev-user'): string {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
  const payload = btoa(
    JSON.stringify({ sub, exp: Math.floor(Date.now() / 1000) + 3600, iss: 'langalpha-local' }),
  )
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
  return `${header}.${payload}.sig`;
}

function TestConsumer() {
  const auth = useAuth();
  return (
    <div>
      <span data-testid="userId">{auth.userId ?? 'none'}</span>
      <span data-testid="isLoggedIn">{String(auth.isLoggedIn)}</span>
      <span data-testid="isInitialized">{String(auth.isInitialized)}</span>
      <button type="button" onClick={() => void auth.loginWithEmail('admin', 'pw')}>
        login
      </button>
      <button type="button" onClick={() => void auth.logout()}>
        logout
      </button>
    </div>
  );
}

function renderProvider() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <LocalAuthProvider>
        <TestConsumer />
      </LocalAuthProvider>
    </QueryClientProvider>,
  );
}

describe('LocalAuthProvider', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  afterEach(() => {
    localStorage.clear();
  });

  it('starts logged out when no token is stored', async () => {
    renderProvider();
    await waitFor(() =>
      expect(screen.getByTestId('isInitialized').textContent).toBe('true'),
    );
    expect(screen.getByTestId('isLoggedIn').textContent).toBe('false');
    expect(screen.getByTestId('userId').textContent).toBe('none');
  });

  it('restores a still-valid stored token', async () => {
    localStorage.setItem('langalpha.local.access_token', futureJwt());
    renderProvider();
    await waitFor(() =>
      expect(screen.getByTestId('isLoggedIn').textContent).toBe('true'),
    );
    expect(screen.getByTestId('userId').textContent).toBe('local-dev-user');
    expect(mockSetTokenGetter).toHaveBeenCalled();
  });

  it('drops an expired stored token', async () => {
    const header = btoa('{"alg":"HS256"}').replace(/=+$/, '');
    const payload = btoa(
      JSON.stringify({ sub: 'local-dev-user', exp: 1 }),
    )
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=+$/, '');
    localStorage.setItem('langalpha.local.access_token', `${header}.${payload}.sig`);
    renderProvider();
    await waitFor(() =>
      expect(screen.getByTestId('isInitialized').textContent).toBe('true'),
    );
    expect(screen.getByTestId('isLoggedIn').textContent).toBe('false');
    expect(localStorage.getItem('langalpha.local.access_token')).toBeNull();
  });

  it('login stores the token and marks the session logged in', async () => {
    const token = futureJwt();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ access_token: token, token_type: 'bearer', expires_in: 3600 }),
    });
    vi.stubGlobal('fetch', fetchMock);

    renderProvider();
    await waitFor(() =>
      expect(screen.getByTestId('isInitialized').textContent).toBe('true'),
    );
    await act(async () => {
      screen.getByText('login').click();
    });
    await waitFor(() =>
      expect(screen.getByTestId('isLoggedIn').textContent).toBe('true'),
    );
    expect(localStorage.getItem('langalpha.local.access_token')).toBe(token);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/auth/local/login',
      expect.objectContaining({ method: 'POST' }),
    );
    vi.unstubAllGlobals();
  });

  it('logout clears the token', async () => {
    localStorage.setItem('langalpha.local.access_token', futureJwt());
    renderProvider();
    await waitFor(() =>
      expect(screen.getByTestId('isLoggedIn').textContent).toBe('true'),
    );
    await act(async () => {
      screen.getByText('logout').click();
    });
    await waitFor(() =>
      expect(screen.getByTestId('isLoggedIn').textContent).toBe('false'),
    );
    expect(localStorage.getItem('langalpha.local.access_token')).toBeNull();
  });
});
