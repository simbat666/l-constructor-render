import { authEnabled, sessionUser } from '@/lib/auth';
export async function GET(request: Request) {
  return Response.json({ enabled: authEnabled(), user: sessionUser(request) }, { headers: { 'Cache-Control': 'no-store' } });
}
