import test from "node:test";
import assert from "node:assert/strict";
import { GestureEvents } from "../src/cv/gestureEvents.js";

test("gesture events include the person and do not repeat each render", () => {
  const tracker = new GestureEvents();
  const person = { track_id: 4, display_name: "Alex", actions: ["wave"] };
  assert.deepEqual(tracker.update([person], 0), [{ event_type: "action_detected",
    track_id: 4, display_name: "Alex", action: "wave" }]);
  assert.deepEqual(tracker.update([person], 100), []);
  assert.deepEqual(tracker.update([], 500), []);
  assert.deepEqual(tracker.update([person], 700), []);
  tracker.update([], 1600);
  assert.equal(tracker.update([person], 1700).length, 1);
});

test("each track has independent events", () => {
  const tracker = new GestureEvents();
  assert.equal(tracker.update([1, 2].map((track_id) => ({ track_id, actions: ["wave"] })), 0).length, 2);
});
