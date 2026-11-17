import { NextRequest, NextResponse } from 'next/server';

/**
 * Same-origin proxy to the Pivot orchestration daemon (`pivot serve`).
 *
 * The browser only ever talks to the Next origin. That keeps tunnel access
 * working (the request is made by the machine running Next, not the phone)
 * and avoids adding a second CORS surface for the loopback port.
 */

const PIVOT_SERVE_URL = (
  process.env.PIVOT_SERVE_URL || 'http://127.0.0.1:8787'
).replace(/\/+$/, '');

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

type RouteContext = { params: Promise<{ path: string[] }> };

async function forward(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  const target = `${PIVOT_SERVE_URL}/v1/${path.join('/')}${request.nextUrl.search}`;
  const body =
    request.method === 'GET' || request.method === 'HEAD'
      ? undefined
      : await request.text();

  try {
    const response = await fetch(target, {
      method: request.method,
      headers: {
        Authorization: request.headers.get('authorization') ?? '',
        'Content-Type': request.headers.get('content-type') ?? 'application/json',
      },
      body,
      cache: 'no-store',
      signal: AbortSignal.timeout(15_000),
    });
    const text = await response.text();
    return new NextResponse(text, {
      status: response.status,
      headers: {
        'Content-Type':
          response.headers.get('content-type') ?? 'application/json',
      },
    });
  } catch (error) {
    console.error('Pivot proxy failed:', error);
    return NextResponse.json(
      { error: 'pivot serve is not running on 127.0.0.1:8787' },
      { status: 502 }
    );
  }
}

export function GET(request: NextRequest, context: RouteContext) {
  return forward(request, context);
}

export function POST(request: NextRequest, context: RouteContext) {
  return forward(request, context);
}

export function DELETE(request: NextRequest, context: RouteContext) {
  return forward(request, context);
}
