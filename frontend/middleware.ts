import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

const SESSION_COOKIE = 'ket_session';
const SESSION_VALUE  = 'authenticated';

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;

  // Always allow the login page and its POST action through
  if (pathname === '/login') return NextResponse.next();

  // Allow Next.js internals and static assets
  if (
    pathname.startsWith('/_next') ||
    pathname.startsWith('/favicon')
  ) {
    return NextResponse.next();
  }

  const session = req.cookies.get(SESSION_COOKIE)?.value;
  if (session === SESSION_VALUE) return NextResponse.next();

  // Not authenticated — redirect to /login
  const loginUrl = req.nextUrl.clone();
  loginUrl.pathname = '/login';
  return NextResponse.redirect(loginUrl);
}

export const config = {
  // Run on every route except API routes (backend proxied via rewrites)
  matcher: ['/((?!api/).*)'],
};
