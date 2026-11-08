import test from "node:test";
import assert from "node:assert/strict";
import { conversationSessionId, persistentConversationId } from "../src/stt/session.js";

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

test("memory identity survives a new tab and browser session", () => {
  const profile = storage();
  assert.equal(persistentConversationId(profile, storage(), () => "saved"), "saved");
  assert.equal(persistentConversationId(profile, storage(), () => "new"), "saved");
  assert.equal(persistentConversationId(storage(), storage(), () => "other"), "other");
});

test("existing tab identity migrates and blocked local storage uses the tab", () => {
  const tab = storage();
  conversationSessionId(tab, () => "existing");
  assert.equal(persistentConversationId(storage(), tab), "existing");
  const blocked = { getItem() { throw new Error("blocked"); } };
  assert.equal(persistentConversationId(blocked, tab), "existing");
});
