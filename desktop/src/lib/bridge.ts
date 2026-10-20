// Typed client for the Tauri process bridge. All engine access goes through `bridge_request`.
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

import type { BridgeErrorData, EventsAppended } from "./types";

export class BridgeError extends Error {
  readonly kind: string;
  readonly hint: string | null;
  readonly code: number;

  constructor(payload: BridgeErrorData) {
    super(payload.message);
    this.kind = payload.data?.kind ?? "internal";
    this.hint = payload.data?.hint ?? null;
    this.code = payload.code;
  }
}

export function toBridgeError(raw: unknown): BridgeError {
  if (raw instanceof BridgeError) return raw;
  if (raw && typeof raw === "object" && "message" in raw) return new BridgeError(raw as BridgeErrorData);
  return new BridgeError({ code: -32603, message: String(raw), data: { kind: "internal" } });
}

export async function call<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  try {
    return await invoke<T>("bridge_request", { method, params });
  } catch (raw) {
    throw toBridgeError(raw);
  }
}

export function onEvents(handler: (payload: EventsAppended) => void): Promise<UnlistenFn> {
  return listen<{ method: string; params: EventsAppended }>("bridge://notification", (event) => {
    if (event.payload.method === "events/appended") handler(event.payload.params);
  });
}

export function onBridgeStatus(handler: (state: string) => void): Promise<UnlistenFn> {
  return listen<{ state: string }>("bridge://status", (event) => handler(event.payload.state));
}

/** A fresh key per user action, so a double submit or a replay never starts two runs. */
export function idempotencyKey(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}`;
}
