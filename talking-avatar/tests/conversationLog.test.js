import test from "node:test";
import assert from "node:assert/strict";
import { ConversationLog } from "../src/ui/conversationLog.js";

// Minimal DOM fixture: exercise message ownership and scrolling without a browser.
function element(document) {
  return {
    ownerDocument: document, children: [], className: "", textContent: "", scrollTop: 0,
    get scrollHeight() {
      return 20 + this.textContent.length + this.children.reduce((height, child) => height + child.scrollHeight, 0);
    },
    append(...nodes) {
      for (const node of nodes) {
        node.parent = this;
        this.children.push(node);
      }
    },
    querySelector(selector) {
      return this.children.find((child) => `.${child.className}` === selector) || null;
    },
    remove() {
      this.parent.children = this.parent.children.filter((child) => child !== this);
    },
  };
}

function fixture() {
  const document = { createElement: () => element(document) };
  const body = element(document);
  const placeholder = element(document);
  placeholder.className = "conversationEmpty";
  body.append(placeholder);
  return { body, log: new ConversationLog(body) };
}

test("multiple turns retain all messages and replace only the initial placeholder", () => {
  const { body, log } = fixture();
  log.append("user", "Hello");
  log.appendReplyDelta("Hi");
  log.finishReply();
  log.append("user", "How are you?");
  log.appendReplyDelta("Doing well");
  log.finishReply();
  assert.equal(body.querySelector(".conversationEmpty"), null);
  assert.deepEqual(body.children.map((block) => block.children.map((child) => child.textContent)), [
    ["You", "Hello"], ["Alex", "Hi"], ["You", "How are you?"], ["Alex", "Doing well"],
  ]);
  assert.equal(body.scrollTop, body.scrollHeight);
});

test("every streamed chunk updates the same reply and follows the growing content", () => {
  const { body, log } = fixture();
  log.append("user", "Tell me a story");
  log.appendReplyDelta("Once upon a time");
  const reply = body.children[1].children[1];
  body.scrollTop = 0;
  log.appendReplyDelta("\n" + "A longer story. ".repeat(100));
  assert.equal(body.children.length, 2);
  assert.equal(body.children[1].children[1], reply);
  assert.ok(reply.textContent.startsWith("Once upon a time\nA longer story."));
  assert.equal(body.scrollTop, body.scrollHeight);
});

test("live transcript revisions update one draft and final text commits in place", () => {
  const { body, log } = fixture();
  log.setUserDraft("I would like");
  const draft = body.children[0];
  log.setUserDraft("I would like to ask");
  assert.equal(body.children.length, 1);
  assert.equal(draft.children[1].textContent, "I would like to ask");
  log.commitUserDraft("I would like to ask a question.");
  assert.equal(body.children.length, 1);
  assert.equal(body.children[0], draft);
  assert.equal(draft.children[1].textContent, "I would like to ask a question.");
});

test("an empty final transcript discards its partial draft", () => {
  const { body, log } = fixture();
  log.setUserDraft("unconfirmed words");
  log.commitUserDraft("");
  assert.equal(body.children.length, 0);
});

test("errors preserve history and a subsequent reply does not overwrite the previous reply", () => {
  const { body, log } = fixture();
  log.append("user", "First question");
  log.appendReplyDelta("Partial reply");
  log.finishReply();
  log.append("error", "Connection lost");
  assert.equal(body.scrollTop, body.scrollHeight);
  log.append("user", "Try again");
  log.appendReplyDelta("New reply");
  assert.equal(body.children.length, 5);
  assert.equal(body.children[1].children[1].textContent, "Partial reply");
  assert.equal(body.children[2].textContent, "Connection lost");
  assert.equal(body.children[4].children[1].textContent, "New reply");
});

test("messages remain plain text and empty deltas do not create reply blocks", () => {
  const { body, log } = fixture();
  const message = '<img src=x onerror="alert(1)">';
  log.append("user", message);
  log.appendReplyDelta("");
  assert.equal(body.children.length, 1);
  assert.equal(body.children[0].children[1].textContent, message);
  assert.equal(body.children[0].children[1].children.length, 0);
});

test("an unavailable conversation panel is safe to update", () => {
  const log = new ConversationLog(null);
  assert.doesNotThrow(() => {
    log.append("error", "Connection lost");
    log.appendReplyDelta("Reply");
    log.finishReply();
  });
});

test("original delivery tags and markup remain inline as literal text across chunks", () => {
  const { body, log } = fixture();
  const chunks = ["[hap", "py][fast][clapping]Great! 👋 ", "[slow][sad]Take care. <b>Literal</b>"];
  for (const chunk of chunks) log.appendReplyDelta(chunk);
  log.finishReply();
  assert.equal(body.children.length, 1);
  const block = body.children[0];
  assert.equal(block.children.length, 2); // Label and original text, with no gesture summary.
  assert.equal(block.children[1].textContent, chunks.join(""));
  assert.equal(block.children[1].children.length, 0); // Model output is never interpreted as HTML.
});
