/** Server-only credentials and signed browser sessions for the private stand. */
import { createHmac, randomBytes, scryptSync, timingSafeEqual } from 'node:crypto';

export const AUTH_COOKIE = 'l_constructor_session';
const SESSION_TTL_SECONDS = 12 * 60 * 60;
type AuthConfig = { secret: string; users: Array<{ username: string; passwordHash: string }> };

function config(): AuthConfig | null {
  const secret = process.env.L_AUTH_SESSION_SECRET;
  const users = [1, 2].map((index) => ({
    username: (process.env[`L_AUTH_USER_${index}`] || '').trim().toLowerCase(),
    passwordHash: process.env[`L_AUTH_PASSWORD_HASH_${index}`] || '',
  })).filter((user) => user.username && user.passwordHash);
  if (!secret || users.length !== 2 || new Set(users.map((user) => user.username)).size !== 2) return null;
  return { secret, users };
}

export function authEnabled() { return Boolean(config()); }
export function passwordHash(password: string, salt = randomBytes(16).toString('base64url')) {
  return `scrypt:${salt}:${scryptSync(password, salt, 32).toString('base64url')}`;
}
function verifyPassword(password: string, stored: string) {
  const [kind, salt, encoded, extra] = stored.split(':');
  if (kind !== 'scrypt' || !salt || !encoded || extra) return false;
  const actual = Buffer.from(scryptSync(password, salt, 32).toString('base64url'));
  const expected = Buffer.from(encoded);
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}
function cookie(request: Request, name: string) {
  return request.headers.get('cookie')?.split(';').map((part) => part.trim()).find((part) => part.startsWith(`${name}=`))?.slice(name.length + 1) || null;
}
function sign(value: string, secret: string) { return createHmac('sha256', secret).update(value).digest('base64url'); }
function safeEqual(left: string, right: string) {
  const first = Buffer.from(left); const second = Buffer.from(right);
  return first.length === second.length && timingSafeEqual(first, second);
}
function secureCookie(request: Request) { return new URL(request.url).protocol === 'https:' || request.headers.get('x-forwarded-proto') === 'https'; }

export function sessionUser(request: Request) {
  const settings = config(); const token = cookie(request, AUTH_COOKIE);
  if (!settings || !token) return null;
  const [payload, signature, extra] = token.split('.');
  if (!payload || !signature || extra || !safeEqual(sign(payload, settings.secret), signature)) return null;
  try {
    const data = JSON.parse(Buffer.from(payload, 'base64url').toString('utf8')) as { user?: string; exp?: number };
    if (typeof data.user !== 'string' || typeof data.exp !== 'number' || !Number.isFinite(data.exp) || data.exp <= Date.now()) return null;
    return settings.users.some((entry) => entry.username === data.user) ? data.user : null;
  } catch { return null; }
}
export function credentialsUser(username: unknown, password: unknown) {
  const settings = config();
  if (!settings || typeof username !== 'string' || typeof password !== 'string') return null;
  const normalized = username.trim().toLowerCase();
  if (!/^[a-z0-9][a-z0-9._-]{2,31}$/.test(normalized) || password.length < 12 || password.length > 256) return null;
  const user = settings.users.find((entry) => entry.username === normalized);
  return user && verifyPassword(password, user.passwordHash) ? user.username : null;
}
export function sessionCookie(user: string, request: Request) {
  const settings = config(); if (!settings) throw new Error('Авторизация не настроена');
  const payload = Buffer.from(JSON.stringify({ user, exp: Date.now() + SESSION_TTL_SECONDS * 1000, nonce: randomBytes(12).toString('base64url') })).toString('base64url');
  return `${AUTH_COOKIE}=${payload}.${sign(payload, settings.secret)}; Path=/; Max-Age=${SESSION_TTL_SECONDS}; HttpOnly; SameSite=Lax${secureCookie(request) ? '; Secure' : ''}`;
}
export function clearSessionCookie(request: Request) { return `${AUTH_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax${secureCookie(request) ? '; Secure' : ''}`; }
export function requireSession(request: Request) {
  if (!authEnabled()) return process.env.NODE_ENV === 'production' ? Response.json({ error: 'Авторизация сервера не настроена.' }, { status: 503 }) : null;
  return sessionUser(request) ? null : Response.json({ error: 'Требуется вход.' }, { status: 401 });
}
