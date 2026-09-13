# legacy — superseded proof-of-concept

**Do not use this code.** It is kept only so the repository's history is
readable, and nothing else in the repo imports it.

`engine.py` and `experiment.py` are the first version of the encoder, written
before the arithmetic coder was in place. Two defects make their round-trip
lossy, which is why they were replaced:

1. **The decoder does not mirror the encoder's pawn weighting.** The encoder
   biases its move choice toward pawn pushes; the decoder does not apply the
   same bias when it rebuilds the legal-move buckets. Encoder and decoder
   therefore walk different bucket lists, and the recovered bitstream drifts.
2. **The encoder samples a fixed 10 bits per move.** Ten bits rarely matches
   `floor(log2(N))` for a given position, so moves are consumed faster than the
   decoder releases them and the message is truncated or misaligned.

The replacements are `codec.py` (Python) and `web/stego.js` (JavaScript), which
are bit-compatible with each other and are covered by `test_codec.py` and
`test_interop.py`.

The interesting idea that survived from this prototype is the pawn-push bias,
which is now the paper's *Active Environment Steering* — but applied as a
bucket **weight** on both sides of the codec, so it changes capacity without
breaking the round-trip.
