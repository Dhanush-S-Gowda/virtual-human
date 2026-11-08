import test from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import { AudioGesturePlayer } from "../src/avatar/AudioGesturePlayer.js";

async function player() {
  const avatar = { armature: new THREE.Object3D(), playAnimation: async (url) => ({ clip:
    new THREE.AnimationClip(url, 2.4, [new THREE.NumberKeyframeTrack(".position[x]", [0, 2.4], [0, 1])]) }) };
  const result = new AudioGesturePlayer(() => avatar);
  await result.prepare([{ action: "clapping" }, { action: "laughing" }]);
  return result;
}

test("a full clip plays naturally instead of compressing to a word interval", async () => {
  const p = await player();
  p.play("clapping", { time: 10, duration: .8, audioTime: 10 });
  assert.ok(Math.abs(p.active.action.getEffectiveTimeScale() - 1) < 1e-9);
  p.update(11.2);
  assert.ok(Math.abs(p.active.action.time - 1.2) < 1e-9);
  p.update(11.2); // A suspended audio clock cannot advance the animation.
  assert.ok(Math.abs(p.active.action.time - 1.2) < 1e-9);
  p.update(12.4);
  assert.equal(p.active, null);
  p.update(12.51);
  assert.equal(p.busy, false);
});

test("new gestures interrupt with a 100ms cross-fade on the same mixer", async () => {
  const p = await player();
  p.play("clapping", { time: 0, duration: 1, audioTime: 0 });
  p.update(.2);
  const old = p.active.action;
  const mixer = p.mixer;
  p.play("laughing", { time: .2, duration: .8, audioTime: .2 });
  assert.equal(p.mixer, mixer);
  assert.notEqual(p.active.action, old);
  p.update(.25);
  assert.ok(Math.abs(old.getEffectiveWeight() - .5) < .001);
  assert.ok(Math.abs(p.active.action.getEffectiveWeight() - .5) < .001);
  p.update(.31);
  assert.equal(old.isScheduled(), false);
  assert.equal(p.active.action.isScheduled(), true);
  p.clear();
  assert.equal(p.busy, false);
  assert.equal(p.mixer, null);
});

test("late dispatch catches up and expired gestures are ignored", async () => {
  const p = await player();
  p.play("clapping", { time: 10, duration: .8, audioTime: 10.2 });
  assert.ok(Math.abs(p.active.action.time - .2) < 1e-9);
  const current = p.active;
  p.play("laughing", { time: 6, duration: .5, audioTime: 10.2 });
  assert.equal(p.active, current);
  p.clear();
});

test("configured minimum changes gesture playback without speeding up longer clips", async () => {
  const p = await player();
  p.minimumSeconds = 4;
  assert.equal(p.durationFor("clapping"), 4);
  p.play("clapping", { time: 0, duration: .1, audioTime: 0 });
  assert.ok(Math.abs(p.active.action.getEffectiveTimeScale() - .6) < 1e-9);
  p.clear();
  p.minimumSeconds = 1;
  assert.equal(p.durationFor("clapping"), 2.4);
});


test("idle preload and arriving speech share one clip download", async () => {
  let finish;
  let calls = 0;
  const pending = new Promise(resolve => { finish = resolve; });
  const avatar = { armature: new THREE.Object3D(), playAnimation: async () => {
    calls++;
    await pending;
    return { clip: new THREE.AnimationClip("greeting", 3, []) };
  } };
  const p = new AudioGesturePlayer(() => avatar);
  const warm = p.prepare([{ action: "greeting" }]);
  const speech = p.prepare([{ action: "greeting" }]);
  finish();
  await Promise.all([warm, speech]);
  assert.equal(calls, 1);
  assert.equal(p.durationFor("greeting"), 3);
});
