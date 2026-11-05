import test from "node:test";
import assert from "node:assert/strict";
import { decodeSpeechAudio } from "../src/avatar/speechAudio.js";

const response = { audio_base64: btoa("wave bytes"), words: ["Hello"], wtimes: [0], wdurations: [100] };

test("audio decoding does not wait for animation loading", async () => {
  const audio = { duration: 1 };
  const context = {
    state: "running",
    decodeAudioData(buffer) {
      assert.equal(new TextDecoder().decode(buffer), "wave bytes");
      return Promise.resolve(audio);
    },
    // Animation preparation must never be part of audio decoding.
    playAnimation() { return new Promise(() => {}); },
  };
  assert.equal(await decodeSpeechAudio(context, response), audio);
});

for (const state of ["suspended", "interrupted"]) {
  test(`audio resumes a ${state} context before decoding`, async () => {
    const calls = [];
    const context = { state, resume: async () => calls.push("resume"),
      decodeAudioData: async () => { calls.push("decode"); return {}; } };
    await decodeSpeechAudio(context, response);
    assert.deepEqual(calls, ["resume", "decode"]);
  });
}

test("invalid speech data fails before invoking audio decoding", async () => {
  const context = { decodeAudioData() { assert.fail("Invalid response reached decoder"); } };
  await assert.rejects(decodeSpeechAudio(context, {}), /audio_base64/);
  await assert.rejects(decodeSpeechAudio(context, { audio_base64: "YQ==" }), /timing/);
});
