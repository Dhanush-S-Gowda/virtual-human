import test from "node:test";
import assert from "node:assert/strict";
import { TalkingGestures } from "../src/avatar/TalkingGestures.js";
import { waitForAction } from "../src/avatar/ActionQueue.js";
import { TalkingHead } from "../src/talkinghead/talkinghead.mjs";
import { AnimationClip, Object3D, Quaternion, QuaternionKeyframeTrack } from "three";

async function flush() { for (let i = 0; i < 12; i++) await Promise.resolve(); }

test("talking variants rotate during audio and yield immediately to tagged actions", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const calls = [];
  let blocked = false;
  const talking = new TalkingGestures(async (url, signal) => {
    calls.push(url);
    await waitForAction(1000, signal);
  }, () => blocked, () => 0);
  talking.until(10);
  talking.update(0);
  await flush();
  t.mock.timers.tick(1000);
  await flush();
  talking.update(1);
  await flush();
  assert.equal(calls.length, 2);
  assert.notEqual(calls[0], calls[1]);
  blocked = true;
  await talking.interrupt();
  talking.update(2);
  await flush();
  assert.equal(calls.length, 2);
  blocked = false;
  talking.update(3);
  await flush();
  assert.equal(calls.length, 3);
  talking.update(10);
  await flush();
  assert.equal(talking.active, null);
});

test("Stop prevents talking clips from resuming", async () => {
  const talking = new TalkingGestures(async (_, signal) => waitForAction(10000, signal), () => false);
  talking.until(20);
  talking.update(0);
  await flush();
  await talking.clear();
  talking.update(1);
  assert.equal(talking.active, null);
});

test("hand-only FBX playback leaves the head and body animation channels alone", async () => {
  const armature = new Object3D();
  const arm = new Object3D(); arm.name = "LeftArm";
  const head = new Object3D(); head.name = "Head";
  armature.add(arm, head);
  const track = (name) => new QuaternionKeyframeTrack(`${name}.quaternion`, [0, 1], [0, 0, 0, 1, 0.3, 0, 0, 0.953939]);
  const avatar = Object.assign(Object.create(TalkingHead.prototype), {
    armature, animQueue: [], poseBase: { props: {} }, poseTarget: { props: {} },
    animClips: [{ url: "test-0", pose: { props: { "LeftArm.quaternion": new Quaternion(), "Head.quaternion": new Quaternion() } },
      clip: new AnimationClip("talking", 1, [track("LeftArm"), track("Head")]) }],
  });
  await avatar.playAnimation("test", null, 0.01, 0, 0.01, null, false, true);
  avatar.mixer.update(0.5);
  assert.notEqual(arm.quaternion.x, 0);
  assert.equal(head.quaternion.x, 0);
  assert.equal(avatar.poseTarget.props["Head.quaternion"], undefined);
  avatar.mixer.stopAllAction();
});
