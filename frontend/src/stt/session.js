/** A tab keeps its conversation ID through reloads; another tab gets its own ID. */
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
  try {
    return conversationSessionId(globalThis.sessionStorage);
  } catch {
    return conversationSessionId(null);
  }
}
