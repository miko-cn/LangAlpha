import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import LoginPage from '@/pages/Login/LoginPage';

vi.mock('@/pages/Login/MarketScanlines', () => ({ default: () => null }));
vi.mock('@/pages/Login/EdgeGrain', () => ({ default: () => null }));

vi.mock('@/config/hostMode', () => ({
  isLocalMode: true,
  isPlatformMode: false,
  HOST_MODE: 'local',
  APP_ENTRY_PATH: '/',
}));

const auth = {
  loginWithEmail: vi.fn().mockResolvedValue({ error: null }),
  signupWithEmail: vi.fn(),
  loginWithProvider: vi.fn(),
  sendMagicLink: vi.fn(),
  sendPasswordReset: vi.fn(),
  resendConfirmation: vi.fn(),
};

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => auth,
}));

describe('LoginPage in local mode', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('skips the method picker and hides OAuth / signup / magic / reset', async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );

    expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Username')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Continue with Google' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Continue with GitHub' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Email me a sign-in link' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Forgot password?' })).not.toBeInTheDocument();
    expect(screen.queryByText(/Don't have an account/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '← Other sign-in options' })).not.toBeInTheDocument();

    await user.type(screen.getByPlaceholderText('Username'), 'admin');
    await user.type(screen.getByPlaceholderText('Your password'), 'secret');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(auth.loginWithEmail).toHaveBeenCalledWith('admin', 'secret');
  });
});
