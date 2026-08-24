import { z } from 'zod';

// Workspace/thread ids arrive from untrusted boundaries — URL params,
// location.state, sessionStorage — so validate with safeParse (never throws)
// per the boundary-validation convention.
const uuidSchema = z.string().uuid();

export function isValidUuid(value: unknown): value is string {
  return uuidSchema.safeParse(value).success;
}

/**
 * UUID v4. `crypto.randomUUID` is secure-context only (https / localhost),
 * so LAN `http://192.168.x.x` throws. `getRandomValues` still works there.
 */
export function randomUuid(): string {
  const c = globalThis.crypto;
  if (typeof c?.randomUUID === 'function') {
    return c.randomUUID();
  }
  const bytes = new Uint8Array(16);
  if (typeof c?.getRandomValues === 'function') {
    c.getRandomValues(bytes);
  } else {
    for (let i = 0; i < 16; i++) bytes[i] = (Math.random() * 256) | 0;
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
