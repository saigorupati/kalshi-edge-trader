'use client';

import { useEffect, useRef, useCallback, useState } from 'react';

export type WsStatus = 'connecting' | 'connected' | 'disconnected' | 'error';

export interface LiveUpdate {
  type: 'snapshot' | 'cycle_update' | 'heartbeat';
  timestamp: string;
  cycle_number?: number;
  opportunities?: unknown[];
  bracket_opportunities?: unknown[];
  city_distributions?: Record<string, unknown>;
  balance?: number;
  open_positions?: number;
  kill_switch_active?: boolean;
}

interface UseWebSocketOptions {
  onMessage?: (data: LiveUpdate) => void;
  reconnectDelayMs?: number;
  maxReconnectDelay?: number;
}

/**
 * Auto-reconnecting WebSocket hook.
 * Connects to /ws/live on the FastAPI backend.
 * Uses exponential backoff capped at maxReconnectDelay.
 *
 * Stability: all mutable state accessed inside the socket callbacks is stored
 * in refs so the `connect` function never needs to be recreated — this prevents
 * the useEffect dependency loop that caused continuous reconnects.
 */
export function useWebSocket(options: UseWebSocketOptions = {}) {
  const {
    onMessage,
    reconnectDelayMs = 1500,
    maxReconnectDelay = 30_000,
  } = options;

  const [status, setStatus] = useState<WsStatus>('connecting');
  const [lastMessage, setLastMessage] = useState<LiveUpdate | null>(null);
  const [lastHeartbeat, setLastHeartbeat] = useState<Date | null>(null);

  // Keep latest onMessage callback in a ref so connect() never needs to change
  const onMessageRef = useRef(onMessage);
  useEffect(() => { onMessageRef.current = onMessage; }, [onMessage]);

  const wsRef             = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const delayRef          = useRef(reconnectDelayMs);
  const unmountedRef      = useRef(false);
  const stopRetryRef      = useRef(false); // set true on auth failure (code 4001)

  const getWsUrl = useCallback(() => {
    const wsBase = process.env.NEXT_PUBLIC_WS_URL;
    const apiKey = process.env.NEXT_PUBLIC_API_SECRET_KEY ?? '';
    const keyParam = apiKey ? `?api_key=${encodeURIComponent(apiKey)}` : '';
    if (wsBase) return `${wsBase}/ws/live${keyParam}`;
    // In dev: derive from current host
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = process.env.NEXT_PUBLIC_API_URL
      ? new URL(process.env.NEXT_PUBLIC_API_URL).host
      : window.location.host;
    return `${protocol}//${host}/ws/live${keyParam}`;
  }, []); // no deps — env vars are static at build time

  // connect is stable — never recreated after mount
  const connect = useCallback(() => {
    if (unmountedRef.current || stopRetryRef.current) return;

    const url = getWsUrl();
    setStatus('connecting');

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      if (unmountedRef.current) { ws.close(); return; }
      setStatus('connected');
      delayRef.current = reconnectDelayMs; // reset backoff
    };

    ws.onmessage = (evt) => {
      if (unmountedRef.current) return;
      try {
        const data: LiveUpdate = JSON.parse(evt.data as string);
        setLastMessage(data);
        if (data.type === 'heartbeat') {
          setLastHeartbeat(new Date(data.timestamp));
        }
        onMessageRef.current?.(data);
      } catch {
        // malformed message — ignore
      }
    };

    ws.onerror = () => {
      if (unmountedRef.current) return;
      setStatus('error');
    };

    ws.onclose = (evt) => {
      if (unmountedRef.current) return;

      // 4001 = invalid API key — stop retrying, no point
      if (evt.code === 4001) {
        stopRetryRef.current = true;
        setStatus('error');
        console.error('WebSocket auth failed (4001) — check API_SECRET_KEY config');
        return;
      }

      setStatus('disconnected');
      wsRef.current = null;

      // Exponential backoff reconnect
      const delay = Math.min(delayRef.current, maxReconnectDelay);
      delayRef.current = Math.min(delayRef.current * 1.5, maxReconnectDelay);
      reconnectTimerRef.current = setTimeout(connect, delay);
    };
  }, [getWsUrl, reconnectDelayMs, maxReconnectDelay]); // onMessage intentionally excluded — use ref

  useEffect(() => {
    unmountedRef.current = false;
    stopRetryRef.current = false;
    connect();

    return () => {
      unmountedRef.current = true;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
    };
  }, []); // empty deps — connect once on mount, cleanup on unmount

  const disconnect = useCallback(() => {
    unmountedRef.current = true;
    if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
    wsRef.current?.close();
    setStatus('disconnected');
  }, []);

  return { status, lastMessage, lastHeartbeat, disconnect };
}
