// Regression tests for the UI-adjacent pure helpers and codec argument guards.
//
// Run: node test_dom.mjs
//
// These exist because the browser-only code paths had no coverage at all: the
// legal-move-highlighting bug below shipped a completely dead core feature, and
// the fractional-beta bug let the UI's own slider corrupt the coder. Both are
// cheap to pin down here without adding a browser dependency to the project.

import { targetClasses } from './dom.js';
import { optsFromPgn } from './headers.js';
import { MAX_PAYLOAD_BYTES, payloadRejection } from './limits.js';
import { weightedMoves, encodeIntoGame, textToBytes, decodeGame, bytesToText } from './stego.js';
import { Chess } from './vendor/chess.js';

let failures = 0;
function check(name, fn) {
  try {
    fn();
    console.log(`PASS ${name}`);
  } catch (e) {
    failures++;
    console.log(`FAIL ${name}: ${e.message}`);
  }
}
function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}
function assertThrows(fn, re, msg) {
  let threw = null;
  try {
    fn();
  } catch (e) {
    threw = e;
  }
  assert(threw !== null, `${msg}: expected a throw, got none`);
  assert(re.test(threw.message), `${msg}: message was ${JSON.stringify(threw.message)}`);
}

// ── legal-move highlighting ────────────────────────────────────────────────
// classList.add('') throws a TypeError. The old inline expression
//   classList.add('target', m.captured ? 'capture' : '')
// did exactly that on the first quiet move of every piece, so no square was ever
// highlighted and the exception aborted the rest of renderTargets().

check('targetClasses never emits an empty class token', () => {
  for (const move of [{}, { captured: undefined }, { captured: null }, { captured: false }]) {
    const classes = targetClasses(move);
    for (const c of classes) {
      assert(typeof c === 'string' && c.length > 0, `empty/invalid token from ${JSON.stringify(move)}`);
    }
  }
});

check('targetClasses marks captures', () => {
  assert(targetClasses({}).join(' ') === 'target', 'quiet move should be plain target');
  assert(targetClasses({ captured: 'p' }).join(' ') === 'target capture', 'capture should add capture');
});

check('targetClasses output is directly usable with classList.add', () => {
  // Simulate the real DOM call: a classList that throws on an empty token,
  // exactly like the browser's.
  const makeClassList = () => {
    const set = new Set();
    return {
      add(...tokens) {
        for (const t of tokens) {
          if (typeof t !== 'string' || t === '') {
            const err = new TypeError("Failed to execute 'add' on 'DOMTokenList': The token provided must not be empty.");
            throw err;
          }
          set.add(t);
        }
      },
      has: (t) => set.has(t),
    };
  };
  for (const move of [{}, { captured: 'p' }]) {
    const cl = makeClassList();
    cl.add(...targetClasses(move)); // must not throw
    assert(cl.has('target'), 'target class missing');
  }
});

// ── steering weight guards ────────────────────────────────────────────────

check('weightedMoves rejects a fractional beta with a clear message', () => {
  assertThrows(
    () => weightedMoves(new Chess(), { steering: true, beta: 1.5, window: 10 }),
    /beta must be a whole number/,
    'beta=1.5'
  );
  assertThrows(
    () => weightedMoves(new Chess(), { steering: true, beta: 0.5, window: 10 }),
    /beta must be a whole number/,
    'beta=0.5'
  );
});

check('weightedMoves rejects a non-integer or too-small window', () => {
  assertThrows(
    () => weightedMoves(new Chess(), { steering: true, beta: 1, window: 2.5 }),
    /window must be a whole number/,
    'window=2.5'
  );
  assertThrows(
    () => weightedMoves(new Chess(), { steering: true, beta: 1, window: 0 }),
    /window must be a whole number/,
    'window=0'
  );
});

check('integer betas still work end to end', () => {
  for (const beta of [0, 1, 2, 4, 8]) {
    const chess = new Chess();
    const opts = { steering: true, beta, window: 10 };
    const res = encodeIntoGame(chess, textToBytes(`beta ${beta}`), opts, () => {});
    assert(res.bitsUsed > 0, `beta=${beta} embedded nothing`);
    chess.setHeader('StegoMode', 'steered');
    chess.setHeader('StegoBeta', String(beta));
    chess.setHeader('StegoWindow', '10');
    const out = decodeGame(chess.pgn(), opts);
    assert(bytesToText(out) === `beta ${beta}`, `beta=${beta} roundtrip mismatch`);
  }
});

// ── StegoMode default must match codec.py ──────────────────────────────────
// opts_from_headers() in codec.py uses `mode == "steered"`, so a PGN with no
// StegoMode tag means plain. The app used to assume steered, so a carrier with
// its tags stripped decoded in the browser but never in Python. That rule lives
// in app.js (which needs a DOM to import), so it is asserted here by replaying
// the same expression it uses.

check('absent StegoMode means plain, matching codec.py', () => {
  const untagged = optsFromPgn('[Event "x"]\n\n1. e4 e5 *');
  assert(untagged.steering === false, 'absent tag must be plain');
  assert(untagged.modeStated === false, 'absent tag must be reported as unstated');
  assert(optsFromPgn('[StegoMode "plain"]').steering === false, 'plain must be plain');
  assert(optsFromPgn('[StegoMode "steered"]').steering === true, 'steered must be steered');
  assert(optsFromPgn('[StegoMode "steered"]').modeStated === true, 'stated tag must be reported');
});

check('header parsing picks up codec, beta and window', () => {
  const o = optsFromPgn('[StegoCodec "integer"]\n[StegoMode "steered"]\n[StegoBeta "3"]\n[StegoWindow "7"]');
  assert(o.arithmetic === false, 'integer codec not detected');
  assert(o.steering === true && o.beta === 3 && o.window === 7, JSON.stringify(o));
  // defaults when tags are absent or junk
  const d = optsFromPgn('[StegoMode "steered"]');
  assert(d.arithmetic === true && d.beta === 1 && d.window === 10, JSON.stringify(d));
  const j = optsFromPgn('[StegoBeta "not-a-number"]');
  assert(j.beta === 1, `junk beta should fall back to 1, got ${j.beta}`);
});

// ── payload size guard ────────────────────────────────────────────────────
// encodeIntoGame() burns main-thread time superlinearly in payload size (53 s at
// 200 kB) and a payload past a few hundred bytes can never fit in a chess game
// anyway. The app must refuse it up front rather than discover it slowly.

check('payload guard allows realistic payloads', () => {
  for (const bytes of [0, 1, 14, 56, 180, MAX_PAYLOAD_BYTES]) {
    assert(payloadRejection(bytes) === null, `${bytes} bytes should be allowed`);
  }
});

check('payload guard refuses oversized payloads with a useful message', () => {
  for (const bytes of [MAX_PAYLOAD_BYTES + 1, 5000, 200000]) {
    const msg = payloadRejection(bytes);
    assert(typeof msg === 'string' && msg.length > 0, `${bytes}: expected a message`);
    assert(msg.includes(String(bytes)), `message should name the size: ${msg}`);
    assert(msg.includes(String(MAX_PAYLOAD_BYTES)), `message should name the limit: ${msg}`);
  }
});

check('payload guard is byte-based, not character-based', () => {
  // The guard is applied to UTF-8 length, so multi-byte text is measured
  // correctly. 4-byte emoji: 1000 chars is 4000 bytes and must be refused.
  const text = '\u{1F512}'.repeat(1000);
  assert(new TextEncoder().encode(text).length === 4000, 'fixture should be 4000 bytes');
  assert(payloadRejection(new TextEncoder().encode(text).length) !== null, 'should refuse');
});

console.log(failures ? `\n${failures} FAILURE(S)` : '\nALL DOM/GUARD TESTS PASS');
process.exit(failures ? 1 : 0);
