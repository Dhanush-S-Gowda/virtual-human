/** Meeting framing: headroom above the face, with the lower body cropped out. */
export function avatarFraming(avatarHeight, width, height, fov = 10, zoomFactor = 1.3915) {
  const visibleHeight = avatarHeight * .58 / zoomFactor;
  const distance = visibleHeight / (2 * Math.tan(fov * Math.PI / 360));
  const target = avatarHeight - .35 * visibleHeight; // Head starts 15% down the stage.
  // TalkingHead's upper view adds 4.5 to distance and 2/3 height to the target.
  return { cameraDistance: distance - 4.5, cameraX: 0,
    cameraY: 1 - (target - 2 * avatarHeight / 3) / (visibleHeight / 2) };
}

export function setupAvatarFraming(head, element, getZoom = () => 1.3915) {
  const fit = () => {
    if (!element.clientWidth || !element.clientHeight) return;
    head.setView("upper", avatarFraming(head.avatarHeight, element.clientWidth, element.clientHeight, head.camera.fov, getZoom()));
  };
  fit();
  const observer = new ResizeObserver(fit);
  observer.observe(element);
  window.addEventListener("pagehide", () => observer.disconnect(), { once: true });
  return fit;
}
