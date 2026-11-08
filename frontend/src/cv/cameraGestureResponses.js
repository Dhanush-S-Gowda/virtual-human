/** React once to confirmed camera gestures using only the available animation catalog. */
const RESPONSES = { wave: "greeting", thumbs_up: "enthusiasm", open_palm: "nod_yes" };

export class CameraGestureResponses {
  constructor(dispatch, isBlocked, now = () => performance.now()) {
    this.dispatch = dispatch;
    this.isBlocked = isBlocked;
    this.now = now;
    this.recent = new Map();
    this.pending = [];
  }

  handle(event) {
    if (!Object.hasOwn(RESPONSES, event.action)) return;
    const time = this.now();
    const key = `${event.source_id || "camera"}:${event.track_id}:${event.action}`;
    if (time - (this.recent.get(key) ?? -Infinity) < 1500) return;
    for (const [old, seen] of this.recent) if (time - seen > 10000) this.recent.delete(old);
    this.recent.set(key, time);
    if (this.recent.size > 64) this.recent.delete(this.recent.keys().next().value);
    this.pending = this.pending.filter((item) => item.key !== key);
    if (this.pending.length === 4) this.pending.shift();
    this.pending.push({ key, action: RESPONSES[event.action], time });
    this.update();
  }

  update() {
    this.pending = this.pending.filter((item) => this.now() - item.time <= 5000);
    if (!this.pending.length || this.isBlocked()) return;
    this.dispatch(this.pending.shift().action);
  }

  clear() { this.pending.length = 0; this.recent.clear(); }
}
