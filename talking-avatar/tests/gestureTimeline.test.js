import test from "node:test";
import assert from "node:assert/strict";
import { GestureTimeline } from "../src/avatar/GestureTimeline.js";

test("cues follow actual playlist audio starts and playback rate", () => {
  const calls = [];
  const timeline = new GestureTimeline((action) => calls.push(action), () => false);
  timeline.schedule([{ action: "wave", at_ms: 500 }], 20);
  timeline.advance(20.4);
  assert.deepEqual(calls, []);
  timeline.advance(20.5);
  assert.deepEqual(calls, ["wave"]);
  timeline.schedule([{ action: "nod", at_ms: 1000 }], 30, 0.5);
  timeline.advance(31);
  assert.deepEqual(calls, ["wave"]);
  timeline.advance(32);
  assert.deepEqual(calls, ["wave", "nod"]);
});

test("foreground gestures are mandatory; only optional talking cues can be skipped", () => {
  const calls = [];
  let busy = true;
  const timeline = new GestureTimeline((action) => calls.push(action), () => busy);
  timeline.schedule([{ action: "laughing", at_ms: 0 }, { action: "clapping", at_ms: 500 },
    { action: "talking", at_ms: 500 }], 1);
  timeline.advance(1);
  busy = false;
  timeline.advance(2);
  assert.deepEqual(calls, ["laughing", "clapping"]);
  assert.equal(timeline.skipped, 1);
  timeline.schedule([{ action: "nod", at_ms: 0 }], 3);
  timeline.clear();
  timeline.advance(3);
  assert.deepEqual(calls, ["laughing", "clapping"]);
});

test("a leading foreground tag reserves time before background talking can begin", () => {
  const timeline = new GestureTimeline(() => {}, () => false);
  timeline.schedule([{ action: "laughing", at_ms: 20 }], 10);
  assert.equal(timeline.hasUpcomingForeground(10), true);
  timeline.advance(10.03);
  assert.equal(timeline.hasUpcomingForeground(10.03), false);
});
