class BreathScheduler {
  constructor() {
    this.context = null;
    this.buffers = new Map();
    this.pending = new Map();
    this.active = null;
  }

  ensureContext() {
    if (this.context) return this.context;
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) return null;
    this.context = new Context({ latencyHint: "playback" });
    return this.context;
  }

  async prime() {
    const context = this.ensureContext();
    if (!context) return false;
    try {
      if (context.state === "suspended") await context.resume();
      return context.state === "running";
    } catch {
      return false;
    }
  }

  async preload(url) {
    if (!url) return null;
    if (this.buffers.has(url)) return this.buffers.get(url);
    if (this.pending.has(url)) return this.pending.get(url);
    const promise = (async () => {
      const context = this.ensureContext();
      if (!context) return null;
      const response = await fetch(url, { cache: "force-cache" });
      if (!response.ok) throw new Error(`Breath audio returned ${response.status}.`);
      const buffer = await context.decodeAudioData(await response.arrayBuffer());
      this.buffers.set(url, buffer);
      return buffer;
    })().finally(() => this.pending.delete(url));
    this.pending.set(url, promise);
    return promise;
  }

  snapshot() {
    const state = this.active;
    if (!state) return null;
    const running = state.paused
      ? 0
      : Math.max(0, (state.context.currentTime - state.startedAt) * state.playbackRate);
    return {
      chunkIndex: state.chunkIndex,
      phase: state.phase,
      elapsed: Math.min(state.duration, state.offset + running),
      duration: state.duration,
      paused: state.paused,
      event: state.event,
    };
  }

  _start(state) {
    const remaining = Math.max(0, state.duration - state.offset);
    if (remaining <= 0.01) {
      this._finish(state, true);
      return;
    }
    const source = state.context.createBufferSource();
    const gain = state.context.createGain();
    source.buffer = state.buffer;
    source.playbackRate.value = state.playbackRate;
    source.connect(gain).connect(state.context.destination);
    const now = state.context.currentTime;
    const wallDuration = remaining / state.playbackRate;
    const fadeIn = Math.min(0.018, wallDuration / 4);
    const fadeOut = Math.min(0.025, wallDuration / 4);
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.linearRampToValueAtTime(0.92, now + fadeIn);
    gain.gain.setValueAtTime(0.92, Math.max(now + fadeIn, now + wallDuration - fadeOut));
    gain.gain.linearRampToValueAtTime(0.0001, now + wallDuration);
    state.source = source;
    state.gain = gain;
    state.startedAt = now;
    state.paused = false;
    source.onended = () => {
      if (this.active !== state || state.paused || state.cancelled) return;
      state.offset = state.duration;
      this._finish(state, true);
    };
    source.start(0, state.offset, remaining);
    state.timer = window.setInterval(() => state.onProgress?.(this.snapshot()), 100);
    state.onProgress?.(this.snapshot());
  }

  _finish(state, completed) {
    if (state.timer) window.clearInterval(state.timer);
    state.timer = null;
    if (this.active === state) this.active = null;
    state.onProgress?.({
      chunkIndex: state.chunkIndex,
      phase: state.phase,
      elapsed: state.duration,
      duration: state.duration,
      paused: false,
      event: state.event,
    });
    state.resolve?.(completed);
  }

  async play({ url, chunkIndex, phase, event, offset = 0, playbackRate = 1, onProgress }) {
    this.cancel();
    const buffer = await this.preload(url);
    const context = this.ensureContext();
    if (!buffer || !context) return false;
    if (context.state === "suspended") await context.resume();
    return new Promise((resolve) => {
      const state = {
        buffer,
        context,
        url,
        chunkIndex,
        phase,
        event,
        duration: buffer.duration,
        offset: Math.max(0, Math.min(buffer.duration, Number(offset) || 0)),
        playbackRate: Math.max(0.7, Math.min(1.5, Number(playbackRate) || 1)),
        paused: false,
        cancelled: false,
        source: null,
        gain: null,
        timer: null,
        startedAt: 0,
        onProgress,
        resolve,
      };
      this.active = state;
      this._start(state);
    });
  }

  pause() {
    const state = this.active;
    if (!state || state.paused) return false;
    const snapshot = this.snapshot();
    state.offset = snapshot?.elapsed || state.offset;
    state.paused = true;
    if (state.timer) window.clearInterval(state.timer);
    state.timer = null;
    if (state.source) {
      state.source.onended = null;
      try { state.source.stop(); } catch { /* already stopped */ }
    }
    state.onProgress?.(this.snapshot());
    return true;
  }

  async resume() {
    const state = this.active;
    if (!state || !state.paused) return false;
    if (state.context.state === "suspended") await state.context.resume();
    this._start(state);
    return true;
  }

  cancel() {
    const state = this.active;
    if (!state) return false;
    state.cancelled = true;
    if (state.timer) window.clearInterval(state.timer);
    if (state.source) {
      state.source.onended = null;
      try { state.source.stop(); } catch { /* already stopped */ }
    }
    this.active = null;
    state.resolve?.(false);
    return true;
  }

  clear() {
    this.cancel();
    this.buffers.clear();
    this.pending.clear();
  }
}

export const ttsBreathScheduler = new BreathScheduler();
