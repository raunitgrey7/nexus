/**
 * Runtime selection between the HTTP/WebSocket clients and the offline mock. The mock module is
 * loaded lazily so it (and the world fixture) never enters the production bundle.
 */
import { restApi, type NexusApi } from "./api";
import { IS_MOCK, WS_URL } from "./env";
import { WebSocketLiveClient, type LiveClient } from "./ws";

let apiPromise: Promise<NexusApi> | null = null;

export function getApi(): Promise<NexusApi> {
  if (!apiPromise) {
    apiPromise = IS_MOCK ? import("@/mock/api").then((m) => m.mockApi) : Promise.resolve(restApi);
  }
  return apiPromise;
}

/** Facade with the same surface as `NexusApi`; resolves the implementation on first use. */
export const api: NexusApi = {
  health: () => getApi().then((a) => a.health()),
  status: () => getApi().then((a) => a.status()),
  control: (req) => getApi().then((a) => a.control(req)),
  world: (q) => getApi().then((a) => a.world(q)),
  robots: () => getApi().then((a) => a.robots()),
  orders: (q) => getApi().then((a) => a.orders(q)),
  entity: (id) => getApi().then((a) => a.entity(id)),
  kpis: (t) => getApi().then((a) => a.kpis(t)),
  spatial: () => getApi().then((a) => a.spatial()),
  events: (q) => getApi().then((a) => a.events(q)),
  recentEvents: (q) => getApi().then((a) => a.recentEvents(q)),
  injectEvent: (req) => getApi().then((a) => a.injectEvent(req)),
  faultPresets: () => getApi().then((a) => a.faultPresets()),
  fireFault: (id) => getApi().then((a) => a.fireFault(id)),
  forecast: (h) => getApi().then((a) => a.forecast(h)),
  createDecision: (req) => getApi().then((a) => a.createDecision(req)),
  decisions: (limit) => getApi().then((a) => a.decisions(limit)),
  decision: (id) => getApi().then((a) => a.decision(id)),
  decisionAction: (id, req) => getApi().then((a) => a.decisionAction(id, req)),
  createWhatIf: (req) => getApi().then((a) => a.createWhatIf(req)),
  whatifs: () => getApi().then((a) => a.whatifs()),
  whatif: (id) => getApi().then((a) => a.whatif(id)),
  whatifPresets: () => getApi().then((a) => a.whatifPresets()),
  nlq: (req) => getApi().then((a) => a.nlq(req)),
  timeline: (q) => getApi().then((a) => a.timeline(q)),
  snapshot: (tick) => getApi().then((a) => a.snapshot(tick)),
  benchmarks: () => getApi().then((a) => a.benchmarks()),
  strategies: () => getApi().then((a) => a.strategies()),
};

export async function createLiveClient(): Promise<LiveClient> {
  if (IS_MOCK) {
    const m = await import("@/mock/live");
    return m.createMockLiveClient();
  }
  return new WebSocketLiveClient(WS_URL);
}
