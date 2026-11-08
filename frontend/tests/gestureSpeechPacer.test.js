import test from "node:test";
import assert from "node:assert/strict";
import { GestureSpeechPacer } from "../src/avatar/GestureSpeechPacer.js";

function buffer(seconds, channels = 2, sampleRate = 1000) {
  const length = Math.round(seconds * sampleRate);
  const data = Array.from({ length: channels }, (_, index) => new Float32Array(length).fill(index + 1));
  return { length, sampleRate, numberOfChannels: channels, duration: length / sampleRate,
    getChannelData: channel => data[channel] };
}
const context = { createBuffer(channels, length, sampleRate) {
  const result = buffer(length / sampleRate, channels, sampleRate);
  for (let channel = 0; channel < channels; channel++) result.getChannelData(channel).fill(0);
  return result;
} };
function speech(actions, starts) {
  return { words: actions, wtimes: starts, wdurations: actions.map(() => 100),
    gestures: actions.map((action, index) => ({ action, at_ms: starts[index], duration_ms: 100, word_index: index })) };
}

test("the supplied gesture list waits for each speech block and a two-second gesture", () => {
  const pacer = new GestureSpeechPacer(2, "complete");
  const actions = ["thankful", "greeting", "nod_yes", "nod_no", "thinking", "laughing", "thankful", "enthusiasm", "clapping"];
  const starts = [0, 5000, 5500, 5700, 6000, 6400, 6800, 7100, 7400];
  const input = speech(actions, starts);
  const original = buffer(8);
  const result = pacer.pace(context, original, input, () => 1.5);
  const expected = [0, 5000, 7000, 9000, 11000, 13000, 15000, 17000, 19000];
  assert.deepEqual(result.gestures.map(cue => cue.at_ms), expected);
  assert.deepEqual(result.wtimes, expected);
  assert.ok(result.gestures.every(cue => cue.duration_ms === 2000));
  assert.equal(result.audio.duration, 19.6);
  assert.deepEqual(input.wtimes, starts);
  assert.deepEqual(result.wdurations, input.wdurations); // Speech is never sped up or stretched.
  for (let channel = 0; channel < 2; channel++) {
    assert.equal(result.audio.getChannelData(channel).filter(value => value !== 0).length, original.length);
    assert.ok(result.audio.getChannelData(channel).slice(5500, 7000).every(value => value === 0));
    assert.equal(result.audio.getChannelData(channel)[7000], channel + 1);
  }
});

test("a gesture gate survives packet boundaries and preserves longer natural clips", () => {
  const pacer = new GestureSpeechPacer(2, "complete");
  pacer.pace(context, buffer(1), speech(["clapping"], [0]), () => 4.2);
  assert.equal(pacer.remaining, 3.2);
  // Untagged continuation belongs to the previous speech block.
  const continuation = buffer(1);
  const middle = pacer.pace(context, continuation, speech([], []), () => 4.2);
  assert.equal(middle.audio, continuation);
  const next = pacer.pace(context, buffer(.5), speech(["laughing"], [0]), () => 4.2);
  assert.equal(next.gestures[0].at_ms, 2200);
  assert.equal(next.gestures[0].duration_ms, 4200);
  assert.equal(next.audio.duration, 2.7);
});

test("minimum duration is configurable and cancellation clears the previous gate", () => {
  const pacer = new GestureSpeechPacer(.75, "complete");
  const first = pacer.pace(context, buffer(.2), speech(["greeting"], [0]), () => .5);
  assert.equal(first.gestures[0].duration_ms, 750);
  pacer.clear();
  const next = pacer.pace(context, buffer(.2), speech(["clapping"], [0]), () => .5);
  assert.equal(next.gestures[0].at_ms, 0);
});

test("pacing respects source playback rate and tags at the same boundary", () => {
  const pacer = new GestureSpeechPacer(2, "complete");
  const result = pacer.pace(context, buffer(1), speech(["greeting", "nod_yes"], [0, 0]), () => 1, 2);
  assert.deepEqual(result.gestures.map(cue => cue.at_ms), [0, 4000]);
  assert.deepEqual(result.gestures.map(cue => cue.duration_ms), [4000, 4000]);
  assert.deepEqual(result.wtimes, [4000, 4000]);
  assert.equal(result.audio.duration, 5);
});


test("speech pacing keeps contrasting gestures at their words despite different clip lengths", () => {
  const pacer = new GestureSpeechPacer();
  const input = speech(["thankful", "laughing", "thinking"], [0, 700, 1500]);
  const audio = buffer(2);
  const result = pacer.pace(context, audio, input,
    action => ({ thankful: 6, laughing: 4, thinking: 8 })[action]);
  assert.equal(result.audio, audio);
  assert.deepEqual(result.wtimes, [0, 700, 1500]);
  assert.deepEqual(result.gestures.map(cue => cue.at_ms), [0, 700, 1500]);
  assert.deepEqual(result.gestures.map(cue => cue.duration_ms), [6000, 4000, 8000]);
  const next = pacer.pace(context, buffer(1), speech(["clapping"], [0]), () => 5);
  assert.equal(next.gestures[0].at_ms, 0);
  assert.equal(next.audio.duration, 1);
});
