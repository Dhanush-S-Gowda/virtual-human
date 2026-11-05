/** One utterance per push-to-talk hold. Lip motion only helps identify its speaker. */
export class SpeakingEvents {
  constructor({ minRms = 0.015, minVariance = 0.0004 } = {}) {
    this.minRms = minRms;
    this.minVariance = minVariance;
    this.holding = false;
    this.speaker = null;
    this.lastSpeaker = null;
    this.audio = [];
    this.lastTimestamp = -Infinity;
  }

  start(people) {
    if (this.holding) return [];
    this.holding = true;
    this.speaker = people.length === 1 ? { ...people[0], method: "single_visible" } : null;
    this.lastSpeaker = null;
    this.audio = [];
    this.lastTimestamp = -Infinity;
    return [this.event("start")];
  }

  addAudio(sample) {
    if (!this.holding || !Number.isFinite(sample.timestamp) || !Number.isFinite(sample.rms)) return;
    this.audio.push(sample);
    this.audio = this.audio.filter((item) => item.timestamp >= sample.timestamp - 4000);
  }

  update(frame, people, nowMs) {
    if (!this.holding) return [];
    if (this.speaker) {
      const current = people.find((person) => person.track_id === this.speaker.track_id
        && person.generation === this.speaker.generation && person.source_id === this.speaker.source_id);
      if (current) this.speaker = { ...this.speaker, ...current };
      return [];
    }
    if (people.length === 1) {
      this.speaker = { ...people[0], method: "single_visible" };
      return [];
    }
    const timestamp = frame?.timestamp * 1000;
    if (!Number.isFinite(timestamp) || timestamp <= this.lastTimestamp
        || nowMs - timestamp > 1500 || timestamp > nowMs) return [];
    this.lastTimestamp = timestamp;
    if (!this.audio.some((sample) => Math.abs(sample.timestamp - timestamp) <= 150
        && sample.rms >= this.minRms)) return [];
    const candidates = (frame.keypoints || [])
      .filter((point) => point.schema === "mediapipe_lips" && point.mouth_motion >= this.minVariance)
      .map((point) => ({ person: people.find((person) => person.track_id === point.track_id),
        motion: point.mouth_motion })).filter((candidate) => candidate.person)
      .sort((a, b) => b.motion - a.motion);
    if (candidates.length && (!candidates[1] || candidates[0].motion >= candidates[1].motion * 1.5)) {
      this.speaker = { ...candidates[0].person, method: "lip_motion" };
    }
    return [];
  }

  snapshot() {
    const person = this.speaker || this.lastSpeaker;
    const displayName = person?.display_name;
    return person ? { track_id: person.track_id, generation: person.generation,
      source_id: person.source_id, method: person.method,
      display_name: displayName && !["unknown", "unknown speaker", "unknown person"].includes(displayName.trim().toLowerCase())
        ? displayName : null } : null;
  }

  event(phase) {
    return { event_type: "speaking", phase, track_id: this.speaker?.track_id ?? null,
      display_name: this.speaker?.display_name || "Unknown speaker" };
  }

  cameraReset() {
    this.speaker = null;
    this.lastTimestamp = -Infinity;
  }

  stop() {
    if (!this.holding) return [];
    const event = this.event("end");
    this.lastSpeaker = this.speaker;
    this.holding = false;
    this.speaker = null;
    this.audio = [];
    return [event];
  }
}
