/** Serialize actions that resolve only after their animation and cleanup finish. */
export class ActionQueue {
  constructor(onError = console.error) {
    this.pending = [];
    this.active = null;
    this.activeKind = null;
    this.onError = onError;
  }

  get busy() {
    return this.active !== null || this.pending.length > 0;
  }

  enqueue(action, { front = false, kind = "manual" } = {}) {
    const priority = { tagged: 3, manual: 2, camera: 1, talking: 0 };
    if ((this.activeKind === "talking" && kind !== "talking")
        || (this.activeKind === "camera" && kind === "tagged")) {
      this.activeKind = kind;
      this.abortActive();
      front = true;
    }
    const run = async (signal) => {
      this.activeKind = kind;
      try {
        await action(signal);
      } finally {
        if (this.activeKind === kind) this.activeKind = null;
      }
    };
    run.priority = priority[kind] ?? 2;
    if (front) this.pending.unshift(run);
    else {
      const index = this.pending.findIndex((item) => item.priority < run.priority);
      if (index < 0) this.pending.push(run);
      else this.pending.splice(index, 0, run);
    }
    if (!this.active) void this.drain();
  }

  clear() {
    this.pending.length = 0;
    this.abortActive();
  }

  abortActive() {
    this.active?.abort();
  }

  async drain() {
    while (this.pending.length) {
      const controller = new AbortController();
      this.active = controller;
      try {
        await this.pending.shift()(controller.signal);
      } catch (error) {
        this.onError(error);
      } finally {
        this.active = null;
      }
    }
  }
}

/** Cancellation releases the active action and removes its timer immediately. */
export function waitForAction(ms, signal) {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve(false);
      return;
    }
    const finish = (completed) => {
      clearTimeout(timer);
      signal.removeEventListener("abort", cancel);
      resolve(completed);
    };
    const cancel = () => finish(false);
    const timer = setTimeout(() => finish(true), ms);
    signal.addEventListener("abort", cancel, { once: true });
  });
}
