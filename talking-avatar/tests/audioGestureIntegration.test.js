import test from "node:test";
import assert from "node:assert/strict";
import { TalkingHead } from "../src/talkinghead/talkinghead.mjs";
import { GestureTimeline } from "../src/avatar/GestureTimeline.js";
import { TalkingGestures } from "../src/avatar/TalkingGestures.js";
import { waitForAction } from "../src/avatar/ActionQueue.js";

function fakeHead() {
  const sources = [];
  const head = Object.assign(Object.create(TalkingHead.prototype), {
    armature: {}, avatar: { lipsyncLang: "en" }, opt: {},
    speechQueue: [], audioPlaylist: [], animQueue: [], animClock: 1000,
    animSlowdownRate: 1, audioAnalyzerNode: {},
    lipsyncWordsToVisemes: () => null,
    lipsyncPreProcessText: (word) => word,
    audioCtx: {
      state: "running", currentTime: 10,
      createBufferSource() {
        const source = { playbackRate: { value: 1 }, connect() {}, disconnect() {},
          start(time) { this.startedAt = time; } };
        sources.push(source);
        return source;
      },
    },
  });
  return { head, sources };
}

function speech(head, cue, onStart) {
  head.speakAudio({ audio: { duration: 2 }, words: ["Hello"], wtimes: [0], wdurations: [500],
    onAudioStart: (start, rate, duration) => onStart(cue, start, rate, duration),
  }, { isRaw: true });
}

test("inline cues survive the actual speech-to-audio playlist and follow each segment's start", () => {
  const { head, sources } = fakeHead();
  const calls = [];
  const timeline = new GestureTimeline((action) => calls.push(action), () => false);
  const starts = [];
  const start = (cue, time, rate, duration) => {
    starts.push({ time, duration });
    timeline.schedule([cue], time, rate);
  };
  speech(head, { action: "greeting", at_ms: 20 }, start);
  speech(head, { action: "clapping", at_ms: 100 }, start);
  assert.deepEqual(starts, [{ time: 10, duration: 2 }]);
  assert.equal(sources.length, 1);
  timeline.advance(10.03);
  assert.deepEqual(calls, ["greeting"]);
  // The second segment was queued earlier but starts only after the first ends.
  head.audioCtx.currentTime = 12;
  sources[0].onended();
  assert.deepEqual(starts, [{ time: 10, duration: 2 }, { time: 12, duration: 2 }]);
  timeline.advance(12.11);
  assert.deepEqual(calls, ["greeting", "clapping"]);
});

test("audio-start cues preempt background talking and preserve tagged-action priority", async () => {
  const { head } = fakeHead();
  let busy = false;
  let backgroundSignal;
  let taggedAction = null;
  const talking = new TalkingGestures(async (_, signal) => {
    backgroundSignal = signal;
    await waitForAction(10000, signal);
  }, () => busy);
  const timeline = new GestureTimeline((action) => {
    busy = true;
    taggedAction = talking.interrupt().then(() => action);
  }, () => busy);
  speech(head, { action: "clapping", at_ms: 400 }, (cue, start, rate, duration) => {
    timeline.schedule([cue], start, rate);
    talking.until(start + duration / rate);
  });
  talking.update(10);
  await Promise.resolve();
  assert.equal(backgroundSignal.aborted, false);
  timeline.advance(10.41);
  assert.equal(backgroundSignal.aborted, true);
  assert.equal(await taggedAction, "clapping");
  talking.update(10.5);
  assert.equal(talking.active, null);
  await talking.clear();
});
