/** Get or create an opaque conversation ID in the supplied storage. */
export function conversationSessionId(storage, uuid = () => crypto.randomUUID()) {
  const key = "talking-avatar.conversation-session";
  try {
    const existing = storage?.getItem(key);
    if (existing) return existing;
    const id = uuid();
    storage?.setItem(key, id);
    return id;
  } catch {
    return uuid();
  }
}

export function tabSessionId() {
  let persistent;
  let tab;
  try { persistent = globalThis.localStorage; } catch { /* Use tab storage. */ }
  try { tab = globalThis.sessionStorage; } catch { /* Use an in-memory ID. */ }
  return persistentConversationId(persistent, tab);
}

/** One browser profile keeps its memory identity across tabs and browser restarts. */
export function persistentConversationId(persistent, tab, uuid = () => crypto.randomUUID()) {
  const key = "virtual-human.memory-conversation";
  try {
    const saved = persistent?.getItem(key);
    if (saved) return saved;
    if (!persistent) return conversationSessionId(tab, uuid);
    const id = conversationSessionId(tab, uuid);
    persistent.setItem(key, id);
    return id;
  } catch {
    return conversationSessionId(tab, uuid);
  }
}
