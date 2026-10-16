import { clearSessionCookie } from '@/lib/auth';
export async function POST(request: Request) {
  return Response.json({ ok: true }, { headers: { 'Set-Cookie': clearSessionCookie(request), 'Cache-Control': 'no-store' } });
}
