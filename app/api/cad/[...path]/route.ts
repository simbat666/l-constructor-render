/** Same-origin bridge. The private CAD process is never addressed by the browser. */
const upstream = process.env.L_DXF_INTERNAL_URL || 'http://127.0.0.1:3001';
async function forward(request: Request) {
  const path = new URL(request.url).pathname.replace(/^\/api\/cad/, '');
  const allowed =
    (request.method === 'POST' && ['/generate', '/calculate'].includes(path)) ||
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
      signal: AbortSignal.timeout(path === '/calculate' ? 15_000 : 100_000),
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
    // Drain calculation JSON before returning it. Streaming an HTTP/1.0
    // upstream into a slower client can trigger Node's Undici paused-parser
    // assertion when the Python connection closes (large cabinets).
    const responseBody = request.method === 'HEAD' ? null
      : path === '/calculate' ? await result.arrayBuffer() : result.body;
    return new Response(responseBody, {
      status: result.status,
      headers,
    });
  } catch {
    return Response.json(
      {
        error:
          'Сервис расчёта недоступен. Попробуй повторить запрос.',
      },
      { status: 503 },
    );
  }
}
export const GET = forward;
export const HEAD = forward;
export const POST = forward;
