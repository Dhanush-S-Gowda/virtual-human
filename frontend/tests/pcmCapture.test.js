import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { STTManager, STTState } from "../src/stt/STTManager.js";

test("worklet streams PCM16 and flushes the remaining samples before stopped", () => {
  const messages = [];
  let Processor;
  vm.runInNewContext(readFileSync(new URL("../src/stt/pcm-worklet.js", import.meta.url), "utf8"), {
    AudioWorkletProcessor: class { constructor() { this.port = { postMessage: (data) => messages.push(data) }; } },
    sampleRate: 16000,
    registerProcessor: (name, processor) => { Processor = processor; },
  });
  const capture = new Processor();
  const input = new Float32Array(128).fill(.5);
  for (let index = 0; index < 14; index++) capture.process([[input]]);
  assert.equal(messages.length, 1); // Sent during recording, before stop.
  assert.equal(new DataView(messages[0].buffer).getInt16(0, true), 16384);
  assert.equal(messages[0].rms, .5);
  capture.port.onmessage({ data: "stop" });
  assert.equal(messages[1].buffer.byteLength, 128 * 2);
  assert.equal(messages[2].stopped, true);
  capture.process([[input]]);
  assert.equal(messages.length, 3);
});

test("recording stop waits for worklet flush before sending end and releases resources", async () => {
  const sent = [];
  let stopped = 0;
  globalThis.WebSocket = { OPEN: 1 };
  const manager = new STTManager({ endpoint: "http://localhost:8000", sessionId: "test",
    onCaptureEnd: () => { stopped++; } });
  manager.state = STTState.LISTENING;
  manager.socket = { readyState: 1, send: (message) => sent.push(message) };
  manager.audioContext = { close: async () => {} };
  manager.sourceNode = { disconnect() {} };
  manager.stream = { getTracks: () => [{ stop() { stopped++; } }] };
  manager.captureNode = { disconnect() {}, port: { postMessage(command) {
    assert.equal(command, "stop");
    assert.deepEqual(sent, []);
    queueMicrotask(() => { sent.push("last PCM packet"); manager.captureStopped(); });
  } } };
  assert.equal(await manager.stopRecording(), true);
  assert.deepEqual(sent, ["last PCM packet", '{"type":"end"}']);
  assert.equal(manager.state, STTState.TRANSCRIBING);
  assert.equal(manager.captureNode, null);
  assert.equal(stopped, 2);
});

test("microphone packets stream before release with the actual context sample rate", async (t) => {
  const globals = ["WebSocket", "AudioContext", "AudioWorkletNode", "navigator"];
  const descriptors = globals.map((key) => Object.getOwnPropertyDescriptor(globalThis, key));
  t.after(() => globals.forEach((key, index) => {
    if (descriptors[index]) Object.defineProperty(globalThis, key, descriptors[index]);
    else delete globalThis[key];
  }));
  const sent = [];
  let stopped = 0;
  class Socket {
    static OPEN = 1;
    static CLOSED = 3;
    constructor() { this.readyState = 1; queueMicrotask(() => this.onopen()); }
    send(value) { sent.push(value); }
    close() { this.readyState = 3; this.onclose?.(); }
  }
  globalThis.WebSocket = Socket;
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { mediaDevices: {
    getUserMedia: async () => ({ getTracks: () => [{ stop() { stopped++; } }] }),
  } } });
  globalThis.AudioContext = class {
    constructor() { this.sampleRate = 48000; this.audioWorklet = { addModule: async () => {} }; }
    async resume() {}
    async close() {}
    createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
  };
  globalThis.AudioWorkletNode = class {
    constructor() { this.port = { postMessage: () => queueMicrotask(() => this.port.onmessage({ data: { stopped: true } })) }; }
    connect() {}
    disconnect() {}
  };
  const activity = [];
  const manager = new STTManager({ endpoint: "http://localhost:8000", sessionId: "test",
    getStartOptions: () => ({ voice: "M1" }),
    onAudioActivity: (sample) => activity.push(sample) });
  assert.equal(await manager.startRecording(), true);
  assert.deepEqual(JSON.parse(sent[0]), { type: "start", session_id: "test",
    mime_type: "audio/pcm", sample_rate: 48000, voice: "M1" });
  const buffer = new ArrayBuffer(3200);
  manager.captureNode.port.onmessage({ data: { buffer, rms: .2, durationMs: 100 } });
  assert.equal(sent[1], buffer);
  assert.equal(activity[0].rms, .2);
  assert.equal(sent.length, 2); // No end event while recording.
  assert.equal(await manager.stopRecording(), true);
  assert.equal(JSON.parse(sent[2]).type, "end");
  assert.equal(stopped, 1);
  manager.cancelRecording();
});

test("releasing Talk during permission setup does not leave a recording running", async (t) => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  t.after(() => {
    if (descriptor) Object.defineProperty(globalThis, "navigator", descriptor);
    else delete globalThis.navigator;
  });
  let allowMicrophone;
  let stopped = false;
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { mediaDevices: {
    getUserMedia: () => new Promise((resolve) => { allowMicrophone = resolve; }),
  } } });
  const manager = new STTManager({ endpoint: "http://localhost:8000", sessionId: "test" });
  const starting = manager.startRecording();
  await Promise.resolve();
  assert.equal(await manager.stopRecording(), false);
  allowMicrophone({ getTracks: () => [{ stop() { stopped = true; } }] });
  assert.equal(await starting, false);
  assert.equal(stopped, true);
  assert.equal(manager.isRecording(), false);
});

test("releasing Talk during voice-settings lookup never opens the microphone", async (t) => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  t.after(() => {
    if (descriptor) Object.defineProperty(globalThis, "navigator", descriptor);
    else delete globalThis.navigator;
  });
  let requests = 0;
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { mediaDevices: {
    getUserMedia: () => { requests++; throw new Error("must not open"); },
  } } });
  let resolveVoice;
  const manager = new STTManager({ endpoint: "http://localhost:8000", sessionId: "test",
    getStartOptions: () => new Promise((resolve) => { resolveVoice = resolve; }) });
  const starting = manager.startRecording();
  await manager.stopRecording();
  resolveVoice({ voice: "M1" });
  assert.equal(await starting, false);
  assert.equal(requests, 0);
});

test("an old flush cannot send end or tear down a new recording after cancellation", async () => {
  globalThis.WebSocket = { OPEN: 1 };
  const manager = new STTManager({ endpoint: "http://localhost:8000", sessionId: "test" });
  manager.state = STTState.LISTENING;
  manager.socket = { readyState: 1, send() {}, close() {} };
  manager.captureNode = { disconnect() {}, port: { postMessage() {} } };
  const stopping = manager.stopRecording();
  manager.cancelRecording();
  const sent = [];
  let newCaptureStopped = false;
  manager.socket = { readyState: 1, send: (data) => sent.push(data), close() {} };
  manager.captureNode = { disconnect() { newCaptureStopped = true; }, port: {} };
  manager.state = STTState.LISTENING;
  assert.equal(await stopping, false);
  assert.deepEqual(sent, []);
  assert.equal(newCaptureStopped, false);
  assert.equal(manager.isRecording(), true);
  manager.cancelRecording();
});
