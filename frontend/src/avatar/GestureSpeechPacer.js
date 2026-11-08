/** Align cues to speech; optional complete mode inserts silence for full clips. */
export class GestureSpeechPacer {
  constructor(minimumSeconds = 2, mode = "speech") {
    this.minimumSeconds = minimumSeconds;
    this.mode = mode;
    this.remaining = 0;
  }

  clear() { this.remaining = 0; }

  pace(context, audio, result, durationForAction, rate = 1) {
    if (!(rate > 0) || !Number.isFinite(rate)) throw new Error("Invalid speech playback rate.");
    const sampleRate = audio.sampleRate;
    const cues = (result.gestures || []).filter(cue => typeof cue.action === "string"
      && Number.isFinite(cue.at_ms) && cue.at_ms >= 0 && cue.at_ms / 1000 < audio.duration)
      .map(cue => ({ ...cue, frame: Math.round(cue.at_ms * sampleRate / 1000) }))
      .sort((a, b) => a.frame - b.frame);
    if (this.mode === "speech") {
      this.remaining = 0;
      return { ...result, audio, gestures: cues.map(cue => {
        const duration = durationForAction(cue.action);
        if (!(duration > 0) || !Number.isFinite(duration)) throw new Error(`Invalid gesture duration: ${cue.action}`);
        return { action: cue.action, word_index: cue.word_index, at_ms: cue.at_ms,
          duration_ms: Math.max(this.minimumSeconds, duration) * rate * 1000 };
      }) };
    }
    if (this.mode !== "complete") throw new Error("Invalid gesture pacing mode.");
    const insertions = [];
    const gestures = [];
    let cursor = 0;
    let added = 0;
    let remaining = this.remaining;
    for (const cue of cues) {
      remaining = Math.max(0, remaining - (cue.frame - cursor) / sampleRate / rate);
      const silence = Math.ceil(remaining * sampleRate * rate);
      if (silence > 0) {
        insertions.push({ frame: cue.frame, length: silence });
        added += silence;
      }
      const clipDuration = durationForAction(cue.action);
      if (!(clipDuration > 0) || !Number.isFinite(clipDuration)) throw new Error(`Invalid gesture duration: ${cue.action}`);
      remaining = Math.max(this.minimumSeconds, clipDuration);
      gestures.push({ action: cue.action, word_index: cue.word_index,
        at_ms: (cue.frame + added) * 1000 / sampleRate,
        duration_ms: remaining * rate * 1000 });
      cursor = cue.frame;
    }
    remaining = Math.max(0, remaining - (audio.length - cursor) / sampleRate / rate);
    let pacedAudio = audio;
    if (added) {
      pacedAudio = context.createBuffer(audio.numberOfChannels, audio.length + added, sampleRate);
      for (let channel = 0; channel < audio.numberOfChannels; channel++) {
        const source = audio.getChannelData(channel);
        const target = pacedAudio.getChannelData(channel);
        let from = 0;
        let to = 0;
        for (const gap of insertions) {
          target.set(source.subarray(from, gap.frame), to);
          to += gap.frame - from + gap.length;
          from = gap.frame;
        }
        target.set(source.subarray(from), to);
      }
    }
    const wtimes = result.wtimes.map(time => {
      const frame = Math.round(time * sampleRate / 1000);
      return time + insertions.reduce((sum, gap) => sum + (gap.frame <= frame ? gap.length : 0), 0) * 1000 / sampleRate;
    });
    this.remaining = remaining;
    return { ...result, audio: pacedAudio, gestures, wtimes };
  }
}
