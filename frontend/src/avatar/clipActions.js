import catalog from "../../../TalkingHead/animation_catalog.json" with { type: "json" };

/** One source for tags, buttons, deployed files and random clip variants. */
export const CLIP_ACTIONS = Object.fromEntries(catalog.map(({ tag, label, files }) => [tag, {
  label, files, urls: files.map((file) => `/animations/${file}`), buttonId: `${tag}Button`,
}]));

export function chooseClip(action, random = Math.random, previous = null) {
  const urls = CLIP_ACTIONS[action]?.urls;
  if (!urls?.length) return null;
  const candidates = urls.length > 1 ? urls.filter((url) => url !== previous) : urls;
  return candidates[Math.min(candidates.length - 1, Math.floor(random() * candidates.length))];
}

export function mountAnimationControls(container, play) {
  if (!container) return;
  const buttons = Object.entries(CLIP_ACTIONS).map(([action, { label, buttonId }]) => {
    const button = container.ownerDocument.createElement("button");
    button.id = buttonId;
    button.type = "button";
    button.textContent = label;
    button.title = label;
    button.setAttribute("aria-label", label);
    button.addEventListener("click", () => play(action));
    return button;
  });
  container.replaceChildren(...buttons);
}
