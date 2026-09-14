/** Same-origin bridge. The private CAD process is never addressed by the browser. */
const upstream = process.env.L_DXF_INTERNAL_URL || 'http://127.0.0.1:3001';
async function forward(request: Request) {
  const path = new URL(request.url).pathname.replace(/^\/api\/cad/, '');
  const allowed =
    (request.method === 'POST' && path === '/generate') ||
    (['GET', 'HEAD'].includes(request.method) &&
      (path === '/healthz' ||
        /^\/files\/cabinet-[a-f0-9]{32}(?:-(?:electrical|external))?\.(dxf|json|zip)$/.test(
          path,
        )));
  if (!allowed)
    return Response.json(
      { error: 'Неизвестный CAD endpoint' },
      { status: 404 },
    );
  try {
    const body = request.method === 'POST' ? await request.text() : undefined;
    if (body && new TextEncoder().encode(body).length > 1_000_000)
      return Response.json(
        { error: 'Слишком большой запрос' },
        { status: 413 },
      );
    const result = await fetch(`${upstream.replace(/\/$/, '')}${path}`, {
      method: request.method,
      body,
      headers: { 'Content-Type': 'application/json' },
      signal: AbortSignal.timeout(100_000),
    });
    const headers = new Headers({
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
    });
    for (const name of [
      'content-type',
      'content-length',
      'content-disposition',
    ]) {
      const value = result.headers.get(name);
      if (value) headers.set(name, value);
    }
    return new Response(request.method === 'HEAD' ? null : result.body, {
      status: result.status,
      headers,
    });
  } catch {
    return Response.json(
      {
        error:
          'DXF-сервис недоступен. Запусти npm run cad в отдельном терминале.',
      },
      { status: 503 },
    );
  }
}
export const GET = forward;
export const HEAD = forward;
export const POST = forward;
