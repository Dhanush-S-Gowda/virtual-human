import test from "node:test";
import assert from "node:assert/strict";
import { KeyboardPushToTalk } from "../src/stt/keyboardPushToTalk.js";

test("Space activates focused controls normally and records only from the call surface", () => {
  const original = globalThis.HTMLElement;
  class Element {
    constructor(tagName) { this.tagName = tagName; }
    getAttribute() { return null; }
  }
  globalThis.HTMLElement = Element;
  try {
    let starts = 0, stops = 0, prevented = 0;
    const keyboard = new KeyboardPushToTalk({ onStart: () => starts++, onStop: () => stops++ });
    keyboard.isEnabled = true;
    const event = (tag) => ({ code: "Space", key: " ", target: new Element(tag), preventDefault: () => prevented++ });
    for (const tag of ["BUTTON", "SUMMARY", "INPUT", "A"]) keyboard._handleKeyDown(event(tag));
    assert.equal(starts, 0);
    assert.equal(prevented, 0);
    keyboard._handleKeyDown(event("BODY"));
    keyboard._handleKeyDown({ ...event("BODY"), repeat: true });
    keyboard._handleKeyUp(event("BODY"));
    assert.equal(starts, 1);
    assert.equal(stops, 1);
  } finally {
    if (original === undefined) delete globalThis.HTMLElement;
    else globalThis.HTMLElement = original;
  }
});

test("an open settings dialog suppresses global push-to-talk", () => {
  const original = globalThis.document;
  globalThis.document = { querySelector: () => ({ open: true }) };
  try {
    let starts = 0;
    const keyboard = new KeyboardPushToTalk({ onStart: () => starts++ });
    keyboard.isEnabled = true;
    keyboard._handleKeyDown({ code: "Space", key: " ", target: null });
    assert.equal(starts, 0);
  } finally {
    if (original === undefined) delete globalThis.document;
    else globalThis.document = original;
  }
});
