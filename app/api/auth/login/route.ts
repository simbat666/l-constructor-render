import { credentialsUser, sessionCookie } from '@/lib/auth';

const attempts = new Map<string, { count: number; resetAt: number }>();
const WINDOW_MS = 15 * 60 * 1000;
const MAX_ATTEMPTS = 8;
function remoteAddress(request: Request) { return request.headers.get('x-forwarded-for')?.split(',')[0]?.trim() || 'direct'; }

export async function POST(request: Request) {
  const address = remoteAddress(request); const now = Date.now(); const previous = attempts.get(address);
  if (previous && previous.resetAt > now && previous.count >= MAX_ATTEMPTS) return Response.json({ error: 'Слишком много попыток. Повтори через 15 минут.' }, { status: 429 });
  let body: { username?: unknown; password?: unknown };
  try { body = await request.json(); } catch { return Response.json({ error: 'Нужны логин и пароль.' }, { status: 400 }); }
  const user = credentialsUser(body.username, body.password);
  if (!user) {
    attempts.set(address, { count: previous && previous.resetAt > now ? previous.count + 1 : 1, resetAt: now + WINDOW_MS });
    return Response.json({ error: 'Неверный логин или пароль.' }, { status: 401 });
  }
  attempts.delete(address);
  return Response.json({ user }, { headers: { 'Set-Cookie': sessionCookie(user, request), 'Cache-Control': 'no-store' } });
}
