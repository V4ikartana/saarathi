import { beforeEach, describe, expect, it, vi } from "vitest";

// A plain implementation variable: a vi.fn() spy tracks the rejected promise itself and reports it as a failure.
let impl: (...args: unknown[]) => Promise<unknown> = () => Promise.resolve(null);
const calls: unknown[][] = [];
vi.mock("@tauri-apps/api/core", () => ({
  invoke: (...args: unknown[]) => {
    calls.push(args);
    return impl(...args);
  },
}));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn() }));

const { BridgeError, call, idempotencyKey, toBridgeError } = await import("./bridge");

describe("bridge client", () => {
  beforeEach(() => {
    calls.length = 0;
  });

  it("forwards method and params to the single Tauri command", async () => {
    impl = () => Promise.resolve({ protocol: 1 });
    await expect(call("system.initialize", { protocol: 1 })).resolves.toEqual({ protocol: 1 });
    expect(calls).toEqual([["bridge_request", { method: "system.initialize", params: { protocol: 1 } }]]);
  });

  it("turns JSON-RPC errors into typed errors that keep the engine's kind and hint", async () => {
    impl = () =>
      Promise.reject({ code: -32009, message: "run is still EXECUTING", data: { kind: "project_busy", hint: "stop it first" } });
    let err: InstanceType<typeof BridgeError> | undefined;
    try {
      await call("sessions.send");
    } catch (e) {
      err = e as InstanceType<typeof BridgeError>;
    }
    expect(err).toBeInstanceOf(BridgeError);
    if (!err) return;
    expect([err.kind, err.hint, err.message]).toEqual(["project_busy", "stop it first", "run is still EXECUTING"]);
  });

  it("never loses an unexpected failure", () => {
    const err = toBridgeError("boom");
    expect([err.kind, err.message]).toEqual(["internal", "boom"]);
  });

  it("gives every user action its own idempotency key", () => {
    const a = idempotencyKey("send");
    expect(a).toMatch(/^send-/);
    expect(idempotencyKey("send")).not.toBe(a);
  });
});
