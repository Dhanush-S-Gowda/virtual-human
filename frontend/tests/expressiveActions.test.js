import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { ListeningNods } from "../src/avatar/ListeningNods.js";
import { SpeechActions } from "../src/avatar/SpeechActions.js";
import { playClipAction } from "../src/avatar/clipAction.js";
import { CLIP_ACTIONS, chooseClip, mountAnimationControls } from "../src/avatar/clipActions.js";
import { FBXLoader } from "three/addons/loaders/FBXLoader.js";

async function flush() {
  for (let i = 0; i < 12; i++) await Promise.resolve();
}

test("listening nods require recent audio, skip busy actions, and stop with capture", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let now = 0;
  let blocked = false;
  const calls = [];
  const avatar = { playGesture: (...args) => calls.push(args), stopGesture: () => calls.push("stop") };
  const nods = new ListeningNods(() => avatar, () => blocked, { random: () => 0, now: () => now });
  nods.start();
  now = 4000;
  t.mock.timers.tick(4000);
  assert.equal(calls.length, 0); // Silence is not active speech.
  now = 8000;
  nods.addAudio({ rms: 0.04, timestamp: now - 100 });
  t.mock.timers.tick(4000);
  assert.deepEqual(calls[0], ["yes", 0.7, false, 200]);
  assert.equal(nods.busy, true);
  t.mock.timers.tick(1000);
  assert.equal(nods.busy, false);
  blocked = true;
  now = 12000;
  nods.addAudio({ rms: 0.04, timestamp: now });
  t.mock.timers.tick(3000);
  assert.equal(calls.length, 2);
  nods.stop();
  t.mock.timers.tick(20000);
  assert.equal(calls.length, 2);
});

test("capture ending cancels an active listening nod immediately", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const calls = [];
  const nods = new ListeningNods(() => ({ playGesture: () => calls.push("nod"),
    stopGesture: () => calls.push("stop") }), () => false, { random: () => 0, now: () => 4000 });
  nods.start();
  nods.addAudio({ rms: 0.03, timestamp: 4000 });
  t.mock.timers.tick(4000);
  nods.stop();
  t.mock.timers.tick(20000);
  assert.deepEqual(calls, ["nod", "stop"]);
});

test("model actions wait for audio, retain order, and can be discarded before speech", () => {
  const calls = [];
  let speaking = false;
  const actions = new SpeechActions(() => speaking);
  actions.add(() => calls.push("wave"));
  actions.add(() => calls.push("clap"));
  assert.deepEqual(calls, []);
  speaking = true;
  actions.startSpeech();
  actions.add(() => calls.push("nod"));
  assert.deepEqual(calls, ["wave", "clap", "nod"]);
  speaking = false;
  actions.add(() => calls.push("discarded"));
  actions.clear();
  actions.startSpeech();
  assert.deepEqual(calls, ["wave", "clap", "nod"]);
});

function fakeClipAvatar() {
  const calls = [];
  return { calls, animClips: [{ url: "/animations/Clapping.fbx-0", clip: { duration: 1.167 } }],
    playAnimation: async (...args) => calls.push(args),
    stopAnimation: () => calls.push("stop"),
    setPoseFromTemplate: () => calls.push("return"),
  };
}

test("clapping waits for its real clip duration and the resting pose transition", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const avatar = fakeClipAvatar();
  let finished = false;
  const playback = playClipAction(avatar, "/animations/Clapping.fbx", new AbortController().signal)
    .then(() => { finished = true; });
  await flush();
  t.mock.timers.tick(1167);
  await flush();
  assert.equal(avatar.calls.at(-1), "return");
  assert.equal(finished, false);
  t.mock.timers.tick(600);
  await playback;
  assert.equal(finished, true);
});

test("Stop releases clapping even while the FBX download is pending", async () => {
  const avatar = fakeClipAvatar();
  let releaseLoad;
  avatar.playAnimation = () => new Promise((resolve) => { releaseLoad = resolve; });
  const controller = new AbortController();
  const playback = playClipAction(avatar, "/animations/Clapping.fbx", controller.signal);
  controller.abort();
  await playback;
  assert.ok(avatar.calls.includes("stop"));
  assert.ok(!avatar.calls.includes("return"));
  const count = avatar.calls.length;
  releaseLoad();
  await flush();
  assert.equal(avatar.calls.length, count);
});

for (const [action, { urls }] of Object.entries(CLIP_ACTIONS)) {
  for (const url of urls) {
    test(`${action}: ${url} is deployed and playable`, () => {
      const deployed = readFileSync(new URL(`../public${url}`, import.meta.url));
      assert.deepEqual(deployed, readFileSync(new URL(`../../TalkingHead${url}`, import.meta.url)));
      const fbx = new FBXLoader().parse(
        deployed.buffer.slice(deployed.byteOffset, deployed.byteOffset + deployed.byteLength), "");
      assert.equal(fbx.animations.length, 1);
      assert.ok(Number.isFinite(fbx.animations[0].duration) && fbx.animations[0].duration > 0);
      assert.ok(fbx.animations[0].tracks.some((track) => track.name === "mixamorigHips.position"));
    });
  }
}

test("animation controls contain only catalog actions, and talking chooses all three variants", () => {
  const buttons = [];
  const container = {
    ownerDocument: { createElement: () => ({ setAttribute() {}, addEventListener() {} }) },
    replaceChildren: (...nodes) => buttons.push(...nodes),
  };
  mountAnimationControls(container, () => {});
  assert.deepEqual(buttons.map((button) => button.id), Object.values(CLIP_ACTIONS).map((item) => item.buttonId));
  assert.equal(CLIP_ACTIONS.talking.urls.length, 3);
  assert.equal(new Set([0, 0.4, 0.9].map((value) => chooseClip("talking", () => value))).size, 3);
  assert.notEqual(chooseClip("talking", () => 0, CLIP_ACTIONS.talking.urls[0]), CLIP_ACTIONS.talking.urls[0]);
  assert.equal(chooseClip("dance"), null);
});
