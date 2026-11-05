/** Occasional short acknowledgements during live microphone activity. */
export class ListeningNods {
  constructor(getAvatar, isBlocked, { random = Math.random, now = () => performance.now() } = {}) {
    this.getAvatar = getAvatar;
    this.isBlocked = isBlocked;
    this.random = random;
    this.now = now;
    this.timer = null;
    this.endTimer = null;
    this.avatar = null;
    this.lastActivity = -Infinity;
  }

  get busy() { return this.avatar !== null; }

  start() {
    this.stop();
    this.lastActivity = -Infinity;
    this.schedule();
  }

  addAudio({ rms, timestamp }) {
    if (Number.isFinite(rms) && rms >= 0.02 && Number.isFinite(timestamp)) {
      this.lastActivity = timestamp;
    }
  }

  schedule() {
    this.timer = setTimeout(() => {
      this.timer = null;
      const age = this.now() - this.lastActivity;
      const avatar = this.getAvatar();
      if (avatar && age >= 0 && age <= 700 && !this.isBlocked()) {
        this.avatar = avatar;
        avatar.playGesture("yes", 0.7, false, 200);
        this.endTimer = setTimeout(() => this.stopNod(), 1000);
      }
      this.schedule();
    }, 4000 + this.random() * 4000);
  }

  stopNod() {
    clearTimeout(this.endTimer);
    this.endTimer = null;
    this.avatar?.stopGesture(200);
    this.avatar = null;
  }

  stop() {
    clearTimeout(this.timer);
    this.timer = null;
    this.stopNod();
  }
}
