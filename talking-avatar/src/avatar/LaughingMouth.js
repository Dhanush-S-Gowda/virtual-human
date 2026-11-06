/** Repeated silent HA mouth shapes during laughter; spoken words keep lip-sync priority. */
export class LaughingMouth {
  constructor(getAvatar) {
    this.getAvatar = getAvatar;
    this.active = null;
    this.speech = [];
    this.owned = new Map();
  }

  start(time, duration) {
    this.stop();
    if (!Number.isFinite(time) || !Number.isFinite(duration) || duration <= 0) return null;
    this.active = { start: time, end: time + duration * .6 };
    return this.active;
  }

  stop(handle = this.active) {
    if (handle !== this.active) return;
    this.active = null;
    this.release();
  }

  clear() {
    this.stop();
    this.speech = [];
  }

  scheduleSpeech(result, start, rate) {
    if (!Number.isFinite(start) || !Number.isFinite(rate) || !(rate > 0)) return;
    for (let index = 0; index < result.words.length; index++) {
      if (!/[\p{L}\p{N}]/u.test(result.words[index])) continue;
      const time = result.wtimes[index];
      const duration = result.wdurations[index];
      if (!Number.isFinite(time) || !Number.isFinite(duration) || duration <= 0) continue;
      this.speech.push({ start: start + time / 1000 / rate,
        end: start + (time + duration) / 1000 / rate });
    }
  }

  update(time) {
    if (!Number.isFinite(time)) return;
    this.speech = this.speech.filter(word => word.end > time);
    if (!this.active) return;
    if (time >= this.active.end) { this.stop(); return; }
    if (time < this.active.start || this.speech.some(word => word.start <= time && time < word.end)) {
      this.release();
      return;
    }
    const targets = this.getAvatar()?.mtAvatar;
    if (!targets) { this.release(); return; }
    const mouth = ["viseme_aa", "jawOpen", "mouthOpen"].find(name => targets[name]
      && (!Array.isArray(targets[name].ms) || targets[name].ms.length > 0));
    if (!mouth) return;
    const elapsed = time - this.active.start;
    const envelope = Math.min(1, elapsed / .1, (this.active.end - time) / .1);
    const phase = (elapsed * 2.4) % 1;
    const opening = Math.sin(Math.PI * Math.min(1, phase / .7)) ** 2;
    this.write(targets[mouth], .7 * opening * envelope);
    if (targets.mouthSmile) this.write(targets.mouthSmile, .35 * envelope);
  }

  write(target, value) {
    // Use TalkingHead's realtime layer without erasing pending TTS visemes.
    // Fixed user-controlled targets keep their existing precedence.
    if (target.fixed !== null && target.fixed !== undefined) return;
    let state = this.owned.get(target);
    if (!state) {
      state = { previous: target.realtime ?? null, last: null };
      this.owned.set(target, state);
    }
    target.realtime = value;
    target.needsUpdate = true;
    state.last = value;
  }

  release() {
    for (const [target, state] of this.owned) {
      if (target.realtime === state.last) {
        target.realtime = state.previous;
        target.needsUpdate = true;
      }
    }
    this.owned.clear();
  }
}
