import test from "node:test";
import assert from "node:assert/strict";
import { boundedCameraSize } from "../src/ui/cameraResize.js";

test("desktop camera can grow in both directions without consuming the stage", () => {
  assert.deepEqual(boundedCameraSize(480, 380, 1440, 900), { width: 480, height: 380 });
  assert.deepEqual(boundedCameraSize(2000, 2000, 1440, 900), { width: 600, height: 504 });
});
test("small and mobile viewports bound camera size to available space", () => {
  for (const width of [320, 375, 414, 768, 1024]) {
    const size = boundedCameraSize(2000, 2000, width, 700);
    assert.ok(size.width < width);
    assert.ok(size.height < 700 / 1.5);
    const minimum = boundedCameraSize(-100, -100, width, 700);
    assert.ok(minimum.width >= 100 && minimum.height >= 80);
  }
});
