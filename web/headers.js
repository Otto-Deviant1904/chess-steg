// PGN header parsing for the stego parameters a carrier carries.
//
// Split out of app.js because it is pure and therefore unit-testable: app.js
// touches `document` at module scope, so nothing in it can be imported under
// plain node. web/test_dom.mjs asserts the rules here directly.

function tag(pgn, name) {
  const m = pgn.match(new RegExp(`\\[${name}\\s+"([^"]*)"\\]`));
  return m ? m[1] : null;
}

// Read coding parameters out of a carrier PGN.
//
// The `steering` default is the load-bearing part: an absent StegoMode tag
// means *unstated*, and we read that as plain. codec.py's opts_from_headers()
// uses `mode == "steered"`, so the two agree. They used to disagree — the app
// defaulted to steered — which meant a carrier with its Stego* tags stripped
// decoded in the browser but never in Python, contradicting the README's
// central compatibility claim. Keep these two in step.
export function optsFromPgn(pgn) {
  const mode = tag(pgn, 'StegoMode');
  return {
    arithmetic: (tag(pgn, 'StegoCodec') || 'arithmetic') !== 'integer',
    steering: mode === 'steered',
    beta: parseInt(tag(pgn, 'StegoBeta'), 10) || 1,
    window: parseInt(tag(pgn, 'StegoWindow'), 10) || 10,
    // Whether the mode was actually stated, so the UI can flag a guess.
    modeStated: mode !== null,
  };
}
