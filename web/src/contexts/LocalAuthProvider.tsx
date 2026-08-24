import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type {
  AuthError,
  AuthOtpResponse,
  AuthResponse,
  OAuthResponse,
  UserResponse,
} from '@supabase/supabase-js';
import { setTokenGetter, setTokenRefresher } from '../api/client';
import { queryKeys } from '../lib/queryKeys';
import { clearFlashWorkspaceCache } from '@/pages/MarketView/utils/flashWorkspace';
import { resetNavPanelExpansion } from '@/pages/ChatAgent/components/navExpansionStore';
import { resetStableNavOrder } from '@/pages/ChatAgent/hooks/useNavigationData';
import { resetSharedWorkspaceThreads } from '@/lib/navThreadsStore';
import { runAuthResets } from '../lib/authResets';
import { AuthContext, type AuthContextValue } from './AuthContext';

const STORAGE_KEY = 'langalpha.local.access_token';
const baseURL = (import.meta.env.VITE_API_BASE_URL as string) ?? '';

function b64urlJson(seg: string): unknown {
  const pad = seg + '='.repeat((4 - (seg.length % 4)) % 4);
  return JSON.parse(atob(pad.replace(/-/g, '+').replace(/_/g, '/')));
}

function readLocalToken(token: string): { sub: string; exp: number } | null {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return null;
    const payload = b64urlJson(parts[1]) as { sub?: unknown; exp?: unknown };
    if (typeof payload.sub !== 'string' || typeof payload.exp !== 'number') return null;
    if (payload.exp * 1000 <= Date.now()) return null;
    return { sub: payload.sub, exp: payload.exp };
  } catch {
    return null;
  }
}

function localAuthError(message: string, code: string, status = 401): AuthError {
  return { name: 'AuthApiError', message, status, code } as AuthError;
}

function unsupported<T>(): Promise<T> {
  return Promise.resolve({
    data: { user: null, session: null },
    error: localAuthError('Not available in local mode', 'not_supported', 400),
  } as T);
}

export function LocalAuthProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const tokenRef = useRef<string | null>(null);
  const [userId, setUserId] = useState<string | null>(null);
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [isInitialized, setIsInitialized] = useState(false);

  const wireToken = useCallback((token: string | null) => {
    tokenRef.current = token;
    setTokenGetter(() => Promise.resolve(tokenRef.current));
    setTokenRefresher(() => Promise.resolve(null));
  }, []);

  const clearSessionCaches = useCallback(() => {
    queryClient.clear();
    clearFlashWorkspaceCache();
    resetNavPanelExpansion();
    resetStableNavOrder();
    resetSharedWorkspaceThreads();
    runAuthResets();
  }, [queryClient]);

  useEffect(() => {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? readLocalToken(raw) : null;
    if (raw && parsed) {
      wireToken(raw);
      setUserId(parsed.sub);
      setIsLoggedIn(true);
    } else if (raw) {
      localStorage.removeItem(STORAGE_KEY);
    }
    setIsInitialized(true);
  }, [wireToken]);

  const loginWithEmail = useCallback(
    async (username: string, password: string) => {
      try {
        const res = await fetch(`${baseURL}/api/v1/auth/local/login`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username, password }),
        });
        if (!res.ok) {
          return {
            data: { user: null, session: null },
            error: localAuthError(
              'Incorrect username or password.',
              'invalid_local_credentials',
            ),
          } as AuthResponse;
        }
        const body = (await res.json()) as { access_token?: string };
        const token = body.access_token;
        const parsed = token ? readLocalToken(token) : null;
        if (!token || !parsed) {
          return {
            data: { user: null, session: null },
            error: localAuthError('Login failed', 'invalid_local_credentials'),
          } as AuthResponse;
        }
        localStorage.setItem(STORAGE_KEY, token);
        wireToken(token);
        setUserId(parsed.sub);
        setIsLoggedIn(true);
        queryClient.invalidateQueries({ queryKey: queryKeys.user.all });
        return { data: { user: { id: parsed.sub }, session: { access_token: token } }, error: null } as AuthResponse;
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Login failed';
        return {
          data: { user: null, session: null },
          error: localAuthError(message, 'generic', 0),
        } as AuthResponse;
      }
    },
    [queryClient, wireToken],
  );

  const logout = useCallback(async () => {
    localStorage.removeItem(STORAGE_KEY);
    wireToken(null);
    setUserId(null);
    setIsLoggedIn(false);
    clearSessionCaches();
  }, [clearSessionCaches, wireToken]);

  const value: AuthContextValue = {
    userId,
    isInitialized,
    isLoggedIn,
    loginWithEmail,
    signupWithEmail: () => unsupported<AuthResponse>(),
    loginWithProvider: () => unsupported<OAuthResponse>(),
    logout,
    sendMagicLink: () => unsupported<AuthOtpResponse>(),
    sendPasswordReset: () => unsupported<{ data: object | null; error: AuthError | null }>(),
    resendConfirmation: () => unsupported<AuthOtpResponse>(),
    verifyEmailOtp: () => unsupported<AuthResponse>(),
    updatePassword: () => unsupported<UserResponse>(),
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
