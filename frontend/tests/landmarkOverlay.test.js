import test from "node:test";
import assert from "node:assert/strict";
import {
  acceptLandmarkFrame, drawLandmarkOverlay, freshLandmarkFrame, landmarkStatus,
} from "../src/cv/landmarkOverlay.js";

function frame(overrides = {}) {
  return {
    type: "frame", seq: 10, timestamp: 1, width: 640, height: 480,
    keypoints: [], completed: ["objects", "keypoints"], errors: [], ...overrides,
  };
}

function context() {
  const calls = [];
  return {
    calls,
    save() { calls.push(["save"]); },
    restore() { calls.push(["restore"]); },
    beginPath() {},
    moveTo(...args) { calls.push(["moveTo", ...args]); },
    lineTo(...args) { calls.push(["lineTo", ...args]); },
    stroke() {},
    arc(...args) { calls.push(["arc", ...args]); },
    fill() {},
  };
}

// Box frame 20 may already be displayed when landmarks for frame 10 arrive.
// Landmark ordering must only depend on previous landmark results, not boxes.
test("delayed landmark frames are accepted independently of box sequence", () => {
  const current = frame({ seq: 8 });
  const incoming = frame({ seq: 10, keypoints: [{ schema: "mediapipe_pose33", points: [] }] });
  assert.equal(acceptLandmarkFrame(current, incoming), incoming);
  assert.equal(acceptLandmarkFrame(incoming, current), incoming);
});

test("incomplete object results do not erase points, completed empty results do", () => {
  const current = frame();
  const pending = frame({ seq: 11, completed: ["objects"] });
  assert.equal(acceptLandmarkFrame(current, pending), current);
  const empty = frame({ seq: 11 });
  assert.equal(acceptLandmarkFrame(current, empty), empty);
  assert.equal(landmarkStatus(empty, 1200), "Landmarks: none detected");
});

test("older services without completion markers still provide nonempty landmarks", () => {
  const message = frame({ completed: undefined, keypoints: [{ schema: "mediapipe_pose33", points: [] }] });
  assert.equal(acceptLandmarkFrame(null, message), message);
});

test("freshness uses capture time and rejects expired or future geometry", () => {
  const message = frame();
  assert.equal(freshLandmarkFrame(message, 1500), message);
  assert.equal(freshLandmarkFrame(message, 2501), null);
  assert.equal(freshLandmarkFrame(message, 999), null);
  assert.equal(landmarkStatus(message, 2501), "Landmarks: stale");
});

test("status separates waiting, model errors and returned point counts", () => {
  assert.equal(landmarkStatus(null, 1200), "Landmarks: waiting");
  assert.equal(landmarkStatus(frame({ errors: ["keypoints: RuntimeError: failed"] }), 1200), "Landmarks: unavailable");
  const message = frame({ keypoints: [
    { schema: "mediapipe_pose33", points: Array(33).fill([1, 1, 0, 1]) },
    { schema: "mediapipe_face478", points: Array(478).fill([1, 1, 0]) },
    { schema: "mediapipe_lips", points: Array(40).fill([1, 1, 0]) },
    { schema: "mediapipe_left_hand21", points: Array(21).fill([1, 1, 0]) },
  ] });
  assert.equal(landmarkStatus(message, 1200), "Landmarks: body 33 · face 478 · lips 40 · hands 21");
});

test("pose points and bones scale and mirror in canvas coordinates", () => {
  const ctx = context();
  drawLandmarkOverlay(ctx, 320, 240, frame({ keypoints: [{
    track_id: 1, schema: "mediapipe_pose33", points: [[100, 80, 0, 1], [200, 160, 0, 1]],
  }] }), { mirrored: true, visibleTrackIds: new Set([1]) });
  assert.ok(ctx.calls.some((call) => call[0] === "moveTo" && call[1] === 270 && call[2] === 40));
  assert.ok(ctx.calls.some((call) => call[0] === "lineTo" && call[1] === 220 && call[2] === 80));
  assert.equal(ctx.calls.filter((call) => call[0] === "arc").length, 2);
  assert.deepEqual(ctx.calls.at(-1), ["restore"]);
});

test("low-confidence, nonfinite and untracked points are not drawn", () => {
  const ctx = context();
  drawLandmarkOverlay(ctx, 640, 480, frame({ keypoints: [
    { track_id: 1, schema: "mediapipe_pose33", points: [[100, 80, 0, .1], [NaN, 80, 0, 1]] },
    { track_id: 2, schema: "mediapipe_left_hand21", points: [[100, 80, 0]] },
  ] }), { visibleTrackIds: new Set([1]) });
  assert.equal(ctx.calls.filter((call) => call[0] === "arc").length, 0);
});

test("lip edges use vertex IDs, not the extraction array order", () => {
  const ctx = context();
  drawLandmarkOverlay(ctx, 640, 480, frame({ keypoints: [{
    track_id: 1, schema: "mediapipe_lips", landmark_indices: [91, 61, 146],
    points: [[300, 200, 0], [100, 200, 0], [200, 200, 0]],
  }] }));
  assert.ok(ctx.calls.some((call) => call[0] === "moveTo" && call[1] === 100));
  assert.ok(ctx.calls.some((call) => call[0] === "lineTo" && call[1] === 200));
});

test("invalid source dimensions do not generate invalid canvas calls", () => {
  const ctx = context();
  drawLandmarkOverlay(ctx, 640, 480, frame({ width: 0 }));
  assert.equal(ctx.calls.length, 0);
});
