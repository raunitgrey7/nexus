import type { ClientFrame, ConnectionState, ServerFrame } from "./types";

export type FrameListener = (frame: ServerFrame) => void;
export type StateListener = (state: ConnectionState) => void;

/** Live stream abstraction — implemented over `/ws/live` and by the in-browser mock. */
export interface LiveClient {
  readonly state: ConnectionState;
  connect(): void;
  disconnect(): void;
  send(frame: ClientFrame): void;
  onFrame(cb: FrameListener): () => void;
  onState(cb: StateListener): () => void;
}

const FRAME_TYPES = new Set(["hello", "tick", "event", "decision", "forecast", "whatif", "status", "pong"]);

export function parseFrame(raw: string): ServerFrame | null {
  try {
    const data: unknown = JSON.parse(raw);
    if (!data || typeof data !== "object") return null;
    const t = (data as { type?: unknown }).type;
    if (typeof t !== "string" || !FRAME_TYPES.has(t)) return null;
    return data as ServerFrame;
  } catch {
    return null;
  }
}

export interface WebSocketLiveOptions {
  /** first retry delay (ms) */
  baseDelayMs?: number;
  /** max retry delay (ms) */
  maxDelayMs?: number;
  /** ping interval (ms) */
  pingMs?: number;
  /** tick throttle sent on open */
  tickEvery?: number;
}

/** WebSocket client with exponential back-off reconnect, keep-alive pings and frame validation. */
export class WebSocketLiveClient implements LiveClient {
  private ws: WebSocket | null = null;
  private frameListeners = new Set<FrameListener>();
  private stateListeners = new Set<StateListener>();
  private attempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private manualClose = false;
  private _state: ConnectionState = "idle";
  private readonly opts: Required<WebSocketLiveOptions>;

  constructor(
    private readonly url: string,
    opts: WebSocketLiveOptions = {},
  ) {
    this.opts = {
      baseDelayMs: opts.baseDelayMs ?? 1000,
      maxDelayMs: opts.maxDelayMs ?? 20_000,
      pingMs: opts.pingMs ?? 15_000,
      tickEvery: opts.tickEvery ?? 1,
    };
  }

  get state(): ConnectionState {
    return this._state;
  }

  connect(): void {
    if (typeof window === "undefined") return;
    this.manualClose = false;
    this.open();
  }

  disconnect(): void {
    this.manualClose = true;
    this.clearTimers();
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
    this.setState("closed");
  }

  send(frame: ClientFrame): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(frame));
    }
  }

  onFrame(cb: FrameListener): () => void {
    this.frameListeners.add(cb);
    return () => this.frameListeners.delete(cb);
  }

  onState(cb: StateListener): () => void {
    this.stateListeners.add(cb);
    cb(this._state);
    return () => this.stateListeners.delete(cb);
  }

  // ---- internals ------------------------------------------------------------------------------

  private open(): void {
    this.clearTimers();
    this.setState(this.attempt === 0 ? "connecting" : "reconnecting");
    let ws: WebSocket;
    try {
      ws = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      this.attempt = 0;
      this.setState("open");
      this.send({ type: "subscribe", tick_every: this.opts.tickEvery });
      this.pingTimer = setInterval(() => this.send({ type: "ping" }), this.opts.pingMs);
    };
    ws.onmessage = (ev: MessageEvent<string>) => {
      const frame = parseFrame(typeof ev.data === "string" ? ev.data : "");
      if (!frame) return;
      for (const cb of this.frameListeners) cb(frame);
    };
    ws.onerror = () => {
      /* onclose follows */
    };
    ws.onclose = () => {
      this.ws = null;
      if (this.pingTimer) clearInterval(this.pingTimer);
      this.pingTimer = null;
      if (!this.manualClose) this.scheduleReconnect();
    };
  }

  private scheduleReconnect(): void {
    this.setState("reconnecting");
    const exp = Math.min(this.opts.maxDelayMs, this.opts.baseDelayMs * 2 ** this.attempt);
    const jitter = exp * (0.8 + Math.random() * 0.4);
    this.attempt = Math.min(this.attempt + 1, 10);
    this.reconnectTimer = setTimeout(() => this.open(), jitter);
  }

  private clearTimers(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.pingTimer) clearInterval(this.pingTimer);
    this.reconnectTimer = null;
    this.pingTimer = null;
  }

  private setState(s: ConnectionState): void {
    if (this._state === s) return;
    this._state = s;
    for (const cb of this.stateListeners) cb(s);
  }
}
