import test from "node:test";
import assert from "node:assert/strict";
import { conversationSessionId } from "../src/stt/session.js";

function storage() {
  const values = new Map();
  return { getItem: (key) => values.get(key), setItem: (key, value) => values.set(key, value) };
}

test("a reload reuses the same tab session", () => {
  const tab = storage();
  assert.equal(conversationSessionId(tab, () => "first"), "first");
  assert.equal(conversationSessionId(tab, () => "unneeded"), "first");
});
test("separate tabs have separate sessions", () => {
  assert.equal(conversationSessionId(storage(), () => "a"), "a");
  assert.equal(conversationSessionId(storage(), () => "b"), "b");
});
test("blocked storage falls back to an ephemeral ID", () => {
  const blocked = { getItem() { throw new Error("blocked"); } };
  assert.equal(conversationSessionId(blocked, () => "fallback"), "fallback");
});
