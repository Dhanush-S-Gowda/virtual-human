/** Keep the current call's messages and follow each new message or reply chunk. */
export class ConversationLog {
  constructor(body) {
    this.body = body;
    this.reply = null;
  }

  scrollToLatest() {
    if (this.body) this.body.scrollTop = this.body.scrollHeight;
  }

  append(type, text) {
    if (!this.body) return null;
    const document = this.body.ownerDocument;
    const block = document.createElement("div");
    let content = block;
    if (type === "user" || type === "avatar") {
      const label = document.createElement("div");
      label.className = type === "user" ? "transcript-user" : "transcript-avatar";
      label.textContent = type === "user" ? "You" : "Alex";
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

  appendReplyDelta(text) {
    if (!text || !this.body) return;
    if (!this.reply) this.reply = this.append("avatar", "");
    this.reply.textContent += text;
    this.scrollToLatest();
  }

  finishReply() {
    this.reply = null;
  }
}
