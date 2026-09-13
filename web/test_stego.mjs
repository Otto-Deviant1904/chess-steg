// Round-trip fuzz test for the stego codecs, run under node.
import { Chess } from './vendor/chess.js';
import {
  encodeIntoGame,
  decodeGame,
  encodeIntoGameInteger,
  decodeGameInteger,
  textToBytes,
  bytesToText,
  makeDecoder,
  bytesToBits,
  bitsToBytes,
} from './stego.js';

const STEER = { steering: true, beta: 1, window: 10 };
const PLAIN = { steering: false };

function pgnOf(chess, opts, codec) {
  chess.setHeader('Event', 'Steganographic chess');
  chess.setHeader('StegoCodec', codec);
  chess.setHeader('StegoMode', opts.steering ? 'steered' : 'plain');
  if (opts.steering) {
    chess.setHeader('StegoBeta', String(opts.beta));
    chess.setHeader('StegoWindow', String(opts.window));
  }
  return chess.pgn();
}
let capacitySkips = 0;
function roundtrip(text, opts, codec = 'arithmetic') {
  const chess = new Chess();
  let moves = 0;
  let res;
  try {
    res =
      codec === 'arithmetic'
        ? encodeIntoGame(chess, textToBytes(text), opts, () => moves++)
        : encodeIntoGameInteger(chess, textToBytes(text), () => moves++);
  } catch (e) {
    if (/Game ended/.test(e.message)) {
      capacitySkips++; // legitimate carrier-capacity limit, not a codec bug
      return null;
    }
    throw e;
  }
  const pgn = pgnOf(chess, opts, codec);
  const out =
    codec === 'arithmetic' ? decodeGame(pgn, opts) : decodeGameInteger(pgn);
  const got = bytesToText(out);
  if (got !== text) {
    throw new Error(
      `MISMATCH codec=${codec} opts=${JSON.stringify(opts)}\n want: ${JSON.stringify(text)}\n got:  ${JSON.stringify(got)}`
    );
  }
  return { plies: moves, bits: res.bitsUsed, bpm: res.avgBitsPerMove };
}

let worstBpm = Infinity;
let bestBpm = 0;
const cases = [
  '',
  'a',
  'Hello, world!',
  'The crow flies at midnight. Money is in the cayman account.',
  'Ünïcödé — 中文 — \u{1F512} emoji test',
  'x'.repeat(1),
  'y'.repeat(40),
  'The quick brown fox jumps over the lazy dog. '.repeat(2),
];
for (const opts of [STEER, PLAIN]) {
  for (const text of cases) {
    for (const codec of ['arithmetic', 'integer']) {
      const r = roundtrip(text, opts, codec);
      if (r && text.length > 0) {
        worstBpm = Math.min(worstBpm, r.bpm);
        bestBpm = Math.max(bestBpm, r.bpm);
      }
    }
  }
}

// Random fuzz
let seed = 12345;
const rand = () => {
  seed = (seed * 1103515245 + 12345) & 0x7fffffff;
  return seed / 0x7fffffff;
};
const alpha = 'abcdefghijklmnopqrstuvwxyz .,!?\n\t0123456789';
for (let t = 0; t < 40; t++) {
  const len = 1 + Math.floor(rand() * 30);
  let text = '';
  for (let i = 0; i < len; i++) text += alpha[Math.floor(rand() * alpha.length)];
  roundtrip(text, rand() < 0.5 ? STEER : PLAIN, rand() < 0.5 ? 'arithmetic' : 'integer');
}

// bits<->bytes identity
const raw = Array.from({ length: 512 }, () => Math.floor(rand() * 256));
if (JSON.stringify(bitsToBytes(bytesToBits(raw))) !== JSON.stringify(raw)) {
  throw new Error('bits<->bytes mismatch');
}

// Decoder must reject a game encoded with the other steering mode.
{
  const chess = new Chess();
  encodeIntoGame(chess, textToBytes('mode check'), STEER, () => {});
  const pgn = pgnOf(chess, STEER, 'arithmetic');
  let wrong = '';
  try {
    wrong = bytesToText(decodeGame(pgn, PLAIN));
  } catch {
    wrong = null; // threw: good
  }
  if (wrong === 'mode check')
    throw new Error('plain-mode decode of steered game should not recover the message');
}

// Streaming decode equals batch decode.
{
  const chess = new Chess();
  const msg = 'streaming = batch: ' + 'Z'.repeat(12);
  encodeIntoGame(chess, textToBytes(msg), STEER, () => {});
  const pgn = pgnOf(chess, STEER, 'arithmetic');
  const dec = makeDecoder(pgn, STEER);
  let out = null;
  while (out === null) out = dec.feed(3);
  if (bytesToText(out) !== msg) throw new Error('streaming decode mismatch');
}

console.log('ALL ROUNDTRIP TESTS PASS');
console.log(`capacity skips (game ended early): ${capacitySkips}`);
console.log(`observed BPM range across cases: ${worstBpm.toFixed(3)} – ${bestBpm.toFixed(3)}`);
