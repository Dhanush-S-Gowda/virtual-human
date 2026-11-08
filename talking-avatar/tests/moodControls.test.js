import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { MOOD_PREVIEWS, configureMoodPreviews, mountMoodControls } from "../src/avatar/moodControls.js";

test("manual mood controls expose every built-in mood and invoke the selected preview", () => {
  const source = readFileSync(new URL("../src/talkinghead/talkinghead.mjs", import.meta.url), "utf8");
  const moods = source.split("this.animMoods = {")[1].split("this.moodName =")[0];
  const supported = [...moods.matchAll(/^      '([a-z]+)'\s*:/gm)].map((match) => match[1]);
  assert.deepEqual(Object.keys(MOOD_PREVIEWS).sort(), supported.sort());
  const calls = [];
  const container = {
    ownerDocument: { createElement: () => ({
      attributes: {},
      setAttribute(name, value) { this.attributes[name] = value; },
      addEventListener(type, callback) { this[type] = callback; },
    }) },
    replaceChildren(...buttons) { this.buttons = buttons; },
  };
  mountMoodControls(container, (mood) => calls.push(mood));
  for (const [index, [mood, label]] of Object.entries(MOOD_PREVIEWS).entries()) {
    const button = container.buttons[index];
    assert.equal(button.type, "button");
    assert.equal(button.textContent, label);
    assert.equal(button.id, `${mood}MoodButton`);
    assert.equal(button.attributes["aria-label"], `Preview ${label.toLowerCase()} expression`);
    button.click();
    assert.equal(calls.at(-1), mood);
  }
});

test("absent emotion panel is safe to skip", () => {
  assert.doesNotThrow(() => mountMoodControls(null, () => assert.fail("No click expected")));
});


test("happy preview strengthens the face while preserving animations and other moods", () => {
  const original = { baseline: { mouthSmile: 0.2, eyesLookDown: 0.1 },
    anims: [{ name: "blink" }], speech: { deltaPitch: 0.1 } };
  const neutral = { baseline: {} };
  const head = { animMoods: { happy: original, neutral } };
  configureMoodPreviews(head);
  const happy = head.animMoods.happy;
  assert.ok(happy.baseline.mouthSmile > original.baseline.mouthSmile);
  assert.ok(happy.baseline.eyeSquintLeft > 0);
  assert.equal(happy.baseline.eyeSquintLeft, happy.baseline.eyeSquintRight);
  assert.ok(happy.baseline.browInnerUp > 0);
  assert.ok(happy.baseline.browOuterUpLeft > 0);
  assert.equal(happy.baseline.browOuterUpLeft, happy.baseline.browOuterUpRight);
  assert.equal(happy.baseline.eyesLookDown, original.baseline.eyesLookDown);
  assert.equal(happy.anims, original.anims);
  assert.equal(happy.speech, original.speech);
  assert.equal(head.animMoods.neutral, neutral);
  assert.equal(original.baseline.mouthSmile, 0.2);
  configureMoodPreviews(head);
  assert.deepEqual(head.animMoods.happy, happy); // Reconfiguration does not compound intensity.
});
