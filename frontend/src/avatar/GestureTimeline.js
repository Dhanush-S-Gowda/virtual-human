/** Run mandatory foreground cues on the audio clock; talking motion is optional. */
export class GestureTimeline {
  constructor(dispatch, isBusy) {
    this.dispatch = dispatch;
    this.isBusy = isBusy;
    this.pending = [];
    this.skipped = 0;
  }

  schedule(cues, audioStart, rate = 1) {
    if (!Number.isFinite(audioStart) || !(rate > 0)) return;
    for (const cue of cues || []) {
      if (typeof cue.action !== "string" || !Number.isFinite(cue.at_ms) || cue.at_ms < 0) continue;
      this.pending.push({ action: cue.action, time: audioStart + cue.at_ms / 1000 / rate,
        duration: Number.isFinite(cue.duration_ms) && cue.duration_ms > 0 ? cue.duration_ms / 1000 / rate : null });
    }
    this.pending.sort((a, b) => a.time - b.time);
  }

  advance(audioTime) {
    while (this.pending.length && this.pending[0].time <= audioTime) {
      const cue = this.pending.shift();
      if ((cue.duration !== null && audioTime >= cue.time + cue.duration)
          || (cue.action === "talking" && cue.duration === null
            && (audioTime - cue.time > 0.15 || this.isBusy()))) this.skipped++;
      else this.dispatch(cue.action, { ...cue, audioTime });
    }
  }

  hasUpcomingForeground(audioTime, lookahead = 0.25) {
    return this.pending.some((cue) => cue.action !== "talking" && cue.time - audioTime <= lookahead);
  }

  clear() { this.pending.length = 0; }
}
