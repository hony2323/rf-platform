import { useCallback, useEffect, useRef, useState } from "react";
import { viewerWsUrl } from "../api/viewer";
import type {
  ViewerInboundMessage,
  ViewerSpectrumFrameMessage,
  ViewerStreamConfigMessage,
} from "../types/viewer";

export type ViewerConnectionState =
  | "idle"
  | "connecting"
  | "subscribed"
  | "offline"
  | "error";

export interface ViewerStreamResult {
  connectionState: ViewerConnectionState;
  config: ViewerStreamConfigMessage | null;
  lastError: string | null;
  onFrame: (cb: (frame: ViewerSpectrumFrameMessage) => void) => () => void;
}

const BACKOFF_MIN_MS = 1000;
const BACKOFF_MAX_MS = 30000;

export function useViewerStream(agentId: string): ViewerStreamResult {
  const [connectionState, setConnectionState] =
    useState<ViewerConnectionState>("idle");
  const [config, setConfig] = useState<ViewerStreamConfigMessage | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);

  const frameCallbacks = useRef<Set<(frame: ViewerSpectrumFrameMessage) => void>>(
    new Set(),
  );
  const wsRef = useRef<WebSocket | null>(null);
  const configRef = useRef<ViewerStreamConfigMessage | null>(null);
  const retryCountRef = useRef(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // True after subscribe_ack — enables reconnect on close. False on permanent errors.
  const retryEnabledRef = useRef(false);
  // True if the server sent an error message — onclose skips the generic "no ack" message.
  const gotServerErrorRef = useRef(false);

  const connect = useCallback(() => {
    if (wsRef.current) return;

    const ws = new WebSocket(viewerWsUrl());
    wsRef.current = ws;
    retryEnabledRef.current = false;
    gotServerErrorRef.current = false;
    setConnectionState("connecting");

    ws.onopen = () => {
      if (wsRef.current !== ws) return;
      ws.send(JSON.stringify({ msg_type: "subscribe", agent_id: agentId }));
    };

    ws.onmessage = (event: MessageEvent<string>) => {
      if (wsRef.current !== ws) return;
      let msg: ViewerInboundMessage;
      try {
        msg = JSON.parse(event.data) as ViewerInboundMessage;
      } catch {
        return;
      }

      if (msg.msg_type === "subscribe_ack") {
        retryEnabledRef.current = true;
        retryCountRef.current = 0;
        setConnectionState("subscribed");
      } else if (msg.msg_type === "stream_config") {
        configRef.current = msg;
        setConfig(msg);
      } else if (msg.msg_type === "spectrum_frame") {
        frameCallbacks.current.forEach((cb) => cb(msg));
      } else if (msg.msg_type === "error") {
        gotServerErrorRef.current = true;
        setLastError(msg.message);
        const isPermanent =
          msg.code === "FORBIDDEN" || msg.code === "INVALID_FRAME";
        if (isPermanent) retryEnabledRef.current = false;
        setConnectionState(msg.code === "AGENT_OFFLINE" ? "offline" : "error");
        ws.close();
      }
    };

    ws.onclose = () => {
      if (wsRef.current !== ws) return;
      wsRef.current = null;
      if (!retryEnabledRef.current) {
        // Never subscribed or hit permanent error — don't reconnect.
        if (!gotServerErrorRef.current) {
          setConnectionState("error");
          setLastError("Connection closed before subscribe was acknowledged");
        }
        return;
      }
      // Was subscribed — schedule reconnect with exponential backoff.
      const delay = Math.min(
        BACKOFF_MIN_MS * 2 ** retryCountRef.current,
        BACKOFF_MAX_MS,
      );
      retryCountRef.current += 1;
      retryTimerRef.current = setTimeout(() => {
        retryTimerRef.current = null;
        connect();
      }, delay);
    };

    ws.onerror = () => {
      if (wsRef.current !== ws) return;
      // onclose fires after onerror — all state transitions happen there.
    };
  }, [agentId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    connect();
    return () => {
      if (retryTimerRef.current !== null) {
        clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
        wsRef.current = null;
      }
      retryEnabledRef.current = false;
      gotServerErrorRef.current = false;
      retryCountRef.current = 0;
      configRef.current = null;
      setConnectionState("idle");
      setConfig(null);
      setLastError(null);
    };
  }, [connect]);

  const onFrame = useCallback(
    (cb: (frame: ViewerSpectrumFrameMessage) => void) => {
      frameCallbacks.current.add(cb);
      return () => {
        frameCallbacks.current.delete(cb);
      };
    },
    [],
  );

  return { connectionState, config, lastError, onFrame };
}
