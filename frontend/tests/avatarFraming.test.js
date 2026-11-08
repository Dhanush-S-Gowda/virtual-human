import test from "node:test";
import assert from "node:assert/strict";
import { avatarFraming } from "../src/ui/avatarFraming.js";

test("meeting view keeps headroom and crops the lower body on different stage sizes", () => {
  for (const [width, height] of [[1064, 792], [412, 780], [304, 260]]) {
    const avatarHeight = 1.7;
    const framing = avatarFraming(avatarHeight, width, height, 10, 1.3915);
    const visibleHeight = 2 * (4.5 + framing.cameraDistance) * Math.tan(5 * Math.PI / 180);
    assert.ok(Math.abs((avatarHeight * .58) / visibleHeight - 1.3915) < .0001);
    const targetY = 2 * avatarHeight / 3 + (1 - framing.cameraY) * visibleHeight / 2;
    const headY = .5 - (avatarHeight - targetY) / visibleHeight;
    const bottomWorldY = targetY - visibleHeight / 2;
    assert.ok(Math.abs(headY - .15) < .0001);
    assert.ok(bottomWorldY > avatarHeight * .64 && bottomWorldY < avatarHeight * .65);
    assert.ok(.5 + targetY / visibleHeight > 1); // Feet stay outside the view.
  }
});
