// Chess steganography codec — exact arithmetic coding over legal-move buckets.
//
// The secret message is treated as a binary fraction p = 0.<bits>. Every
// position's legal moves split the current interval [lo, hi) into
// variable-width buckets, with optional pawn-push steering applied identically
// on BOTH sides. The encoder narrows into the bucket containing p; the decoder
// replays the moves and emits the bits of p as the interval renormalizes.
// Round-trip is exact for any message.
//
// State is (lo, hi, D) BigInt with real interval [lo/D, hi/D).

import { Chess, DEFAULT_POSITION } from './vendor/chess.js';

const HEADER_BITS = 32n; // big-endian byte length prefix

export function textToBytes(text) {
  return Array.from(new TextEncoder().encode(text));
}

export function bytesToText(bytes) {
  return new TextDecoder().decode(new Uint8Array(bytes));
}

export function bytesToBits(bytes) {
  const bits = [];
  for (const b of bytes) {
    for (let i = 7; i >= 0; i--) bits.push((b >> i) & 1);
  }
  return bits;
}

export function bitsToBytes(bits) {
  const bytes = [];
  for (let i = 0; i + 7 < bits.length; i += 8) {
    let b = 0;
    for (let j = 0; j < 8; j++) b = (b << 1) | bits[i + j];
    bytes.push(b);
  }
  return bytes;
}

// floor(log2(n)) for n >= 1, computed with integer ops. Math.log2 is correct
// at powers of two but the floor() of a fractional result can sit on the wrong
// side of an integer boundary for large n, which would shift every subsequent
// move index in the integer-truncation codec.
export function floorLog2(n) {
  if (n < 1) return 0;
  return 31 - Math.clz32(n);
}

// Canonical move order for every codec in every language: sort by UCI, which
// chess.js exposes as `lan` ("e2e4", "e7e8q", "e1g1") — character-for-character
// the same string python-chess's `uci()` produces. Both libraries generate legal
// moves in their own internal order, so without this the two implementations
// would map the same bitstream onto different games.
function byLan(a, b) {
  return a.lan < b.lan ? -1 : a.lan > b.lan ? 1 : 0;
}

function canonicalMoves(chess) {
  return chess.moves({ verbose: true }).sort(byLan);
}

// Legal moves in canonical order with optional pawn-push steering (the paper's
// Active Environment Steering): within the first `window` plies of the carrier
// movetext, legal pawn pushes get weight 1+beta (captures excluded). Returns
// { moves, weights, total, cum } with cum[i] = sum of weights before index i.
//
// The window is keyed on the move's index in the carrier movetext, which is
// what chess.js's history length already is. Do not use an absolute ply: for a
// board loaded from a FEN, python-chess's board.ply() continues from the FEN's
// move number while this counts from 0, and the two would disagree.
export function weightedMoves(chess, opts) {
  const o = typeof opts === 'boolean' ? { steering: opts } : opts || {};
  const beta = o.beta ?? 1;
  const window = o.window ?? 10;
  // beta and window scale bucket *widths*, and the coder below multiplies them
  // into BigInt state. A fractional value makes the bucket total a non-integer,
  // which BigInt() rejects with an opaque RangeError from deep inside narrow().
  // Catch it here with a message that names the actual problem. (In the Python
  // mirror the same mistake is worse: floats leak into Coder.D and the exact
  // integer coder degrades to floating point, dying with OverflowError on a
  // realistic payload.)
  if (!Number.isInteger(beta)) {
    throw new Error(`beta must be a whole number (got ${beta}). Fractional weights break the coder.`);
  }
  if (!Number.isInteger(window) || window < 1) {
    throw new Error(`window must be a whole number >= 1 (got ${window}).`);
  }
  const ply = chess.history().length;
  const steer = o.steering && ply < window;
  const moves = canonicalMoves(chess);
  const weights = moves.map((m) => {
    const push = m.piece === 'p' && !m.captured;
    return steer && push ? 1 + beta : 1;
  });
  const cum = [0];
  for (let i = 0; i < weights.length; i++) cum.push(cum[i] + weights[i]);
  return { moves, weights, total: cum[cum.length - 1], cum };
}

export class Coder {
  constructor() {
    this.lo = 0n;
    this.hi = 1n;
    this.D = 1n;
  }

  // Narrow interval into bucket i of the weighted list.
  narrow(wm, i) {
    const W = this.hi - this.lo;
    const lo = this.lo * BigInt(wm.total) + BigInt(wm.cum[i]) * W;
    const hi = lo + BigInt(wm.weights[i]) * W;
    this.lo = lo;
    this.hi = hi;
    this.D *= BigInt(wm.total);
  }

  // Emit (decode) or consume (encode) renormalization bits until straddling.
  // Each emitted bit rescales the real interval by 2 (D unchanged).
  renormalize(sink) {
    let n = 0;
    for (;;) {
      if (this.hi * 2n <= this.D) {
        sink(0);
        this.lo *= 2n;
        this.hi *= 2n;
        n++;
      } else if (this.lo * 2n >= this.D) {
        sink(1);
        this.lo = this.lo * 2n - this.D;
        this.hi = this.hi * 2n - this.D;
        n++;
      } else {
        return n;
      }
    }
  }
}

// Encode message bytes into a game played on `chess` (mutates it) with the
// true arithmetic coder. opts: { steering, beta, window }. onMove(moveObj,
// bitsSoFar) fires per ply. Throws if the game ends before the message is
// fully embedded.
export function encodeIntoGame(chess, messageBytes, opts, onMove) {
  // 32-bit big-endian byte-length header, payload bits, then a sentinel 1.
  // The sentinel keeps the remainder fraction p strictly positive so the
  // coder never locks onto bucket 0 with an all-zero tail (which cycles the
  // game into a threefold-repetition draw before the last bits emit).
  const header = [];
  let len = messageBytes.length;
  for (let i = 31; i >= 0; i--) header.push((len >> i) & 1);
  const bits = header.concat(bytesToBits(messageBytes), [1]);
  const nBits = BigInt(bits.length);
  // Remainder fraction p = P/Q. Renormalization consumes p's leading bits,
  // so Q shrinks by one bit per emitted bit; once exhausted (Q === 1) the
  // remaining expansion is trailing zeros and p stays 0.
  let P = 0n;
  for (const b of bits) P = (P << 1n) | BigInt(b);
  let Q = 1n << nBits;
  const targetBits = bits.length - 1; // sentinel not counted

  const coder = new Coder();
  let emitted = 0;

  for (;;) {
    if (emitted >= targetBits)
      return {
        plies: chess.history().length,
        bitsUsed: emitted,
        avgBitsPerMove: emitted / Math.max(1, chess.history().length),
      };
    if (chess.isGameOver()) {
      throw new Error(
        `Game ended after ${chess.history().length} plies with ${emitted}/${targetBits} bits embedded — message too long for this carrier game.`
      );
    }
    const wm = weightedMoves(chess, opts);
    let i = 0;
    if (wm.total > 1) {
      // r = floor((p - lo/D) * D*total/W): position of p within the buckets
      const X = (P * coder.D - coder.lo * Q) * BigInt(wm.total);
      const WQ = (coder.hi - coder.lo) * Q;
      if (X < 0n || X >= WQ * BigInt(wm.total)) throw new Error('interval invariant broken');
      const r = X / WQ; // BigInt floor div
      while (i + 1 < wm.moves.length && wm.cum[i + 1] <= r) i++;
    }
    coder.narrow(wm, i);
    chess.move(wm.moves[i]);
    coder.renormalize((bit) => {
      emitted++;
      if (bit === 0) {
        if (Q > 1n) Q >>= 1n;
      } else {
        P -= Q >> 1n;
        Q >>= 1n;
      }
    });
    if (onMove) onMove(wm.moves[i], emitted);
  }
}

// Parse a carrier PGN into { headers, sans, board }. `board` is a fresh replay
// board positioned at the game's SetUp/FEN (or the standard start). Throws if
// the text is not a PGN or carries no moves.
function loadCarrier(pgn) {
  const replay = new Chess();
  let sans;
  try {
    replay.loadPgn(pgn, { strict: false });
    sans = replay.history();
  } catch {
    throw new Error('Could not parse PGN.');
  }
  if (!sans.length) throw new Error('Could not parse PGN: no moves found.');
  const headers = replay.getHeaders();
  const board = new Chess();
  if (headers.FEN && headers.FEN !== DEFAULT_POSITION) board.load(headers.FEN);
  return { headers, sans, board };
}

// Streaming decoder. Feed SAN moves in game order; the decoder keeps its own
// board (starting from the game's SetUp/FEN if any) so weighted move lists
// are computed from the position before each move. result() returns the
// decoded bytes once complete, null while incomplete.
export function makeDecoder(pgn, opts) {
  const { sans, board: work } = loadCarrier(pgn);
  const coder = new Coder();
  const bits = [];
  let expectedLen = null; // bytes
  let ply = 0;
  const needBits = () =>
    expectedLen === null ? Number(HEADER_BITS) : Number(HEADER_BITS) + expectedLen * 8;

  return {
    totalPlies: sans.length,
    plyIndex() {
      return ply;
    },
    // Feed the next ply (or all remaining). Returns bytes once complete.
    feed(count) {
      const n = count === undefined ? sans.length - ply : Math.min(count, sans.length - ply);
      for (let k = 0; k < n; k++) {
        const san = sans[ply++];
        const wm = weightedMoves(work, opts);
        let idx = -1;
        for (let i = 0; i < wm.moves.length; i++) {
          if (wm.moves[i].san === san) {
            idx = i;
            break;
          }
        }
        if (idx === -1) throw new Error('Illegal or unmatched move in carrier game: ' + san);
        coder.narrow(wm, idx);
        // Replay the move object we just matched rather than re-parsing the
        // SAN, so an ambiguous token can never resolve to a different move.
        work.move(wm.moves[idx]);
        coder.renormalize((bit) => bits.push(bit));
        if (expectedLen === null && bits.length >= Number(HEADER_BITS)) {
          let len = 0;
          for (let j = 0; j < 32; j++) len = len * 2 + bits[j];
          if (len > 1_000_000)
            throw new Error('No hidden message here — the game decodes to an implausible message length. Either this is an ordinary game, or it was encoded with a different codec.');
          expectedLen = len;
        }
        if (this.done())
          return bitsToBytes(bits.slice(Number(HEADER_BITS), Number(HEADER_BITS) + expectedLen * 8));
      }
      return null;
    },
    done() {
      return expectedLen !== null && bits.length >= Number(HEADER_BITS) + expectedLen * 8;
    },
    progress() {
      return { bits: bits.length, needed: needBits(), bytes: expectedLen };
    },
    board() {
      return work;
    },
  };
}

// Convenience: decode an entire PGN at once.
export function decodeGame(pgn, opts) {
  const dec = makeDecoder(pgn, opts);
  const result = dec.feed();
  if (result === null) throw new Error('Game ended before a complete message was embedded.');
  return result;
}
// ---------------------------------------------------------------------------
// Integer-truncation baseline (the Cao-style encoder the paper compares
// against): at each ply take k = floor(log2 N) bits, interpret them as an
// index into the legal-move list, and play that move. The fractional part of
// log2 N is discarded — this is the capacity loss the true coder recovers.
// ---------------------------------------------------------------------------

// Shared bit-cursor over the message bitstream (header + payload).
function bitStream(messageBytes) {
  const header = [];
  let len = messageBytes.length;
  for (let i = 31; i >= 0; i--) header.push((len >> i) & 1);
  const bits = header.concat(bytesToBits(messageBytes));
  return { bits, pos: 0 };
}
export function encodeIntoGameInteger(chess, messageBytes, onMove) {
  const stream = bitStream(messageBytes);
  const targetBits = stream.bits.length;
  for (;;) {
    if (stream.pos >= targetBits)
      return {
        plies: chess.history().length,
        bitsUsed: stream.pos,
        avgBitsPerMove: stream.pos / Math.max(1, chess.history().length),
      };
    if (chess.isGameOver()) {
      throw new Error(
        `Game ended after ${chess.history().length} plies with ${stream.pos}/${targetBits} bits embedded — message too long for this carrier game.`
      );
    }
    const moves = canonicalMoves(chess);
    const k = floorLog2(moves.length);
    if (k <= 0) {
      chess.move(moves[0]); // forced move, carries no information
      if (onMove) onMove(moves[0], stream.pos);
      continue;
    }
    let v = 0;
    for (let j = 0; j < k; j++) {
      const bit = stream.pos < targetBits ? stream.bits[stream.pos++] : 0;
      v = (v << 1) | bit;
    }
    // v < 2^k <= moves.length, so the index is always legal.
    chess.move(moves[v]);
    if (onMove) onMove(moves[v], stream.pos);
  }
}

export function decodeGameInteger(pgn) {
  const { sans, board: work } = loadCarrier(pgn);
  const bits = [];
  let expectedLen = null;
  for (const san of sans) {
    const moves = canonicalMoves(work);
    let idx = -1;
    for (let i = 0; i < moves.length; i++) {
      if (moves[i].san === san) {
        idx = i;
        break;
      }
    }
    if (idx === -1) throw new Error('Illegal or unmatched move in carrier game: ' + san);
    const k = floorLog2(moves.length);
    for (let j = k - 1; j >= 0; j--) bits.push((idx >> j) & 1);
    work.move(moves[idx]);
    if (expectedLen === null && bits.length >= 32) {
      let len = 0;
      for (let j = 0; j < 32; j++) len = len * 2 + bits[j];
      if (len > 1_000_000)
        throw new Error('No hidden message here — the game decodes to an implausible message length. Either this is an ordinary game, or it was encoded with a different codec.');
      expectedLen = len;
    }
    if (expectedLen !== null && bits.length >= 32 + expectedLen * 8) {
      const out = bits.slice(32, 32 + expectedLen * 8);
      return bitsToBytes(out);
    }
  }
  throw new Error('Game ended before a complete message was embedded.');
}
