import { chooseClip } from "./clipActions.js";

/** Low-priority hand motion: random variants while audio plays, preemptible by tags. */
export class TalkingGestures {
  constructor(play, isBlocked, random = Math.random) {
    this.play = play;
    this.isBlocked = isBlocked;
    this.random = random;
    this.end = -Infinity;
    this.active = null;
    this.controller = null;
    this.previous = null;
  }

  until(end) { this.end = end; }

  update(time) {
    if (time >= this.end || this.isBlocked()) {
      void this.interrupt();
      return;
    }
    if (this.active) return;
    const url = chooseClip("talking", this.random, this.previous);
    if (!url) return;
    this.previous = url;
    this.controller = new AbortController();
    const signal = this.controller.signal;
    this.active = Promise.resolve().then(() => this.play(url, signal)).catch((error) => {
      console.warn("Talking animation failed:", error);
      this.end = -Infinity;
    }).finally(() => { this.active = null; this.controller = null; });
  }

  interrupt() {
    this.controller?.abort();
    return this.active || Promise.resolve();
  }

  clear() { this.end = -Infinity; return this.interrupt(); }
}
