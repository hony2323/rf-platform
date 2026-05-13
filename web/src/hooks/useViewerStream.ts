import { useCallback, useEffect, useRef, useState } from "react";
import { viewerWsUrl } from "../api/viewer";
import type {
  RfConfigRequest,
  TunerConfigRequest,
  ViewerInboundMessage,
  ViewerSpectrumFrameMessage,
  ViewerStreamConfigMessage,
} from "../types/viewer";

type ViewerControlMessage = Exclude<ViewerInboundMessage, ViewerSpectrumFrameMessage>;

export class RequestConfigError extends Error {
  constructor(public readonly code: string, message: string) {
    super(message);
    this.name = "RequestConfigError";
  }
}

interface PendingRequest {
  resolve: () => void;
  reject: (err: Error) => void;
}

function newRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `req_${crypto.randomUUID()}`;
  }
  return `req_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}

function parseBinarySpectrumFrame(buffer: ArrayBuffer): ViewerSpectrumFrameMessage | null {
  if (buffer.byteLength < 2) return null;
  const view = new DataView(buffer);
  const headerLen = view.getUint16(0, false);
  const headerEnd = 2 + headerLen;
  if (headerEnd > buffer.byteLength) return null;
  let header: Record<string, unknown>;
  try {
    header = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 2, headerLen)));
  } catch {
    return null;
  }
  if (header.msg_type !== "spectrum_frame") return null;
  const payloadOffset = headerEnd;
  const payloadBytes = buffer.byteLength - payloadOffset;
  if (payloadBytes % 4 !== 0) return null;
  const payload = new Float32Array(buffer, payloadOffset, payloadBytes / 4);
  return {
    msg_type: "spectrum_frame",
    agent_id: String(header.agent_id ?? ""),
    session_id: String(header.session_id ?? ""),
    stream_id: String(header.stream_id ?? ""),
    config_version: Number(header.config_version ?? 0),
    frame_index: Number(header.frame_index ?? 0),
    timestamp_utc: String(header.timestamp_utc ?? ""),
    data: { payload },
  };
}

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
  sendRequestConfig: (
    rf: RfConfigRequest,
    tuner: TunerConfigRequest | null,
  ) => Promise<void>;
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
  // In-flight request_config promises keyed by viewer-side request_id.
  const pendingRequestsRef = useRef<Map<string, PendingRequest>>(new Map());

  const rejectAllPending = useCallback((err: Error) => {
    const pending = pendingRequestsRef.current;
    pending.forEach((entry) => entry.reject(err));
    pending.clear();
  }, []);

  const connect = useCallback(() => {
    if (wsRef.current) return;

    const ws = new WebSocket(viewerWsUrl());
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;
    retryEnabledRef.current = false;
    gotServerErrorRef.current = false;
    setConnectionState("connecting");

    ws.onopen = () => {
      if (wsRef.current !== ws) return;
      ws.send(JSON.stringify({ msg_type: "subscribe", agent_id: agentId }));
    };

    ws.onmessage = (event: MessageEvent<string | ArrayBuffer>) => {
      if (wsRef.current !== ws) return;

      if (event.data instanceof ArrayBuffer) {
        const frame = parseBinarySpectrumFrame(event.data);
        if (frame) frameCallbacks.current.forEach((cb) => cb(frame));
        return;
      }

      let msg: ViewerControlMessage;
      try {
        msg = JSON.parse(event.data) as ViewerControlMessage;
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
        if (msg.request_id) {
          const pending = pendingRequestsRef.current.get(msg.request_id);
          if (pending) {
            pendingRequestsRef.current.delete(msg.request_id);
            pending.resolve();
          }
        }
      } else if (msg.msg_type === "request_config_error") {
        const pending = pendingRequestsRef.current.get(msg.request_id);
        if (pending) {
          pendingRequestsRef.current.delete(msg.request_id);
          pending.reject(new RequestConfigError(msg.code, msg.message));
        }
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
      rejectAllPending(new RequestConfigError("CONFIG_REJECTED", "connection closed"));
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
      rejectAllPending(new RequestConfigError("CONFIG_REJECTED", "page navigated away"));
      retryEnabledRef.current = false;
      gotServerErrorRef.current = false;
      retryCountRef.current = 0;
      configRef.current = null;
      setConnectionState("idle");
      setConfig(null);
      setLastError(null);
    };
  }, [connect, rejectAllPending]);

  const onFrame = useCallback(
    (cb: (frame: ViewerSpectrumFrameMessage) => void) => {
      frameCallbacks.current.add(cb);
      return () => {
        frameCallbacks.current.delete(cb);
      };
    },
    [],
  );

  const sendRequestConfig = useCallback(
    (rf: RfConfigRequest, tuner: TunerConfigRequest | null): Promise<void> => {
      const ws = wsRef.current;
      if (ws === null || ws.readyState !== WebSocket.OPEN) {
        return Promise.reject(
          new RequestConfigError("CONFIG_REJECTED", "not connected"),
        );
      }
      const requestId = newRequestId();
      const payload: Record<string, unknown> = {
        msg_type: "request_config",
        request_id: requestId,
        rf,
      };
      if (tuner !== null) payload.tuner = tuner;
      return new Promise<void>((resolve, reject) => {
        pendingRequestsRef.current.set(requestId, { resolve, reject });
        try {
          ws.send(JSON.stringify(payload));
        } catch (err) {
          pendingRequestsRef.current.delete(requestId);
          reject(
            new RequestConfigError(
              "CONFIG_REJECTED",
              err instanceof Error ? err.message : "send failed",
            ),
          );
        }
      });
    },
    [],
  );

  return { connectionState, config, lastError, onFrame, sendRequestConfig };
}
