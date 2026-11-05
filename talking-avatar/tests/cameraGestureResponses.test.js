import test from "node:test";
import assert from "node:assert/strict";
import { CameraGestureResponses } from "../src/cv/cameraGestureResponses.js";
import { GestureEvents } from "../src/cv/gestureEvents.js";
import { ActionQueue, waitForAction } from "../src/avatar/ActionQueue.js";
import { GestureTimeline } from "../src/avatar/GestureTimeline.js";
import { CLIP_ACTIONS } from "../src/avatar/clipActions.js";

async function flush() { for (let i = 0; i < 20; i++) await Promise.resolve(); }

test("confirmed wave, thumbs-up and open-palm events trigger catalog animations", () => {
  const calls = [];
  const responses = new CameraGestureResponses((action) => calls.push(action), () => false, () => 0);
  const events = new GestureEvents().update([{ track_id: 1,
    actions: ["wave", "thumbs_up", "open_palm"] }], 0);
  for (const event of events) responses.handle(event);
  assert.deepEqual(calls, ["greeting", "enthusiasm", "nod_yes"]);
  for (const action of calls) assert.ok(CLIP_ACTIONS[action]);
  for (const event of events) responses.handle(event);
  assert.equal(calls.length, 3); // Duplicate local/server detections do not retrigger.
});

test("camera reactions wait behind speech actions and stale reactions are discarded", () => {
  const calls = [];
  let time = 0;
  let busy = true;
  const responses = new CameraGestureResponses((action) => calls.push(action), () => busy, () => time);
  responses.handle({ track_id: 1, action: "thumbs_up" });
  assert.deepEqual(calls, []);
  busy = false;
  time = 1000;
  responses.update();
  assert.deepEqual(calls, ["enthusiasm"]);
  busy = true;
  responses.handle({ track_id: 1, action: "wave" });
  time = 7000;
  busy = false;
  responses.update();
  assert.deepEqual(calls, ["enthusiasm"]);
});

test("inline tags preempt camera reactions without discarding queued manual actions", async () => {
  const calls = [];
  const queue = new ActionQueue();
  const camera = new CameraGestureResponses((action) => queue.enqueue(async (signal) => {
    calls.push(action);
    await waitForAction(10000, signal);
    calls.push(signal.aborted ? "camera cancelled" : "camera done");
  }, { kind: "camera" }), () => queue.busy, () => 0);
  camera.handle({ action: "wave", track_id: 1 });
  queue.enqueue(async () => { calls.push("manual preserved"); });
  const timeline = new GestureTimeline((action) => queue.enqueue(async () => {
    calls.push(action);
  }, { kind: "tagged" }), () => queue.busy && queue.activeKind !== "camera");
  timeline.schedule([{ action: "clapping", at_ms: 0 }, { action: "laughing", at_ms: 0 }], 1);
  timeline.advance(1);
  assert.equal(queue.activeKind, "tagged");
  await flush();
  assert.deepEqual(calls, ["greeting", "camera cancelled", "clapping", "laughing", "manual preserved"]);
  assert.equal(timeline.skipped, 0);
  assert.equal(queue.busy, false);
});

test("Stop/reconnect clears pending camera reactions and unknown actions do nothing", () => {
  const calls = [];
  let busy = true;
  const camera = new CameraGestureResponses((action) => calls.push(action), () => busy, () => 0);
  camera.handle({ action: "wave", track_id: 1 });
  camera.clear();
  busy = false;
  camera.update();
  camera.handle({ action: "unknown", track_id: 1 });
  assert.deepEqual(calls, []);
});
