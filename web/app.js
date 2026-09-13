// Chess Steg — interactive UI over the stego codec (stego.js).
import { Chess } from './vendor/chess.js';
import {
  encodeIntoGame,
  decodeGame,
  encodeIntoGameInteger,
  decodeGameInteger,
  makeDecoder,
  textToBytes,
  bytesToText,
} from './stego.js';
import { targetClasses } from './dom.js';
import { optsFromPgn } from './headers.js';
import { payloadRejection } from './limits.js';

const GLYPH = { k: '\u265A', q: '\u265B', r: '\u265C', b: '\u265D', n: '\u265E', p: '\u265F' };
const FILES = 'abcdefgh';

const $ = (id) => document.getElementById(id);

// ── App state ─────────────────────────────────────────────
let board = new Chess();
let flipped = false;
let selected = null; // square string
let busy = false; // auto-play in progress
let playGen = 0; // invalidates in-flight playback loops when reset/stop happens
let playTimer = null;
let lastMoveSquares = [];
let embeddedBits = 0; // bits counted by the latest codec operation
let ledgerChars = []; // [{char, state: 'plain'|'redacted'|'revealed'}]

const boardEl = $('board');
const moveLogEl = $('move-log');
const ledgerEl = $('ledger');

// ── Board rendering ───────────────────────────────────────
function renderBoard() {
  const pos = board.board(); // [rank8..rank1][file a..h]
  const checkSquare = board.inCheck() ? kingSquare(board.turn()) : null;
  const ranks = flipped ? [...pos].reverse().map((r) => [...r].reverse()) : pos.map((r) => [...r]);
  boardEl.innerHTML = '';
  ranks.forEach((row, ri) => {
    row.forEach((cell, ci) => {
      const rankIdx = flipped ? ri : 7 - ri;
      const fileIdx = flipped ? 7 - ci : ci;
      const square = FILES[fileIdx] + (rankIdx + 1);
      const el = document.createElement('div');
      el.className = `square ${(rankIdx + fileIdx) % 2 === 1 ? 'light' : 'dark'}`;
      el.dataset.square = square;
      if (cell) {
        const span = document.createElement('span');
        span.className = 'piece';
        span.textContent = GLYPH[cell.type];
        el.appendChild(span);
        el.classList.add(cell.color === 'w' ? 'w-piece' : 'b-piece');
      }
      if (selected === square) el.classList.add('selected');
      if (lastMoveSquares.includes(square)) el.classList.add('last-move');
      if (checkSquare === square) el.classList.add('in-check');
      if (rankIdx === 0) addCoord(el, 'file', FILES[fileIdx]);
      if (fileIdx === 0) addCoord(el, 'rank', String(rankIdx + 1));
      el.addEventListener('click', () => onSquareClick(square));
      boardEl.appendChild(el);
    });
  });
}

function addCoord(el, kind, text) {
  const c = document.createElement('span');
  c.className = `coord ${kind}`;
  c.textContent = text;
  el.appendChild(c);
}

function kingSquare(color) {
  for (const row of board.board()) {
    for (const cell of row) {
      if (cell && cell.type === 'k' && cell.color === color) return cell.square;
    }
  }
  return null;
}

// CSS classes for a legal-move target square live in dom.js so they can be
// unit-tested without a browser.
function renderTargets() {
  boardEl.querySelectorAll('.square').forEach((el) => el.classList.remove('target', 'capture'));
  if (!selected) return;
  for (const m of board.moves({ square: selected, verbose: true })) {
    const el = boardEl.querySelector(`[data-square="${m.to}"]`);
    if (el) el.classList.add(...targetClasses(m));
  }
}

// ── Manual play ───────────────────────────────────────────
function onSquareClick(square) {
  if (busy) return;
  const piece = board.get(square);
  if (selected) {
    const move = board
      .moves({ square: selected, verbose: true })
      .find((m) => m.to === square);
    if (move) {
      if (move.promotion) {
        askPromotion((type) => applyManual({ ...move, promotion: type }));
      } else {
        applyManual(move);
      }
      return;
    }
    selected = piece && piece.color === board.turn() ? square : null;
  } else if (piece && piece.color === board.turn()) {
    selected = square;
  }
  renderBoard();
  renderTargets();
}

function applyManual(move) {
  const played = board.move(move);
  selected = null;
  lastMoveSquares = played ? [played.from, played.to] : [];
  renderBoard();
  renderTargets();
  renderMoveLog();
  updateStatus();
}

function askPromotion(cb) {
  const modal = $('promo');
  modal.hidden = false;
  modal.querySelectorAll('button').forEach((b) => {
    b.onclick = () => {
      modal.hidden = true;
      cb(b.dataset.promo);
    };
  });
}

// ── Move log ──────────────────────────────────────────────
let moveTaps = []; // bits consumed per ply, aligned with history
function renderMoveLog() {
  const hist = board.history();
  moveLogEl.innerHTML = '';
  if (!hist.length) {
    moveLogEl.innerHTML = '<span class="empty">No moves yet — the board is a blank page.</span>';
    return;
  }
  for (let i = 0; i < hist.length; i += 2) {
    const line = document.createElement('div');
    const num = document.createElement('span');
    num.className = 'mnum';
    num.textContent = `${i / 2 + 1}.`;
    line.appendChild(num);
    for (let j = i; j < Math.min(i + 2, hist.length); j++) {
      const span = document.createElement('span');
      span.textContent = hist[j];
      const tap = moveTaps[j];
      if (tap > 0) {
        const t = document.createElement('span');
        t.className = 'mtap';
        t.textContent = `+${tap}b`;
        span.appendChild(t);
      }
      line.appendChild(span);
      line.appendChild(document.createTextNode(' '));
    }
    moveLogEl.appendChild(line);
  }
  moveLogEl.scrollTop = moveLogEl.scrollHeight;
}

// ── Ledger ────────────────────────────────────────────────
function renderLedger() {
  ledgerEl.innerHTML = '';
  if (!ledgerChars.length) {
    const hint = document.createElement('span');
    hint.className = 'hint';
    hint.textContent = 'Idle.';
    ledgerEl.appendChild(hint);
    return;
  }
  ledgerChars.forEach((tile, idx) => {
    const el = document.createElement('span');
    el.className =
      'tile' +
      (tile.char === ' ' ? ' space' : '') +
      (tile.state === 'redacted' ? ' redacted' : '') +
      (tile.state === 'revealed' ? ' revealing' : '');
    el.textContent = tile.char === ' ' ? '\u00B7' : tile.char;
    el.title = `char ${idx + 1}`;
    ledgerEl.appendChild(el);
  });
}

function ledgerForEncode(text, bitsNow) {
  const chars = [...text];
  while (ledgerChars.length < chars.length) ledgerChars.push({ char: '', state: 'plain' });
  chars.forEach((ch, i) => {
    const tile = ledgerChars[i];
    tile.char = ch;
    // header (32 bits) + 8 bits per character
    tile.state = bitsNow >= 32 + 8 * (i + 1) ? 'redacted' : 'plain';
  });
  renderLedger();
}

function ledgerForDecode(text, revealed) {
  const chars = [...text];
  ledgerChars = chars.map((ch, i) => ({
    char: ch,
    state: i < revealed ? 'revealed' : 'redacted',
  }));
  renderLedger();
}

// ── Status line ───────────────────────────────────────────
function updateStatus(note = '') {
  $('st-ply').textContent = `ply ${board.history().length}`;
  $('st-turn').textContent = board.turn() === 'w' ? 'white to move' : 'black to move';
  $('st-bits').textContent = `${embeddedBits} bits embedded`;
  $('st-bpm').textContent =
    board.history().length > 0 && embeddedBits > 0
      ? `${(embeddedBits / board.history().length).toFixed(2)} bits/move`
      : '— bits/move';
  $('st-state').textContent = note;
  if (board.isGameOver()) {
    $('st-state').textContent = board.isCheckmate()
      ? 'checkmate'
      : board.isStalemate()
        ? 'stalemate'
        : 'draw';
  }
}

function toast(msg) {
  const el = $('toast');
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (el.hidden = true), 4200);
}

// ── Options / headers ─────────────────────────────────────
function readOpts() {
  return {
    steering: $('opt-steer').checked,
    beta: parseFloat($('opt-beta').value) || 1,
    window: parseInt($('opt-window').value, 10) || 10,
    arithmetic: $('opt-codec').checked,
  };
}

const START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

function stampHeaders(chess, opts, startFen) {
  chess.setHeader('Event', 'Steganographic chess');
  if (startFen && startFen !== START_FEN) {
    // Carrier game started from a custom position — record it or decoding breaks.
    chess.setHeader('SetUp', '1');
    chess.setHeader('FEN', startFen);
  }
  chess.setHeader('StegoCodec', opts.arithmetic ? 'arithmetic' : 'integer');
  chess.setHeader('StegoMode', opts.steering ? 'steered' : 'plain');
  if (opts.steering) {
    chess.setHeader('StegoBeta', String(opts.beta));
    chess.setHeader('StegoWindow', String(opts.window));
  }
}


// ── Playback helpers ──────────────────────────────────────
function paceMs() {
  return parseInt($('speed').value, 10) || 380;
}

function stopPlayback() {
  playGen++;
  if (playTimer) {
    clearInterval(playTimer);
    playTimer = null;
  }
  busy = false;
  $('btn-encode').disabled = false;
  $('btn-decode').disabled = false;
}

// Occluded windows throttle setTimeout/setInterval to ~1/s, which would
// stall the playback loops — pace with MessageChannel yields instead, which
// Chrome never throttles.
function yieldTick() {
  return new Promise((res) => {
    const ch = new MessageChannel();
    ch.port1.onmessage = () => {
      ch.port1.close();
      res();
    };
    ch.port2.postMessage(0);
  });
}

async function sleep(ms) {
  if (ms <= 0) return;
  const target = performance.now() + ms;
  while (performance.now() < target) await yieldTick();
}

// ── Encode flow ───────────────────────────────────────────

async function runEncode() {
  if (busy) return;
  // Clear any previous carrier before doing anything else. Otherwise a refused
  // attempt (empty message, oversized payload, finished game) leaves the last
  // successful PGN sitting in the output box looking like the result of this
  // one, and the obvious next action is to copy it and send the wrong game.
  $('encode-result').hidden = true;
  const text = $('secret').value;
  if (!text) {
    toast('Write a message first — an empty page hides nothing.');
    return;
  }
  if (board.isGameOver()) {
    toast('This game is over. Start a new game to hide a message.');
    return;
  }
  // Refuse an impossible payload before spending 50 seconds of main thread
  // finding out. See web/limits.js for the measured cost curve.
  const rejection = payloadRejection(textToBytes(text).length);
  if (rejection) {
    toast(rejection);
    return;
  }
  const opts = readOpts();
  const startFen = board.fen();
  const gen = ++playGen;
  busy = true;
  $('btn-encode').disabled = true;
  $('encode-progress').hidden = false;
  setBar('encode', 0, 'planning the carrier game…');

  // Plan the whole embedding on a scratch board, then replay on the visible one.
  const scratch = new Chess();
  scratch.load(board.fen());
  const plan = [];
  let planned;
  try {
    planned =
      opts.arithmetic
        ? encodeIntoGame(scratch, textToBytes(text), opts, (mv, bits) => plan.push({ mv, bits }))
        : encodeIntoGameInteger(scratch, textToBytes(text), (mv, bits) => plan.push({ mv, bits }));
  } catch (e) {
    stopPlayback();
    $('encode-progress').hidden = true;
    toast(e.message);
    return;
  }

  ledgerChars = [];
  moveTaps = [];
  let prevBits = 0;
  for (let i = 0; i < plan.length; i++) {
    const step = plan[i];
    board.move(step.mv);
    const tap = step.bits - prevBits;
    prevBits = step.bits;
    embeddedBits = step.bits;
    moveTaps.push(tap);
    lastMoveSquares = [step.mv.from, step.mv.to];
    renderBoard();
    renderMoveLog();
    ledgerForEncode(text, step.bits);
    setBar('encode', (i + 1) / plan.length, `ply ${i + 1}/${plan.length} — ${step.bits} bits buried`);
    updateStatus();
    await sleep(paceMs());
  }
  stopPlayback();

  stampHeaders(scratch, opts, startFen);
  $('pgn-out').value = scratch.pgn();
  $('encode-result').hidden = false;
  $('encode-progress-text').textContent = `sealed: ${planned.bitsUsed} bits in ${planned.plies} plies (${planned.avgBitsPerMove.toFixed(2)} bits/move)`;
  updateStatus('message hidden — copy the PGN');
}

// ── Decode flow ───────────────────────────────────────────
async function runDecode() {
  if (busy) return;
  const pgn = $('pgn-in').value.trim();
  if (!pgn) {
    toast('Paste a carrier game first.');
    return;
  }
  const opts = optsFromPgn(pgn);
  const gen = ++playGen;
  busy = true;
  $('btn-decode').disabled = true;
  $('decode-result').hidden = true;
  $('decode-progress').hidden = false;
  const modeNote = opts.modeStated
    ? ''
    : ' · no StegoMode header, assuming plain (matches codec.py)';
  setBar('decode', 0, `replaying with ${opts.steering ? `steering (β=${opts.beta}, W=${opts.window})` : 'no steering'}${modeNote}…`);

  // Validate + get SAN list.
  const probe = new Chess();
  try {
    probe.loadPgn(pgn, { strict: false });
  } catch {
    stopPlayback();
    $('decode-progress').hidden = true;
    toast('Could not read that PGN.');
    return;
  }
  const sans = probe.history();
  if (!sans.length) {
    stopPlayback();
    $('decode-progress').hidden = true;
    toast('That PGN has no moves to decode.');
    return;
  }

  // Fresh board replay of the carrier game (honouring a SetUp/FEN start).
  board = new Chess();
  const startHeaders = probe.getHeaders();
  if (startHeaders.FEN && startHeaders.FEN !== START_FEN) {
    board.load(startHeaders.FEN);
  }
  selected = null;
  lastMoveSquares = [];
  moveTaps = [];
  ledgerChars = [];
  renderLedger();

  let decoder = null;
  let result = null;
  let error = null;
  if (opts.arithmetic) {
    try {
      decoder = makeDecoder(pgn, opts);
    } catch (e) {
      stopPlayback();
      $('decode-progress').hidden = true;
      toast(e.message);
      return;
    }
  }

  for (let i = 0; i < sans.length; i++) {
    const san = sans[i];
    board.move(san);
    const hist = board.history({ verbose: true });
    lastMoveSquares = [hist[hist.length - 1].from, hist[hist.length - 1].to];
    if (opts.arithmetic && decoder && !decoder.done()) {
      try {
        const r = decoder.feed(1);
        if (r) result = r;
      } catch (e) {
        error = e;
      }
      const p = decoder.progress();
      embeddedBits = p.bits;
      moveTaps.push(Math.max(0, p.bits - (moveTaps.reduce((a, b) => a + b, 0))));
      setBar('decode', i / sans.length, `ply ${i + 1}/${sans.length} — ${p.bits} bits tapped`);
    } else {
      moveTaps.push(0);
      setBar('decode', i / sans.length, `ply ${i + 1}/${sans.length}`);
    }
    renderBoard();
    renderMoveLog();
    updateStatus();
    await sleep(Math.min(paceMs(), 160));
    if (gen !== playGen) return; // playback was stopped
  }

  if (!opts.arithmetic) {
    try {
      result = decodeGameInteger(pgn);
    } catch (e) {
      error = e;
    }
  }

  stopPlayback();
  $('decode-progress').hidden = true;
  if (error || !result) {
    toast(error ? error.message : 'No complete message found in this game.');
    updateStatus('nothing recovered');
    return;
  }
  const text = bytesToText(result);
  ledgerForDecode(text, 0);
  // staggered reveal animation
  const chars = [...text];
  for (let r = 0; r <= chars.length; r++) {
    ledgerForDecode(text, r);
    await sleep(28);
  }
  $('decoded-text').textContent = text;
  $('decode-stats').textContent = `${result.length} bytes recovered over ${sans.length} plies.`;
  $('decode-result').hidden = false;
  updateStatus('message recovered');
}

function setBar(which, frac, label) {
  document.getElementById(`${which}-bar`).style.width = `${Math.round(frac * 100)}%`;
  if (label) document.getElementById(`${which}-progress-text`).textContent = label;
}

// ── Wiring ────────────────────────────────────────────────
function switchTab(which) {
  const hide = which === 'hide';
  $('tab-hide').classList.toggle('is-active', hide);
  $('tab-reveal').classList.toggle('is-active', !hide);
  $('view-hide').hidden = !hide;
  $('view-reveal').hidden = hide;
}

$('tab-hide').addEventListener('click', () => switchTab('hide'));
$('tab-reveal').addEventListener('click', () => switchTab('reveal'));
$('btn-encode').addEventListener('click', runEncode);
$('btn-decode').addEventListener('click', runDecode);
$('btn-new').addEventListener('click', () => {
  stopPlayback();
  board = new Chess();
  selected = null;
  lastMoveSquares = [];
  embeddedBits = 0;
  moveTaps = [];
  ledgerChars = [];
  $('encode-progress').hidden = true;
  $('encode-result').hidden = true;
  $('decode-progress').hidden = true;
  renderAll();
  updateStatus('fresh board');
});
$('btn-undo').addEventListener('click', () => {
  if (busy) return;
  board.undo();
  moveTaps.pop();
  lastMoveSquares = [];
  const hist = board.history({ verbose: true });
  if (hist.length) lastMoveSquares = [hist[hist.length - 1].from, hist[hist.length - 1].to];
  renderAll();
  updateStatus();
});
$('btn-flip').addEventListener('click', () => {
  flipped = !flipped;
  renderBoard();
});
$('btn-copy').addEventListener('click', async () => {
  const v = $('pgn-out').value;
  try {
    await navigator.clipboard.writeText(v);
    toast('PGN copied to clipboard.');
  } catch {
    $('pgn-out').select();
    document.execCommand('copy');
    toast('PGN copied.');
  }
});

function renderAll() {
  renderBoard();
  renderTargets();
  renderMoveLog();
  renderLedger();
  updateStatus();
}

renderAll();
