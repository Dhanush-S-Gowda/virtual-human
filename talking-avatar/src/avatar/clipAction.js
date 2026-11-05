import { waitForAction } from "./ActionQueue.js";

/** Play one FBX cycle and wait through its actual duration and pose return. */
export async function playClipAction(avatar, url, signal, { armsOnly = false } = {}) {
  let release;
  const cancelled = new Promise((resolve) => { release = resolve; });
  const cancel = () => {
    avatar.stopAnimation();
    release();
  };
  signal.addEventListener("abort", cancel, { once: true });
  try {
    if (signal.aborted) return;
    await Promise.race([avatar.playAnimation(url, null, 0.01, 0, 0.01, signal, false, armsOnly), cancelled]);
    if (signal.aborted) return;
    const clip = avatar.animClips.find((item) => item.url === `${url}-0`)?.clip;
    if (!clip || !Number.isFinite(clip.duration) || clip.duration <= 0) {
      throw new Error("Animation has no playable clip.");
    }
    await waitForAction(clip.duration * 1000, signal);
  } finally {
    signal.removeEventListener("abort", cancel);
    avatar.stopAnimation();
    if (!signal.aborted) {
      avatar.setPoseFromTemplate(avatar.poseCurrentTemplate, 600);
      await waitForAction(600, signal);
    }
  }
}
