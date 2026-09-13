# chess-steg — steganography over chess games

Hide a secret message inside a legal chess game.

The message is **arithmetic-coded into the sequence of moves**. To anyone
watching, it is an ordinary legal game. The recipient replays the moves and
recovers the bitstream exactly — no shared key, no key exchange, no file
format, just a PGN.

```
[Event "Steganographic chess"]
[StegoCodec "arithmetic"]
[StegoMode "steered"]
[StegoBeta "1"]
[StegoWindow "10"]

1. a3 a5 2. Ra2 a4 3. Ra1 b5 4. d3 Qe7 5. d4 Kd7 6. Nf3 Qc5 7. Nfd2 Be7 *
```

Measured over 1,000 real Lichess games, the true arithmetic coder carries
**~4.77 bits per ply** with a **0% decode error rate**. A 56-byte payload
(480 bits including the 32-bit header) takes ~101 plies to embed — about half a
game.

---

## Why this exists

Most game-based steganography maps bits onto moves by taking
`floor(log2(N))` bits per ply, where `N` is the number of legal moves. That
throws away the fractional part of the interval — when a position has 30 legal
moves you spend 4 bits and waste the rest.

This repo implements the alternative: a **true arithmetic coder** over
legal-move buckets, which recovers that fractional capacity, plus **Active
Environment Steering** — deliberately biasing early pawn pushes to enlarge the
branching factor of *later* plies.

| Arm | Mean BPM | Decode error | Plies used | Capacity failure |
|---|---|---|---|---|
| Integer-truncation baseline | 4.231 | 0.000% | 113.6 | 4.8% |
| True arithmetic, plain | 4.749 | 0.000% | 101.2 | 3.7% |
| True arithmetic + steering | **4.766** | **0.000%** | **100.8** | **2.4%** |

Steering beats the integer baseline by **12.64%** BPM (paired *t*(927) = 74.92,
*p* < 1e-300, Cohen's *d* = 2.46) while also embedding in fewer plies. All three
arms recovered every bit of every payload they managed to embed — 0.000% decode
error across 1,000 games.

Full numbers and statistics: [`results/summary.md`](results/summary.md). The
write-up is in [`paper/paper.pdf`](paper/paper.pdf).

---

## Quick start

### Web app — no build, no network, no install

```bash
cd web
python3 -m http.server 8642     # then open http://127.0.0.1:8642
```

- **Hide a message** — type it, press the button, watch the coder play the
  carrier game, then copy the PGN.
- **Reveal a message** — paste a carrier PGN; the codec replays it and recovers
  the text. Coding parameters travel in the PGN headers, so nothing else is
  needed.
- Play moves by hand and start embedding from any position, including FENs.

`web/vendor/chess.js` is vendored, so the page works fully offline. `web/` is
declared as ES modules (`"type": "module"` in `web/package.json`); there are no
npm dependencies and nothing to install or build.

### Python

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

.venv/bin/python -c "
from codec import TrueArithmeticCodec, STEER_OPTS, encode_to_pgn
import chess
pgn, res = encode_to_pgn(chess.Board(), 'attack at dawn', TrueArithmeticCodec(), STEER_OPTS)
print(res.bpm, 'bits/ply over', res.plies, 'plies')
print(TrueArithmeticCodec().decode_pgn(pgn).decode())
"
```

### Tests

```bash
.venv/bin/python -m pytest -q      # codec + JS/Python interop
cd web && node test_stego.mjs      # JS roundtrip fuzz
```

---

## How the codec works

1. The message becomes a bitstream: a 32-bit big-endian byte-length header, the
   UTF-8 payload, then a single sentinel `1` bit. The sentinel keeps the
   coder's remainder fraction strictly positive, so it can never stall in an
   all-zero corner.
2. That bitstream is treated as the point `p = 0.<bits>` in `[0, 1)`.
3. Each ply, the current interval `[lo, hi)` is split into one bucket per legal
   move. With steering on, pawn *pushes* (not captures) in the first `W` plies
   get bucket width `1 + β`. The interval narrows into the bucket containing
   `p`, and that bucket's move is played.
4. Whenever the interval falls entirely within one half of `[0, 1)`, its
   leading bit is emitted and the interval rescales (renormalisation). This is
   where the *fractional* bits come from — exactly what the integer-truncation
   baseline discards.
5. The decoder replays the moves, rebuilds identical bucket lists, and emits the
   same bits as its interval renormalises. The header tells it when to stop.

### Bit-compatibility is enforced, not assumed

Move ordering is the whole game: if the encoder and decoder disagree about the
order of legal moves, the same bits map to a different position and the
message is unrecoverable. Every codec here enumerates legal moves in **one
canonical order** (sorted by UCI / chess.js's `lan`, which are the same string).

[`test_interop.py`](test_interop.py) proves the Python and JavaScript
implementations are interchangeable across an 84-case matrix: identical move
sequences, JS decoding Python carriers, and Python decoding JS carriers. It
covers plain and steered modes, both codecs, UTF-8 and emoji payloads,
all-zero payloads, en-passant, promotion, and FEN-start carriers.

Two real bugs were found by that test and are now regression-tested:

- the integer-truncation baseline was using each chess library's internal
  move order, so the two implementations encoded the same bits into *different
  games*;
- the steering window was keyed on the board's absolute ply, which the two
  libraries compute differently for FEN starts.

---

## Repository layout

| Path | What it is |
|---|---|
| `web/` | The interactive app. No build step, no network, no dependencies. |
| `web/stego.js` | The codec in JavaScript — exact `BigInt` arithmetic coding over legal-move buckets. |
| `web/app.js`, `web/index.html`, `web/style.css` | UI: board rendering, animated encode/decode, PGN import/export. |
| `codec.py` | Python mirror of the codec. The two are bit-compatible. |
| `benchmark.py` | Evaluation harness: baseline vs true coder vs true coder + steering, over a real corpus. Paired *t*-tests, effect sizes, figures. |
| `make_figures.py` | Regenerates the paper's figures. |
| `test_codec.py` | Python roundtrip + invariant tests. |
| `test_interop.py` | Cross-language bit-compatibility proof. |
| `web/test_stego.mjs` | JavaScript roundtrip fuzz. |
| `results/` | Benchmark output: `summary.md`, `benchmark.csv`, and the JSON summaries. |
| `paper/figs/` | The paper's four figures, regenerated by `make_figures.py`. |
| `paper/` | Paper draft (`paper.pdf`) with measured numbers filled in. |
| `examples/` | A committed carrier PGN plus how to decode it. |
| `scripts/fetch_corpus.sh` | Downloads the benchmark corpus (not committed — ~89 MB). |
| `legacy/` | The original proof-of-concept. **Superseded and lossy** — see below. |

### `legacy/`

`legacy/engine.py` and `legacy/experiment.py` are the first proof-of-concept.
They are kept only for history: their decoder does not mirror the encoder's
pawn-weighting, and the encoder samples 10 bits per move, so their round-trip
is lossy. **Use `codec.py` or `web/stego.js` instead.** Nothing else imports
them and no test covers them.

---

## Reproducing the benchmark

The corpus is not committed (~89 MB). Fetch it, then run:

```bash
./scripts/fetch_corpus.sh          # January 2013 standard-rated dump
.venv/bin/python benchmark.py pilot
.venv/bin/python benchmark.py full 1000
```

`pilot` calibrates the payload size (the largest that all three arms embed
successfully in at least 95% of games); `full` runs the 1000-game evaluation
and writes `results/`. Both are seeded, so runs are reproducible.

Per-game rows including full per-ply bit trajectories are ~3 MB and are not
committed. Generate them if you want to rebuild the figures:

```bash
INCLUDE_ROWS=1 .venv/bin/python benchmark.py full 1000
.venv/bin/python make_figures.py
```

---

## PGN format

A carrier is a normal PGN plus a few tags:

| Tag | Meaning |
|---|---|
| `StegoCodec` | `arithmetic` or `integer` |
| `StegoMode` | `steered` or `plain` |
| `StegoBeta` | `β`, the extra weight given to a steered pawn push |
| `StegoWindow` | `W`, how many carrier plies steering applies to |
| `SetUp` / `FEN` | Present when the carrier starts from a non-standard position |

A decoder given only the PGN recovers both the message and the parameters it
needs.

---

## Scope and honest limitations

- **Not encrypted.** This hides a message; it does not protect one. Anyone who
  suspects steganography and runs the decoder gets the plaintext. Real
  confidentiality needs a key — for example by seeding the arithmetic interval
  from a shared secret, which this implementation does not do.
- **Steering is detectable in principle.** Over-weighting early pawn pushes
  shifts the game's move-frequency distribution away from natural play, which
  is exactly the kind of signal a steganalyzer looks for. The paper treats the
  capacity-versus-stealth trade-off as open.
- **Capacity is bounded by the carrier.** A game ends; a message that will not
  fit raises an error rather than truncating. Budget roughly 4.8 bytes per ply,
  and expect 2-5% of games to be too short for a given payload. In practice a
  game carries **~180 bytes**, so the web app refuses anything over 1 KiB up
  front instead of grinding through a payload that cannot possibly fit.
- **Steering weights must be whole numbers.** `beta` scales bucket widths that
  feed the coder's exact-integer state; a fractional value makes the coder
  degrade to floating point (Python) or throw a `RangeError` (JavaScript).
  Both codecs now reject a non-integer `beta` at the boundary.
- **The benchmark starts every carrier from the standard position**, so the
  reported BPM is not the branching factor of real midgame positions. The codec
  does support arbitrary FEN starts; the numbers simply do not cover them.
- **The paper reports a single operating point** (`beta = 1`, `window = 10`).
  There is no `beta`/`window` sweep, and **detectability was not measured** —
  both are left to future work.

---

## Citation

```bibtex
@misc{chesssteg2026,
  author       = {Harsh Thakur},
  title        = {True Arithmetic Coding with Pawn-Push Steering for
                  Capacity-Optimized Chess Steganography},
  year         = {2026},
  howpublished = {\url{https://github.com/Otto-Deviant1904/chess-steg}}
}
```

## License

MIT — see [`LICENSE`](LICENSE). `web/vendor/chess.js` is BSD 2-Clause, vendored
unmodified; see [`NOTICE.md`](NOTICE.md).
