/** Built-in TalkingHead moods, including neutral to reset a manual preview. */
export const MOOD_PREVIEWS = {
  neutral: "Neutral",
  happy: "Happy",
  sad: "Sad",
  angry: "Angry",
  fear: "Fear",
  disgust: "Disgust",
  love: "Love",
  sleep: "Sleep",
};

/** Keep the happy preview readable without an exaggerated grin. */
export function configureMoodPreviews(head) {
  const happy = head.animMoods.happy;
  head.animMoods.happy = {
    ...happy,
    baseline: {
      ...happy.baseline,
      mouthSmile: 0.55,
      eyeSquintLeft: 0.18,
      eyeSquintRight: 0.18,
      browInnerUp: 0.12,
      browOuterUpLeft: 0.18,
      browOuterUpRight: 0.18,
    },
  };
}

export function mountMoodControls(container, preview) {
  if (!container) return;
  const buttons = Object.entries(MOOD_PREVIEWS).map(([mood, label]) => {
    const button = container.ownerDocument.createElement("button");
    button.type = "button";
    button.id = `${mood}MoodButton`;
    button.textContent = label;
    button.title = `Preview ${label.toLowerCase()} expression`;
    button.setAttribute("aria-label", button.title);
    button.addEventListener("click", () => preview(mood));
    return button;
  });
  container.replaceChildren(...buttons);
}
