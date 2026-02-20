'use server';

import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';

const SESSION_COOKIE  = 'ket_session';
const SESSION_VALUE   = 'authenticated';
// 7-day session; adjust as needed
const MAX_AGE_SECONDS = 60 * 60 * 24 * 7;

export async function loginAction(
  formData: FormData,
): Promise<{ error: string } | void> {
  const password         = formData.get('password') as string;
  const correctPassword  = process.env.DASHBOARD_PASSWORD;

  if (!correctPassword) {
    // Env var not set — deny access so the dashboard isn't accidentally public
    return { error: 'Server misconfiguration: DASHBOARD_PASSWORD is not set.' };
  }

  if (!password || password !== correctPassword) {
    return { error: 'Incorrect password.' };
  }

  // Set a simple session cookie (httpOnly, secure in prod)
  const cookieStore = await cookies();
  cookieStore.set(SESSION_COOKIE, SESSION_VALUE, {
    httpOnly: true,
    secure:   process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    path:     '/',
    maxAge:   MAX_AGE_SECONDS,
  });

  redirect('/dashboard');
}
