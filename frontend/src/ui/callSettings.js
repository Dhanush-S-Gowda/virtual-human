import { site } from "../../../TalkingHead/siteconfig.js";

const presets = Object.entries(site.avatars).map(([label, preset]) => ({
  id: preset.url.split("/").pop().replace(/\.glb$/, ""), label, preset,
}));
presets.splice(1, 0, { id: "brunette-t", label: "Brunette (T-pose)", preset: {
  ...site.avatars.Brunette, url: "./avatars/brunette-t.glb",
} });
export const AVATARS = presets.map(({ id, label }) => ({ id, label }));
export const VOICES = { kokoro: ["am_puck", "af_heart"], supertonic: ["F1", "M1"] };
const DEFAULT_VOICES = { kokoro: "af_heart", supertonic: "F1" };
export const DEFAULT_ZOOM_PERCENT = 139.15;
const MIN_ZOOM_PERCENT = 100;
const MAX_ZOOM_PERCENT = 200;
const STORAGE_KEY = "avatar-call.preferences.v1";

export function avatarOptions(id) {
  const entry = presets.find((item) => item.id === id);
  if (!entry) throw new Error("Choose an available avatar.");
  return { ...structuredClone(entry.preset), url: `/avatars/${id}.glb`, lipsyncLang: "en" };
}

export class CallPreferences {
  constructor(storage) {
    this.saved = {};
    this.provider = null;
    this.storage = null;
    try {
      this.storage = storage === undefined ? globalThis.localStorage : storage;
      const parsed = JSON.parse(this.storage?.getItem(STORAGE_KEY) || "{}");
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) this.saved = parsed;
    } catch { /* A later save can replace malformed stored preferences. */ }
  }

  get avatar() {
    return AVATARS.some(({ id }) => id === this.saved.avatar) ? this.saved.avatar : "brunette";
  }

  get zoomPercent() {
    const value = Number(this.saved.zoomPercent);
    return Number.isFinite(value) && value >= MIN_ZOOM_PERCENT && value <= MAX_ZOOM_PERCENT
      ? value : DEFAULT_ZOOM_PERCENT;
  }

  get zoomFactor() { return this.zoomPercent / 100; }

  setProvider(provider) {
    this.provider = Object.hasOwn(VOICES, provider) ? provider : null;
  }

  get voice() {
    if (!this.provider) return undefined;
    const saved = this.saved.voices?.[this.provider];
    return VOICES[this.provider].includes(saved) ? saved : DEFAULT_VOICES[this.provider];
  }

  save(avatar, voice, provider = this.provider, zoomPercent = this.zoomPercent) {
    avatarOptions(avatar); // Stored preferences may never supply an arbitrary asset URL.
    if (provider && (!Object.hasOwn(VOICES, provider) || !VOICES[provider].includes(voice))) throw new Error("Choose an available voice.");
    const zoom = Number(zoomPercent);
    if (!Number.isFinite(zoom) || zoom < MIN_ZOOM_PERCENT || zoom > MAX_ZOOM_PERCENT) throw new Error("Choose a zoom between 100% and 200%.");
    const voices = {};
    for (const id of Object.keys(VOICES)) {
      const value = this.saved.voices?.[id];
      if (VOICES[id].includes(value)) voices[id] = value;
    }
    if (provider) voices[provider] = voice;
    this.saved = { avatar, voices, zoomPercent: zoom };
    try { this.storage?.setItem(STORAGE_KEY, JSON.stringify(this.saved)); } catch { /* Usable for this page even if storage is blocked. */ }
  }
}

export function mountCallSettings(preferences, onAvatarChange, onZoomChange = () => {}) {
  const dialog = document.getElementById("callSettings");
  const form = document.getElementById("settingsForm");
  const avatar = document.getElementById("avatarSelect");
  const voice = document.getElementById("voiceSelect");
  const zoom = document.getElementById("avatarZoom");
  const zoomValue = document.getElementById("avatarZoomValue");
  const providerLabel = document.getElementById("voiceProvider");
  const save = document.getElementById("settingsSave");
  const close = document.getElementById("settingsCancel");
  const status = document.getElementById("settingsStatus");
  let busy = false;
  const option = (value, label = value) => {
    const node = document.createElement("option");
    node.value = value;
    node.textContent = label;
    return node;
  };
  avatar.replaceChildren(...AVATARS.map(({ id, label }) => option(id, label)));
  const refresh = () => {
    const provider = preferences.provider;
    providerLabel.textContent = provider ? (provider === "kokoro" ? "Kokoro" : "Supertonic") : "Waiting for backend";
    voice.replaceChildren(...(provider ? VOICES[provider].map((id) => option(id)) : [option("", "Voice options unavailable")]));
    voice.disabled = busy || !provider;
    voice.value = preferences.voice || "";
  };
  const setZoom = (value) => {
    zoom.value = String(value);
    zoomValue.textContent = `${Number(value).toFixed(1).replace(/\.0$/, "")}%`;
  };
  const revertZoom = () => {
    setZoom(preferences.zoomPercent);
    onZoomChange(preferences.zoomFactor);
  };
  document.getElementById("settingsButton").addEventListener("click", () => {
    avatar.value = preferences.avatar;
    status.textContent = "";
    refresh();
    setZoom(preferences.zoomPercent);
    dialog.showModal();
  });
  zoom.addEventListener("input", () => {
    setZoom(zoom.value);
    onZoomChange(Number(zoom.value) / 100);
  });
  close.addEventListener("click", () => { revertZoom(); dialog.close(); });
  dialog.addEventListener("cancel", (event) => {
    if (busy) event.preventDefault();
    else revertZoom();
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    busy = true;
    save.disabled = close.disabled = avatar.disabled = voice.disabled = zoom.disabled = true;
    const selected = avatar.value;
    const selectedVoice = voice.value;
    const selectedProvider = preferences.provider;
    const selectedZoom = Number(zoom.value);
    status.textContent = selected !== preferences.avatar ? "Loading avatar…" : "Saving…";
    try {
      await onAvatarChange(selected);
      preferences.save(selected, selectedVoice, selectedProvider, selectedZoom);
      onZoomChange(selectedZoom / 100);
      dialog.close();
    } catch (error) {
      revertZoom();
      status.textContent = error.message || "Settings could not be applied. Please try again.";
    } finally {
      busy = false;
      save.disabled = close.disabled = avatar.disabled = false;
      zoom.disabled = false;
      refresh();
    }
  });
  refresh();
  return refresh;
}
