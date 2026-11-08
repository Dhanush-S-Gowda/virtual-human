import test from 'node:test';
import assert from 'node:assert/strict';
import { renderTrackList } from '../src/ui/trackList.js';

function fixture() {
  const document = { createElement: () => ({
    ownerDocument: document, children: [], style: {}, textContent: '',
    set innerHTML(value) { throw new Error('Identity data must not use HTML parsing'); },
    append(...nodes) { this.children.push(...nodes); },
    replaceChildren(...nodes) { this.children = nodes; },
  }) };
  return document.createElement('div');
}

test('camera names, actions, and identifiers are displayed as literal text', () => {
  const list = fixture();
  const payload = '<img src=x onerror="alert(1)">';
  renderTrackList(list, [{ display_name: payload, actions: [payload], track_id: payload, person_id: payload }]);
  const [card] = list.children;
  assert.equal(card.children[0].children[0].textContent, payload);
  assert.equal(card.children[0].children[0].children.length, 0);
  assert.ok(card.children[0].children[1].textContent.includes(payload));
  assert.equal(card.children[1].textContent, payload);
});

test('camera list replaces stale tracks and handles missing identity details', () => {
  const list = fixture();
  renderTrackList(list, [{ track_id: 1 }]);
  assert.equal(list.children[0].children[0].children[0].textContent, 'Unknown Person');
  assert.equal(list.children[0].children[1].textContent, 'Idle');
  renderTrackList(list, [{ display_name: 'Bhvan', track_id: 2, person_id: '123456789', actions: ['waving'] }]);
  assert.equal(list.children.length, 1);
  assert.equal(list.children[0].className, 'track-card known');
  assert.equal(list.children[0].children[0].children[1].textContent, 'Track ID: #2 • 12345678...');
  renderTrackList(list, []);
  assert.equal(list.children.length, 1);
  assert.equal(list.children[0].textContent, 'No persons currently tracked');
});
