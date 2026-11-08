import test from "node:test";
import assert from "node:assert/strict";
import { STTManager } from "../src/stt/STTManager.js";

test("relative API endpoints use secure same-origin WebSockets behind a gateway", () => {
  const previous = globalThis.location;
  globalThis.location = { origin: "https://demo.example" };
  try {
    const manager = new STTManager({ endpoint: "/api", sessionId: "test" });
    assert.equal(manager.websocketUrl, "wss://demo.example/api/ws");
  } finally {
    if (previous === undefined) delete globalThis.location;
    else globalThis.location = previous;
  }
});

test("explicit development API endpoints retain their port", () => {
  const manager = new STTManager({ endpoint: "http://localhost:8000/", sessionId: "test" });
  assert.equal(manager.websocketUrl, "ws://localhost:8000/ws");
});
