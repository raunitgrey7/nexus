/** Public runtime configuration. `NEXT_PUBLIC_*` values are inlined at build time. */

export const API_URL: string = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");
export const WS_URL: string = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws/live";
export const IS_MOCK: boolean = process.env.NEXT_PUBLIC_MOCK === "1";
