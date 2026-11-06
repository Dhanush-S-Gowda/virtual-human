import test from "node:test";
import assert from "node:assert/strict";
import { LaughingMouth } from "../src/avatar/LaughingMouth.js";
import { TalkingHead } from "../src/talkinghead/talkinghead.mjs";
import { playClipAction } from "../src/avatar/clipAction.js";

function morph() {
  return { fixed: null, realtime: null, system: null, systemd: null, newvalue: null,
    baseline: 0, base: null, ref: null, value: 0, applied: 0, needsUpdate: true,
    limit: null, onchange: null, min: 0, max: 1, ms: [[0]], is: [0], v: 0, acc: .00001, maxv: .005 };
}
function fixture(target = "viseme_aa") {
  const avatar = Object.assign(Object.create(TalkingHead.prototype), { mtAvatar: {
    [target]: morph(), mouthSmile: morph(),
  } });
  const mouth = new LaughingMouth(() => avatar);
  const apply = time => { mouth.update(time); avatar.updateMorphTargets(16); };
  return { avatar, mouth, target: avatar.mtAvatar[target], apply };
}

test("laughter produces repeated visible HA openings using the real morph updater", () => {
  const { mouth, target, apply } = fixture();
  mouth.start(10, 2);
  apply(10.12);
  const first = target.ms[0][0];
  assert.ok(first > .5);
  apply(10.36);
  assert.ok(target.ms[0][0] < .01);
  apply(10.56);
  assert.ok(target.ms[0][0] > .5);
  assert.equal(target.fixed, null);
  mouth.clear();
  assert.equal(target.realtime, null);
});

test("actual spoken intervals keep normal TTS lip sync; silence gets laughter", () => {
  const { mouth, target, apply } = fixture();
  mouth.scheduleSpeech({ words: ["Hello", "."], wtimes: [100, 500], wdurations: [400, 100] }, 10, 1);
  mouth.start(10, 2);
  apply(10.05);
  assert.notEqual(target.realtime, null);
  target.newvalue = .23;
  apply(10.2);
  assert.equal(target.realtime, null);
  assert.equal(target.ms[0][0], .23);
  apply(10.54); // Punctuation and padded silence must not suppress the HA motion.
  assert.notEqual(target.realtime, null);
  apply(12);
  assert.equal(mouth.active, null);
  assert.equal(target.realtime, null);
});

for (const name of ["jawOpen", "mouthOpen"]) {
  test(`avatars without AA visemes use ${name}`, () => {
    const { mouth, target, apply, avatar } = fixture(name);
    avatar.mtAvatar.viseme_aa = { ...morph(), ms: [] };
    mouth.start(0, 2);
    apply(.12);
    assert.ok(target.ms[0][0] > .5);
    mouth.clear();
  });
}

test("fixed face controls keep priority, and existing realtime values are restored", () => {
  const { mouth, target, apply } = fixture();
  target.fixed = .1;
  target.value = target.applied = target.ms[0][0] = .1;
  target.realtime = .2;
  mouth.start(0, 2);
  apply(.12);
  assert.equal(target.ms[0][0], .1);
  mouth.clear();
  assert.equal(target.fixed, .1);
  assert.equal(target.realtime, .2);
});

test("interruption releases only owned values and stale cleanup cannot stop a new laugh", () => {
  const { mouth, target } = fixture();
  const old = mouth.start(0, 2);
  mouth.update(.12);
  const next = mouth.start(1, 2);
  mouth.stop(old);
  assert.equal(mouth.active, next);
  mouth.update(1.12);
  target.realtime = .9; // Another controller has taken ownership.
  mouth.stop(next);
  assert.equal(target.realtime, .9);
  mouth.clear();
  assert.deepEqual(mouth.speech, []);
});

test("speech priority follows source playback rate and clear removes old speech windows", () => {
  const { mouth, target } = fixture();
  mouth.scheduleSpeech({ words: ["Ha"], wtimes: [0], wdurations: [500] }, 10, .5);
  mouth.start(10, 2);
  mouth.update(10.8);
  assert.equal(target.realtime, null);
  mouth.update(11.1);
  assert.notEqual(target.realtime, null);
  mouth.clear();
  mouth.start(10, 2);
  mouth.update(10.12);
  assert.notEqual(target.realtime, null);
});

test("manual clip cancellation runs its mouth cleanup", async () => {
  const { mouth, target } = fixture();
  const avatar = { animClips: [{ url: "laugh-0", clip: { duration: 2 } }],
    playAnimation: async () => {}, stopAnimation() {}, setPoseFromTemplate() {} };
  const controller = new AbortController();
  const playback = playClipAction(avatar, "laugh", controller.signal, { onStart(duration) {
    const handle = mouth.start(0, duration);
    return () => mouth.stop(handle);
  } });
  for (let index = 0; index < 8; index++) await Promise.resolve();
  mouth.update(.12);
  assert.notEqual(target.realtime, null);
  controller.abort();
  await playback;
  assert.equal(mouth.active, null);
  assert.equal(target.realtime, null);
});

test("HA mouth motion fades out and releases at 60 percent of the gesture", () => {
  const { mouth, target, apply } = fixture();
  mouth.start(10, 2);
  assert.equal(mouth.active.end, 11.2);
  apply(11.199);
  assert.ok(target.ms[0][0] < .01);
  target.newvalue = .25;
  apply(11.2);
  assert.equal(mouth.active, null);
  assert.equal(target.realtime, null);
  assert.equal(target.ms[0][0], .25); // Ordinary speech still works in the final 40 percent.
  mouth.update(11.8);
  assert.equal(target.realtime, null);
});
