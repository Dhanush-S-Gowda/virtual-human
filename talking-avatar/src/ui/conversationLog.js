/** Keep the current call's messages and follow each new message or reply chunk. */
export class ConversationLog {
  constructor(body) {
    this.body = body;
    this.reply = null;
    this.userDraft = null;
    if (body && globalThis.MutationObserver) {
      this.scrollObserver = new globalThis.MutationObserver(() => this.scrollToLatest());
      this.scrollObserver.observe(body, { childList: true, characterData: true, subtree: true });
    }
  }

  scrollToLatest() {
    if (this.body) this.body.scrollTop = this.body.scrollHeight;
  }

  append(type, text, { speakerName = null, finalizedAt = null } = {}) {
    if (!this.body) return null;
    const document = this.body.ownerDocument;
    const block = document.createElement("div");
    let content = block;
    if (type === "user" || type === "avatar") {
      const label = document.createElement("div");
      label.className = type === "user" ? "transcript-user" : "transcript-avatar";
      label.textContent = this.labelText(type, speakerName, finalizedAt);
      content = document.createElement("div");
      content.className = type === "user" ? "transcript-text" : "transcript-reply";
      block.append(label, content);
    } else {
      block.className = `transcript-${type}`;
    }
    content.textContent = type === "thinking" ? "Thinking…" : text;
    this.body.querySelector(".conversationEmpty")?.remove();
    this.body.append(block);
    this.scrollToLatest();
    return content;
  }

  labelText(type, speakerName = null, finalizedAt = null) {
    const name = type === "user" ? (speakerName || "Unknown person") : "Alex";
    return finalizedAt ? `${name} · ${finalizedAt}` : name;
  }

  appendReplyDelta(text) {
    if (!text || !this.body) return;
    if (!this.reply) this.reply = this.append("avatar", "");
    this.reply.textContent += text;
    this.scrollToLatest();
  }

  appendGestureTags(tags) {
    if (!this.reply || !Array.isArray(tags) || !tags.length) return;
    let line = this.reply.parentElement?.querySelector?.(".transcript-gestures");
    if (!line) {
      line = this.body.ownerDocument.createElement("div");
      line.className = "transcript-gestures";
      this.reply.parentElement?.append(line);
    }
    line.textContent = `Gestures: ${tags.join(", ")}`;
    this.scrollToLatest();
  }

  clearErrors() {
    if (!this.body) return;
    for (const error of this.body.querySelectorAll?.(".transcript-error") || []) error.remove();
    this.scrollToLatest();
  }

  setUserDraft(text) {
    if (!this.userDraft) {
      const node = this.append("user", "");
      this.userDraft = node ? { node, block: this.body.children[this.body.children.length - 1] } : null;
    }
    if (this.userDraft) {
      this.userDraft.node.textContent = text;
      this.scrollToLatest();
    }
  }

  commitUserDraft(text, speakerName = null, finalizedAt = null) {
    if (!text) {
      this.discardUserDraft();
      return;
    }
    if (this.userDraft) {
      this.userDraft.node.textContent = text;
      const label = this.userDraft.block.querySelector?.(".transcript-user");
      if (label) label.textContent = this.labelText("user", speakerName, finalizedAt);
      this.userDraft = null;
      this.scrollToLatest();
    } else {
      this.append("user", text, { speakerName, finalizedAt });
    }
  }

  discardUserDraft() {
    this.userDraft?.block.remove();
    this.userDraft = null;
  }

  finishReply(finalizedAt = null) {
    const label = this.reply?.parentElement?.querySelector?.(".transcript-avatar");
    if (label && finalizedAt) label.textContent = this.labelText("avatar", null, finalizedAt);
    this.reply = null;
  }
}
