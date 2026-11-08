import { Euler, Quaternion, Vector3 } from "three";

/** Limit the final combined pose; keep small head movements and downward nods. */
export function createHeadPitchLimiter() {
  const world = new Quaternion();
  const parent = new Quaternion();
  const rotation = new Euler(0, 0, 0, "YXZ");
  const eyes = new Vector3();
  const otherEye = new Vector3();
  const viewer = new Vector3();
  return (head) => {
    const bone = head?.objectHead;
    if (!bone?.parent || !head.camera || !head.objectLeftEye || !head.objectRightEye) return;
    head.objectLeftEye.getWorldPosition(eyes);
    head.objectRightEye.getWorldPosition(otherEye);
    eyes.add(otherEye).multiplyScalar(0.5);
    head.camera.getWorldPosition(viewer);
    const distance = Math.hypot(viewer.x - eyes.x, viewer.z - eyes.z);
    if (distance < 0.001) return;
    const viewerPitch = Math.atan2(eyes.y - viewer.y, distance);
    bone.getWorldQuaternion(world);
    rotation.setFromQuaternion(world, "YXZ");
    const pitch = Math.max(viewerPitch - 0.06, Math.min(viewerPitch + 0.22, rotation.x));
    if (Math.abs(pitch - rotation.x) < 0.00001) return;
    rotation.x = pitch;
    world.setFromEuler(rotation);
    bone.parent.getWorldQuaternion(parent);
    bone.quaternion.copy(parent.invert().multiply(world));
  };
}
