/** Keep a user-sized camera tile inside the call and leave conversation space. */
export function boundedCameraSize(width, height, viewportWidth, viewportHeight) {
  const mobile = viewportWidth < 768;
  const maxWidth = mobile ? (viewportWidth - 24) * .65 : Math.min(600, viewportWidth * .48);
  const minWidth = Math.min(mobile ? 100 : 280, maxWidth);
  const maxHeight = mobile ? viewportHeight * .24 : Math.max(120, Math.min(viewportHeight * .56, viewportHeight - 350));
  const minHeight = Math.min(mobile ? 80 : 180, maxHeight);
  return {
    width: Math.round(Math.max(minWidth, Math.min(maxWidth, width))),
    height: Math.round(Math.max(minHeight, Math.min(maxHeight, height))),
  };
}

export function setupCameraResize(app, handle, camera, sidebar) {
  let drag = null;
  const apply = (width, height) => {
    const size = boundedCameraSize(width, height, window.innerWidth, window.innerHeight);
    app.style.setProperty(window.innerWidth < 768 ? "--mobile-camera-width" : "--side-width", `${size.width}px`);
    app.style.setProperty("--camera-height", `${size.height}px`);
    if (window.innerWidth < 768) app.style.setProperty("--mobile-side-height", `${size.height + 80}px`);
  };
  const currentWidth = () => (window.innerWidth < 768 ? camera : sidebar).getBoundingClientRect().width;
  const end = (event) => {
    if (drag?.id !== event.pointerId) return;
    drag = null;
    if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
  };
  handle.addEventListener("pointerdown", (event) => {
    if (!event.isPrimary || event.button !== 0) return;
    event.preventDefault();
    drag = { id: event.pointerId, x: event.clientX, y: event.clientY,
      width: currentWidth(), height: camera.getBoundingClientRect().height };
    handle.setPointerCapture(event.pointerId);
  });
  handle.addEventListener("pointermove", (event) => {
    if (drag?.id !== event.pointerId) return;
    const delta = (event.clientX - drag.x) * (window.innerWidth < 768 ? 1 : -1);
    apply(drag.width + delta, drag.height + event.clientY - drag.y);
  });
  for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) handle.addEventListener(type, end);
  handle.addEventListener("keydown", (event) => {
    if (event.key === "Home") {
      event.preventDefault();
      for (const property of ["--side-width", "--camera-height", "--mobile-camera-width", "--mobile-side-height"]) app.style.removeProperty(property);
    } else if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
      event.preventDefault();
      const step = event.shiftKey ? 40 : 16;
      const dx = event.key === "ArrowRight" ? step : event.key === "ArrowLeft" ? -step : 0;
      const dy = event.key === "ArrowDown" ? step : event.key === "ArrowUp" ? -step : 0;
      apply(currentWidth() + dx, camera.getBoundingClientRect().height + dy);
    }
  });
  window.addEventListener("resize", () => {
    if (app.style.getPropertyValue("--camera-height")) apply(currentWidth(), camera.getBoundingClientRect().height);
  });
}
