/** Emit one named event per gesture, with a short gap allowance for inference. */
export class GestureEvents {
  constructor(gapMs = 800) {
    this.gapMs = gapMs;
    this.active = new Map();
  }

  update(people, nowMs) {
    const events = [];
    for (const person of people) {
      for (const action of new Set(person.actions || [])) {
        const key = `${person.track_id}:${action}`;
        if (!this.active.has(key) || nowMs - this.active.get(key) > this.gapMs) {
          events.push({ event_type: "action_detected", action,
            track_id: person.track_id, display_name: person.display_name || "Unknown" });
        }
        this.active.set(key, nowMs);
      }
    }
    for (const [key, lastSeen] of this.active) {
      if (nowMs - lastSeen > this.gapMs) this.active.delete(key);
    }
    return events;
  }
}
