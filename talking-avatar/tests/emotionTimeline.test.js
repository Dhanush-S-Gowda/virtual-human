import test from "node:test";
import assert from "node:assert/strict";
import { EmotionTimeline } from "../src/avatar/EmotionTimeline.js";

function fixture() {
  const calls = [];
  const avatar = { moodName: "neutral", setMood(mood) { this.moodName = mood; calls.push(mood); } };
  return { avatar, calls, timeline: new EmotionTimeline(() => avatar) };
}

test("expression starts on audio time, persists over chunks, and resets at the end", () => {
  const { calls, timeline } = fixture();
  timeline.schedule("happy", 10, 2);
  timeline.schedule("happy", 12, 2);
  timeline.schedule("sad", 14, 2);
  timeline.schedule("neutral", 16, 2);
  timeline.update(9);
  assert.deepEqual(calls, []);
  for (const time of [10, 11, 12, 13]) timeline.update(time);
  assert.deepEqual(calls, ["happy"]);
  timeline.update(14);
  assert.deepEqual(calls, ["happy", "sad"]);
  timeline.update(16);
  timeline.update(19);
  assert.deepEqual(calls, ["happy", "sad", "neutral"]);
});

test("interruption clears queued expressions and returns to neutral immediately", () => {
  const { calls, timeline } = fixture();
  timeline.schedule("happy", 0, 5);
  timeline.schedule("sad", 5, 5);
  timeline.update(1);
  timeline.clear();
  timeline.update(6);
  assert.deepEqual(calls, ["happy", "neutral"]);
});

test("ended or delayed audio never leaves an expression stuck", () => {
  const { calls, timeline } = fixture();
  timeline.schedule("sad", 10, 1);
  timeline.update(10);
  timeline.update(12);
  assert.deepEqual(calls, ["sad", "neutral"]);
  timeline.schedule("happy", 13, 1);
  timeline.update(20); // Rendering missed the whole audio interval.
  assert.deepEqual(calls, ["sad", "neutral"]);
});

test("audio rate changes duration and invalid emotions fall back to neutral", () => {
  const { calls, timeline } = fixture();
  timeline.schedule("happy", 10, 2, 2);
  timeline.update(10);
  timeline.update(11);
  assert.deepEqual(calls, ["happy", "neutral"]);
  timeline.schedule("run_shell", 12, 2);
  timeline.update(12);
  assert.deepEqual(calls, ["happy", "neutral"]);
  for (const [start, duration, rate] of [[NaN, 2, 1], [0, -1, 1], [0, 1, 0], [0, 1, Infinity]]) {
    timeline.schedule("happy", start, duration, rate);
  }
  assert.equal(timeline.pending.length, 1);
});

test("preview stays untouched without speech; new audio owns its default emotion", () => {
  const { avatar, timeline } = fixture();
  avatar.setMood("love");
  timeline.update(0);
  assert.equal(avatar.moodName, "love");
  timeline.schedule(undefined, 1, 2);
  timeline.update(1);
  assert.equal(avatar.moodName, "neutral");
});
