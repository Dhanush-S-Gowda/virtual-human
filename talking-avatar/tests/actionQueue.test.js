import test from "node:test";
import assert from "node:assert/strict";
import { ActionQueue, waitForAction } from "../src/avatar/ActionQueue.js";

// Flush action, cleanup, and drain continuations after advancing fake time.
async function flush() {
  for (let i = 0; i < 10; i++) await Promise.resolve();
}

test("burst and later arrivals play in order, including repeated actions and return transitions", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const queue = new ActionQueue();
  const events = [];
  const action = (name) => async (signal) => {
    events.push(`start ${name}`);
    await waitForAction(3000, signal);
    events.push(`return ${name}`);
    await waitForAction(600, signal);
    events.push(`end ${name}`);
  };
  queue.enqueue(action("wave"));
  queue.enqueue(action("thumbs_up"));
  queue.enqueue(action("namaste"));
  assert.deepEqual(events, ["start wave"]);
  t.mock.timers.tick(3000);
  await flush();
  assert.deepEqual(events, ["start wave", "return wave"]);
  queue.enqueue(action("wave"));
  for (const [index, name] of ["wave", "thumbs_up", "namaste", "wave"].entries()) {
    if (index) {
      t.mock.timers.tick(3000);
      await flush();
    }
    assert.equal(events.at(-1), `return ${name}`);
    t.mock.timers.tick(599);
    await flush();
    assert.equal(events.at(-1), `return ${name}`);
    t.mock.timers.tick(1);
    await flush();
  }
  assert.deepEqual(events.filter((event) => event.startsWith("start")), [
    "start wave", "start thumbs_up", "start namaste", "start wave",
  ]);
  assert.equal(queue.busy, false);
});

test("a failed action does not discard the remaining actions", async () => {
  const errors = [];
  const events = [];
  const queue = new ActionQueue((error) => errors.push(error.message));
  queue.enqueue(async () => { throw new Error("avatar unavailable"); });
  queue.enqueue(async () => { events.push("nod"); });
  await flush();
  assert.deepEqual(errors, ["avatar unavailable"]);
  assert.deepEqual(events, ["nod"]);
  assert.equal(queue.busy, false);
});

test("Stop cancels the active timer and drops pending gestures; a new sequence can start", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const events = [];
  const queue = new ActionQueue();
  queue.enqueue(async (signal) => {
    events.push("wave");
    const completed = await waitForAction(2800, signal);
    events.push(completed ? "completed" : "cancelled");
  });
  queue.enqueue(async () => { events.push("discarded namaste"); });
  queue.clear();
  // Enqueue before the cancelled action has finished its cleanup.
  queue.enqueue(async () => { events.push("new nod"); });
  await flush();
  assert.deepEqual(events, ["wave", "cancelled", "new nod"]);
  assert.equal(queue.busy, false);
  t.mock.timers.tick(10000);
  await flush();
  assert.deepEqual(events, ["wave", "cancelled", "new nod"]);
});

test("cancellation during the return transition prevents the next gesture", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const queue = new ActionQueue();
  const events = [];
  queue.enqueue(async (signal) => {
    await waitForAction(3000, signal);
    events.push("return");
    await waitForAction(600, signal);
  });
  queue.enqueue(async () => { events.push("next gesture"); });
  t.mock.timers.tick(3000);
  await flush();
  queue.clear();
  await flush();
  t.mock.timers.tick(10000);
  await flush();
  assert.deepEqual(events, ["return"]);
  assert.equal(queue.busy, false);
});

test("waiting with an already cancelled signal finishes immediately", async () => {
  const controller = new AbortController();
  controller.abort();
  assert.equal(await waitForAction(3000, controller.signal), false);
});

test("non-talking tags interrupt talking and all foreground gestures are preserved in order", async () => {
  const queue = new ActionQueue();
  const calls = [];
  queue.enqueue(async (signal) => {
    calls.push("talking");
    await waitForAction(10000, signal);
    calls.push("talking stopped");
  }, { kind: "talking" });
  queue.enqueue(async () => { calls.push("optional talking"); }, { kind: "talking" });
  queue.enqueue(async () => { calls.push("laughing"); }, { kind: "tagged" });
  queue.enqueue(async () => { calls.push("clapping"); }, { kind: "tagged" });
  for (let i = 0; i < 30; i++) await Promise.resolve();
  assert.deepEqual(calls, ["talking", "talking stopped", "laughing", "clapping", "optional talking"]);
  assert.equal(queue.busy, false);
});
