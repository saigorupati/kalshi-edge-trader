'use client';

import { useEffect, useRef, useCallback, useState } from 'react';

export type WsStatus = 'connecting' | 'connected' | 'disconnected' | 'error';

export interface LiveUpdate {
  type: 'snapshot' | 'cycle_update' | 'heartbeat';
  timestamp: string;
  cycle_number?: number;
  opportunities?: unknown[];
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

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const delayRef = useRef(reconnectDelayMs);
  const unmountedRef = useRef(false);

  const getWsUrl = useCallback(() => {
    const wsBase = process.env.NEXT_PUBLIC_WS_URL;
    if (wsBase) return `${wsBase}/ws/live`;
    // In dev use current host but swap protocol
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = process.env.NEXT_PUBLIC_API_URL
      ? new URL(process.env.NEXT_PUBLIC_API_URL).host
      : window.location.host;
    return `${protocol}//${host}/ws/live`;
  }, []);

  const connect = useCallback(() => {
    if (unmountedRef.current) return;

    const url = getWsUrl();
    setStatus('connecting');

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      if (unmountedRef.current) { ws.close(); return; }
      setStatus('connected');
      delayRef.current = reconnectDelayMs; // reset backoff on success
    };

    ws.onmessage = (evt) => {
      if (unmountedRef.current) return;
      try {
        const data: LiveUpdate = JSON.parse(evt.data as string);
        setLastMessage(data);
        if (data.type === 'heartbeat') {
          setLastHeartbeat(new Date(data.timestamp));
        }
        onMessage?.(data);
      } catch {
        // malformed message — ignore
      }
    };

    ws.onerror = () => {
      if (unmountedRef.current) return;
      setStatus('error');
    };

    ws.onclose = () => {
      if (unmountedRef.current) return;
      setStatus('disconnected');
      wsRef.current = null;

      // Exponential backoff reconnect
      const delay = Math.min(delayRef.current, maxReconnectDelay);
      delayRef.current = Math.min(delayRef.current * 1.5, maxReconnectDelay);
      reconnectTimerRef.current = setTimeout(connect, delay);
    };
  }, [getWsUrl, onMessage, reconnectDelayMs, maxReconnectDelay]);

  useEffect(() => {
    unmountedRef.current = false;
    connect();

    return () => {
      unmountedRef.current = true;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const disconnect = useCallback(() => {
    unmountedRef.current = true;
    if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
    wsRef.current?.close();
    setStatus('disconnected');
  }, []);

  return { status, lastMessage, lastHeartbeat, disconnect };
}
