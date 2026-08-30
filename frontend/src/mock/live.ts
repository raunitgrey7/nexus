/** Mock `LiveClient`: replays the in-browser sim as `/ws/live` frames. */
import type { ClientFrame, ConnectionState } from "@/lib/types";
import type { FrameListener, LiveClient, StateListener } from "@/lib/ws";
import { makeForecast } from "./fixtures";
import { getSim } from "./sim";

class MockLiveClient implements LiveClient {
  private frameListeners = new Set<FrameListener>();
  private stateListeners = new Set<StateListener>();
  private unsubscribe: (() => void) | null = null;
  private forecastTimer: ReturnType<typeof setInterval> | null = null;
  private tickEvery = 1;
  private tickCount = 0;
  private _state: ConnectionState = "idle";

  get state(): ConnectionState {
    return this._state;
  }

  connect(): void {
    const sim = getSim();
    this.setState("connecting");
    this.unsubscribe = sim.subscribe((frame) => {
      if (frame.type === "tick") {
        this.tickCount++;
        if (this.tickCount % this.tickEvery !== 0) return;
      }
      for (const cb of this.frameListeners) cb(frame);
    });
    setTimeout(() => {
      this.setState("mock");
      const hello = { type: "hello" as const, world: sim.world("open", true), kpis: sim.kpis(), status: sim.status() };
      for (const cb of this.frameListeners) cb(hello);
      if (!sim.running) sim.start();
    }, 250);
    this.forecastTimer = setInterval(() => {
      if (!sim.running) return;
      const forecast = makeForecast(sim.tick, sim.kpis(), sim.robotModels(), sim.zones, sim.zoneOccupancy, sim.demandMultiplier, 60);
      for (const cb of this.frameListeners) cb({ type: "forecast", forecast });
    }, 30_000);
  }

  disconnect(): void {
    this.unsubscribe?.();
    this.unsubscribe = null;
    if (this.forecastTimer) clearInterval(this.forecastTimer);
    this.forecastTimer = null;
    this.setState("closed");
  }

  send(frame: ClientFrame): void {
    const sim = getSim();
    switch (frame.type) {
      case "control":
        if (frame.action === "start") sim.start();
        else if (frame.action === "pause") sim.pause();
        else if (frame.action === "step") sim.step(frame.ticks ?? 1);
        else if (frame.action === "speed" && frame.ticks_per_second) sim.setSpeed(frame.ticks_per_second);
        break;
      case "subscribe":
        this.tickEvery = Math.max(1, Math.floor(frame.tick_every));
        break;
      case "ping":
        for (const cb of this.frameListeners) cb({ type: "pong" });
        break;
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

  private setState(s: ConnectionState): void {
    this._state = s;
    for (const cb of this.stateListeners) cb(s);
  }
}

export function createMockLiveClient(): LiveClient {
  return new MockLiveClient();
}
