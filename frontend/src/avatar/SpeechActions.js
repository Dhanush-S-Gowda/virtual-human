/** Hold model actions until response audio is ready, retaining arrival order. */
export class SpeechActions {
  constructor(isSpeaking) {
    this.isSpeaking = isSpeaking;
    this.pending = [];
  }

  add(action) {
    if (this.isSpeaking()) action();
    else this.pending.push(action);
  }

  startSpeech() {
    for (const action of this.pending.splice(0)) action();
  }

  clear() { this.pending.length = 0; }
}
