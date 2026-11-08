import * as THREE from "three";
import { chooseClip } from "./clipActions.js";

/** Foreground clips share a mixer driven only by the speech audio clock. */
export class AudioGesturePlayer {
  constructor(getAvatar, minimumSeconds = 2) {
    this.getAvatar = getAvatar;
    this.avatar = null;
    this.mixer = null;
    this.clips = new Map();
    this.loading = new Map();
    this.active = null;
    this.retiring = [];
    this.clock = null;
    this.minimumSeconds = minimumSeconds;
  }

  get busy() { return Boolean(this.active || this.retiring.length); }

  async prepare(cues) {
    const avatar = this.getAvatar();
    if (!avatar?.armature) throw new Error("Avatar is not ready for gesture playback.");
    if (avatar !== this.avatar || this.root !== avatar.armature) {
      this.clear();
      this.clips.clear();
      this.loading = new Map();
      this.avatar = avatar;
      this.root = avatar.armature;
    }
    const loading = this.loading;
    await Promise.all([...new Set((cues || []).map(cue => cue.action))].map(async (name) => {
      if (this.clips.has(name)) return;
      if (loading.has(name)) return loading.get(name);
      const promise = this.loadClip(avatar, name, loading);
      loading.set(name, promise);
      try { await promise; }
      finally { loading.delete(name); }
    }));
  }

  async loadClip(avatar, name, loading) {
    const url = chooseClip(name);
    if (!url) throw new Error(`Unknown gesture: ${name}`);
    const item = await avatar.playAnimation(url, null, 0, 0, 0.01, null, true);
    if (!item?.clip || !(item.clip.duration > 0)) throw new Error(`Invalid gesture clip: ${name}`);
    const clip = name === "talking" ? new THREE.AnimationClip(`${item.clip.name}-arms`, item.clip.duration,
      item.clip.tracks.filter(track => /^(Left|Right)(Shoulder|Arm|ForeArm|Hand)/.test(track.name))) : item.clip;
    if (this.loading === loading) this.clips.set(name, clip);
  }

  durationFor(name) { return Math.max(this.minimumSeconds, this.clips.get(name)?.duration || 0); }

  play(name, { time, duration, audioTime }) {
    const clip = this.clips.get(name);
    if (!clip || !(duration > 0)) return;
    duration = Math.max(this.durationFor(name), duration);
    if (audioTime >= time + duration) return;
    this.update(audioTime);
    if (!this.mixer) this.mixer = new THREE.AnimationMixer(this.root);
    const action = this.mixer.clipAction(clip);
    const previous = this.active?.action;
    this.retiring = this.retiring.filter(item => item.action !== action);
    action.reset().setLoop(THREE.LoopOnce, 1).setEffectiveWeight(1);
    action.setEffectiveTimeScale(clip.duration / duration);
    action.clampWhenFinished = true;
    action.play();
    action.time = Math.max(0, audioTime - time) * clip.duration / duration;
    if (previous && previous !== action) {
      previous.crossFadeTo(action, 0.1, false);
      this.retiring.push({ action: previous, end: audioTime + 0.1 });
    } else if (!previous) {
      action.fadeIn(Math.min(0.1, duration));
    }
    this.active = { action, end: time + duration };
    this.clock = audioTime;
    this.mixer.update(0);
  }

  update(audioTime) {
    if (!this.mixer) { this.clock = audioTime; return; }
    this.mixer.update(Math.max(0, audioTime - (this.clock ?? audioTime)));
    this.clock = audioTime;
    if (this.active && audioTime >= this.active.end) {
      this.active.action.fadeOut(0.1);
      this.retiring.push({ action: this.active.action, end: audioTime + 0.1 });
      this.active = null;
    }
    this.retiring = this.retiring.filter(item => {
      if (audioTime < item.end) return true;
      item.action.stop();
      return false;
    });
    if (!this.busy) this.clear();
  }

  clear() {
    this.mixer?.stopAllAction();
    if (this.mixer) this.mixer.uncacheRoot(this.root);
    this.mixer = null;
    this.active = null;
    this.retiring = [];
    this.clock = null;
  }
}
