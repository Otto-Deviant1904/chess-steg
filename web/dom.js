// DOM helpers that are pure enough to unit test outside a browser.
//
// Kept in its own module because app.js touches `document` at module scope, so
// importing app.js under plain node throws before any export is reachable.

// CSS classes for a square a selected piece may legally move to.
//
// This must never yield an empty token: classList.add('') throws a TypeError,
// and an earlier version of this rule was written inline as
// `classList.add('target', move.captured ? 'capture' : '')`, which threw on the
// first quiet move of every piece and left the board with *no* legal-move
// highlighting at all. web/test_dom.mjs locks that in.
export function targetClasses(move) {
  const classes = ['target'];
  if (move.captured) classes.push('capture');
  return classes;
}
