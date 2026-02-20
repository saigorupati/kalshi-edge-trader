'use client';

import { useState, useTransition } from 'react';
import { loginAction } from './actions';

export default function LoginPage() {
  const [error, setError]       = useState('');
  const [pending, startTransition] = useTransition();

  function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    setError('');
    startTransition(async () => {
      const result = await loginAction(fd);
      if (result?.error) setError(result.error);
    });
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-bg-primary">
      <div className="card w-full max-w-sm p-8 flex flex-col gap-6">
        <div className="flex flex-col gap-1">
          <h1 className="text-lg font-semibold text-text-primary tracking-tight">
            Kalshi Edge Trader
          </h1>
          <p className="text-xs text-text-muted font-mono">Enter password to continue</p>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <input
            type="password"
            name="password"
            placeholder="Password"
            autoFocus
            autoComplete="current-password"
            required
            className="
              w-full px-3 py-2 rounded-md text-sm font-mono
              bg-bg-secondary border border-bg-border
              text-text-primary placeholder-text-muted
              focus:outline-none focus:border-accent-cyan
              transition-colors
            "
          />

          {error && (
            <p className="text-xs text-accent-red font-mono">{error}</p>
          )}

          <button
            type="submit"
            disabled={pending}
            className="btn w-full justify-center"
          >
            {pending ? 'Checking…' : 'Unlock'}
          </button>
        </form>
      </div>
    </div>
  );
}
