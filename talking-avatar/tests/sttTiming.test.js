import test from "node:test";
import assert from "node:assert/strict";
import { STTManager, STTState } from "../src/stt/STTManager.js";

test("STT completion starts at release and includes flush/transport, not speaking", async (t) => {
  let now = 100000; // A long recording has already happened.
  const sent = [];
  const requests = [];
  t.mock.method(globalThis, "fetch", async (url, options) => {
    requests.push({ url, body: JSON.parse(options.body) });
    return { ok: true };
  });
  const previousWebSocket = globalThis.WebSocket;
  globalThis.WebSocket = { OPEN: 1 };
  t.after(() => { globalThis.WebSocket = previousWebSocket; });
  const manager = new STTManager({ endpoint: "http://backend", sessionId: "test", now: () => now });
  manager.state = STTState.LISTENING;
  manager.socket = { readyState: 1, send: (body) => sent.push(JSON.parse(body)) };
  manager.captureNode = {
    port: { postMessage() { now += 25; manager.captureStopped(); } },
    disconnect() {},
  };
  assert.equal(await manager.stopRecording(), true);
  assert.equal(sent[0].type, "end");
  manager._recordTranscriptTiming({ type: "transcript_partial" }, 100050);
  assert.equal(requests.length, 0);
  now = 100080;
  const event = { type: "transcript", stt_measurement_id: "token" };
  manager._recordTranscriptTiming(event, now);
  assert.equal(event.release_to_transcript_ms, 80);
  assert.deepEqual(requests, [{ url: "http://backend/telemetry/stt", body: {
    measurement_id: "token", elapsed_ms: 80,
  } }]);
  manager._recordTranscriptTiming(event, 100090);
  assert.equal(requests.length, 1);
});

test("a transcript without a release does not produce a sample", (t) => {
  t.mock.method(globalThis, "fetch", () => assert.fail("Unexpected telemetry"));
  const manager = new STTManager({ endpoint: "http://backend", sessionId: "test" });
  manager._recordTranscriptTiming({ type: "transcript", stt_measurement_id: "token" }, 1000);
});
