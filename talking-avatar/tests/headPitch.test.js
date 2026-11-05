import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { AnimationMixer, Euler, Object3D, PerspectiveCamera, Quaternion, Vector3 } from "three";
import { FBXLoader } from "three/addons/loaders/FBXLoader.js";
import { createHeadPitchLimiter } from "../src/avatar/headPitch.js";

function fixture(pitch, parentRotation = [0, 0, 0]) {
  const parent = new Object3D();
  parent.rotation.set(...parentRotation);
  const bone = new Object3D();
  parent.add(bone);
  const desired = new Quaternion().setFromEuler(new Euler(pitch, 0.2, 0.1, "YXZ"));
  bone.quaternion.copy(parent.getWorldQuaternion(new Quaternion()).invert().multiply(desired));
  const left = new Object3D();
  const right = new Object3D();
  left.position.x = -0.03;
  right.position.x = 0.03;
  bone.add(left, right);
  const camera = new PerspectiveCamera();
  camera.position.set(0, 0, 4);
  return { objectHead: bone, objectLeftEye: left, objectRightEye: right, camera };
}

function angles(head) {
  return new Euler().setFromQuaternion(head.objectHead.getWorldQuaternion(new Quaternion()), "YXZ");
}

test("raised idle head pitch is limited while yaw and roll remain", () => {
  const head = fixture(-0.45);
  createHeadPitchLimiter()(head);
  const rotation = angles(head);
  assert.ok(Math.abs(rotation.x + 0.06) < 1e-6);
  assert.ok(Math.abs(rotation.y - 0.2) < 1e-6);
  assert.ok(Math.abs(rotation.z - 0.1) < 1e-6);
});

test("natural head motion and nodding within the bounds are unchanged", () => {
  const limit = createHeadPitchLimiter();
  for (const pitch of [-0.04, 0, 0.1, 0.2]) {
    const head = fixture(pitch);
    const before = head.objectHead.quaternion.clone();
    limit(head);
    assert.deepEqual(head.objectHead.quaternion.toArray(), before.toArray());
  }
});

test("pitch correction accounts for rotated parent bones and camera height", () => {
  const head = fixture(-0.6, [0.3, -0.25, 0.15]);
  head.camera.position.y = 0.4;
  const parentBefore = head.objectHead.parent.quaternion.clone();
  createHeadPitchLimiter()(head);
  const expected = Math.atan2(-0.4, 4) - 0.06;
  assert.ok(Math.abs(angles(head).x - expected) < 1e-6);
  assert.deepEqual(head.objectHead.parent.quaternion.toArray(), parentBefore.toArray());
});

test("the actual clapping pose is corrected after mixer playback without changing arm motion", () => {
  const bytes = readFileSync(new URL("../public/animations/Clapping.fbx", import.meta.url));
  const scene = new FBXLoader().parse(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength), "");
  const mixer = new AnimationMixer(scene);
  mixer.clipAction(scene.animations[0]).play();
  mixer.update(0.5);
  const bone = scene.getObjectByName("mixamorigHead");
  const arm = scene.getObjectByName("mixamorigRightArm");
  const armBefore = arm.quaternion.clone();
  const left = new Object3D();
  const right = new Object3D();
  bone.add(left, right);
  const camera = new PerspectiveCamera();
  camera.position.copy(bone.getWorldPosition(new Vector3())).add(new Vector3(0, 0, 4));
  const head = { objectHead: bone, objectLeftEye: left, objectRightEye: right, camera };
  assert.ok(angles(head).x < -0.3);
  createHeadPitchLimiter()(head);
  assert.ok(Math.abs(angles(head).x + 0.06) < 1e-6);
  assert.deepEqual(arm.quaternion.toArray(), armBefore.toArray());
});

test("incomplete or unloaded avatars are safe to update", () => {
  const limit = createHeadPitchLimiter();
  assert.doesNotThrow(() => { limit(null); limit({}); });
});
