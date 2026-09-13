// Interop harness — proves the JS codec and the Python mirror (codec.py) are
// bit-compatible. Driven by test_interop.py; not a standalone test.
//
//   node interop.mjs gen <dir> <spec.json>   encode each case -> <dir>/js_<i>.pgn
//   node interop.mjs dec <dir> <spec.json>   decode each <dir>/py_<i>.pgn -> <dir>/py_<i>.json
//
// A "case" is { text, codec, steering, beta, window, startFen? }. Both
// implementations read the same spec and must produce the same carrier game.

import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { Chess } from './vendor/chess.js';
import {
  encodeIntoGame,
  decodeGame,
  encodeIntoGameInteger,
  decodeGameInteger,
  textToBytes,
  bytesToText,
} from './stego.js';

function optsFor(c) {
  return c.steering ? { steering: true, beta: c.beta ?? 1, window: c.window ?? 10 } : { steering: false };
}

function pgnOf(chess, c) {
  chess.setHeader('Event', 'Steganographic chess');
  if (c.startFen) {
    chess.setHeader('SetUp', '1');
    chess.setHeader('FEN', c.startFen);
  }
  chess.setHeader('StegoCodec', c.codec);
  chess.setHeader('StegoMode', c.steering ? 'steered' : 'plain');
  if (c.steering) {
    chess.setHeader('StegoBeta', String(c.beta ?? 1));
    chess.setHeader('StegoWindow', String(c.window ?? 10));
  }
  return chess.pgn();
}

// A carrier may terminate (mate/stalemate/repetition) before the payload fits.
// That is a capacity limit, not an interop failure, so report it as a skip.
const TOO_LONG = /Game ended/;

function gen(dir, cases) {
  const out = [];
  cases.forEach((c, i) => {
    const chess = c.startFen ? new Chess(c.startFen) : new Chess();
    try {
      const bytes = textToBytes(c.text);
      if (c.codec === 'arithmetic') encodeIntoGame(chess, bytes, optsFor(c), () => {});
      else encodeIntoGameInteger(chess, bytes, () => {});
    } catch (e) {
      if (!TOO_LONG.test(e.message)) throw e;
      out.push({ i, skipped: 'capacity' });
      return;
    }
    const pgn = pgnOf(chess, c);
    writeFileSync(join(dir, `js_${i}.pgn`), pgn);
    out.push({ i, skipped: null, sans: chess.history(), pgn });
  });
  writeFileSync(join(dir, 'js_result.json'), JSON.stringify(out, null, 2));
}

function dec(dir, cases) {
  const out = [];
  cases.forEach((c, i) => {
    if (existsSync(join(dir, `py_${i}.skip`))) {
      out.push({ i, ok: null, skipped: 'capacity' });
      return;
    }
    let record;
    try {
      const pgn = readFileSync(join(dir, `py_${i}.pgn`), 'utf8');
      const bytes = c.codec === 'arithmetic' ? decodeGame(pgn, optsFor(c)) : decodeGameInteger(pgn);
      record = { i, ok: true, text: bytesToText(bytes) };
    } catch (e) {
      if (TOO_LONG.test(e.message)) record = { i, ok: null, skipped: 'capacity' };
      else record = { i, ok: false, error: e.message };
    }
    out.push(record);
  });
  writeFileSync(join(dir, 'js_decoded.json'), JSON.stringify(out, null, 2));
}

const [mode, dir, specPath] = process.argv.slice(2);
if (!mode || !dir || !specPath) {
  console.error('usage: node interop.mjs <gen|dec> <dir> <spec.json>');
  process.exit(2);
}
const spec = JSON.parse(readFileSync(specPath, 'utf8'));
if (mode === 'gen') gen(dir, spec);
else if (mode === 'dec') dec(dir, spec);
else {
  console.error(`unknown mode: ${mode}`);
  process.exit(2);
}
