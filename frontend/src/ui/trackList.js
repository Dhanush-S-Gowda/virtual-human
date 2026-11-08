/** Render camera identity data as text, never as executable HTML. */
export function renderTrackList(container, people) {
  if (!container) return;
  const document = container.ownerDocument;
  const element = (className, text) => {
    const node = document.createElement('div');
    node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  };
  if (!people?.length) {
    const empty = element('track-empty', 'No persons currently tracked');
    container.replaceChildren(empty);
    return;
  }
  const cards = people.map((person) => {
    const known = Boolean(person.display_name && person.display_name !== 'Unknown');
    const card = element(`track-card${known ? ' known' : ''}`);
    const details = element('track-details');
    details.append(element('track-name', known ? person.display_name : 'Unknown Person'));
    const identity = person.person_id ? ` • ${String(person.person_id).slice(0, 8)}...` : '';
    details.append(element('track-sub', `Track ID: #${person.track_id}${identity}`));
    const actions = Array.isArray(person.actions) && person.actions.length
      ? person.actions.join(', ') : 'Idle';
    const pill = element('info-pill', actions);
    pill.style.fontSize = '11px';
    card.append(details, pill);
    return card;
  });
  container.replaceChildren(...cards);
}
