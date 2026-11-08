const EMOTIONS = new Set(["neutral", "happy", "sad", "angry", "fear", "disgust", "love"]);

/** Paragraph emotions follow actual audio intervals, including gesture-added pauses. */
export class EmotionTimeline {
  constructor(getAvatar) {
    this.getAvatar = getAvatar;
    this.pending = [];
    this.managed = false;
    this.current = null;
    this.avatar = null;
  }

  schedule(emotion, start, duration, rate = 1) {
    if (!Number.isFinite(start) || !Number.isFinite(duration) || duration <= 0
        || !Number.isFinite(rate) || rate <= 0) return;
    this.pending.push({ emotion: EMOTIONS.has(emotion) ? emotion : "neutral",
      start, end: start + duration / rate });
    this.pending.sort((a, b) => a.start - b.start);
  }

  update(time) {
    if (!Number.isFinite(time)) return;
    this.pending = this.pending.filter(item => item.end > time);
    const active = this.pending.findLast(item => item.start <= time);
    if (active) {
      this.apply(active.emotion);
      this.managed = true;
    } else if (this.managed) {
      this.apply("neutral");
      this.managed = false;
    }
  }

  apply(emotion) {
    const avatar = this.getAvatar();
    if (!avatar) return;
    if (avatar !== this.avatar || emotion !== this.current || avatar.moodName !== emotion) {
      // TalkingHead smooths facial baselines while retaining lip-sync priority.
      avatar.setMood(emotion);
      this.avatar = avatar;
      this.current = emotion;
    }
  }

  clear() {
    this.pending = [];
    this.apply("neutral");
    this.managed = false;
  }
}
