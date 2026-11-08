import test from "node:test";
import assert from "node:assert/strict";
import { accessSync } from "node:fs";
import { CallPreferences, AVATARS, avatarOptions, DEFAULT_ZOOM_PERCENT } from "../src/ui/callSettings.js";

function storage(initial = null) {
  let value = initial;
  return { getItem: () => value, setItem: (key, next) => { value = next; } };
}

test("all six local avatars have matching assets and the author's skeleton presets", () => {
  assert.equal(AVATARS.length, 6);
  for (const { id } of AVATARS) {
    const options = avatarOptions(id);
    accessSync(new URL(`../../TalkingHead/avatars/${id}.glb`, import.meta.url));
    accessSync(new URL(`../public${options.url}`, import.meta.url));
  }
  assert.equal(avatarOptions("avatarsdk").body, "M");
  assert.ok(avatarOptions("avaturn").retarget.Hips);
  const options = avatarOptions("avaturn");
  options.retarget.Hips.y = 999;
  assert.notEqual(avatarOptions("avaturn").retarget.Hips.y, 999);
  assert.throws(() => avatarOptions("../../outside"));
});

test("avatar and voice choices persist independently for each provider", () => {
  const saved = storage();
  const preferences = new CallPreferences(saved);
  preferences.setProvider("kokoro");
  assert.equal(preferences.voice, "af_heart");
  preferences.save("vroid", "am_puck");
  preferences.setProvider("supertonic");
  assert.equal(preferences.voice, "F1");
  preferences.save("vroid", "M1");
  const reloaded = new CallPreferences(saved);
  assert.equal(reloaded.avatar, "vroid");
  reloaded.setProvider("kokoro");
  assert.equal(reloaded.voice, "am_puck");
  reloaded.setProvider("supertonic");
  assert.equal(reloaded.voice, "M1");
  assert.throws(() => reloaded.save("vroid", "af_heart"));
});

test("default zoom is 15% closer than the previous view and custom zoom persists", () => {
  const saved = storage();
  const preferences = new CallPreferences(saved);
  assert.equal(preferences.zoomPercent, DEFAULT_ZOOM_PERCENT);
  assert.equal(preferences.zoomFactor, 1.3915);
  preferences.save("brunette", undefined, null, 160);
  const restored = new CallPreferences(saved);
  assert.equal(restored.zoomPercent, 160);
  assert.equal(restored.zoomFactor, 1.6);
  assert.throws(() => restored.save("brunette", undefined, null, 201));
});

test("corrupt or blocked storage cannot select arbitrary assets or invalid voices", () => {
  for (const initial of ["bad JSON", "null", '{"avatar":"../../outside","voices":{"kokoro":"M1"}}']) {
    const preferences = new CallPreferences(storage(initial));
    assert.equal(preferences.avatar, "brunette");
    preferences.setProvider("kokoro");
    assert.equal(preferences.voice, "af_heart");
  }
  const blocked = new CallPreferences({ getItem() { throw new Error("blocked"); } });
  blocked.setProvider("supertonic");
  blocked.save("brunette-t", "M1");
  assert.equal(blocked.avatar, "brunette-t");
  assert.equal(blocked.voice, "M1");
});

test("saving repairs corrupt storage and retains a voice if provider info changes during loading", () => {
  const saved = storage("broken");
  const preferences = new CallPreferences(saved);
  preferences.setProvider("kokoro");
  preferences.setProvider("supertonic");
  preferences.save("avatarsdk", "am_puck", "kokoro");
  const restored = new CallPreferences(saved);
  assert.equal(restored.avatar, "avatarsdk");
  restored.setProvider("kokoro");
  assert.equal(restored.voice, "am_puck");
});
