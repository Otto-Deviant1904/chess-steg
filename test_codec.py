"""Round-trip tests for the chess stego codecs — mirror of web/test_stego.mjs.

Run: .venv/bin/python -m pytest test_codec.py   (or plain: .venv/bin/python test_codec.py)
"""

import random
import sys

import chess
import pytest

from codec import (
    Coder,
    IntegerTruncationCodec,
    PLAIN_OPTS,
    STEER_OPTS,
    StegOpts,
    TrueArithmeticCodec,
    bits_to_bytes,
    bytes_to_bits,
    canonical_moves,
    encode_to_pgn,
    make_headers,
    opts_from_headers,
    text_to_bytes,
    weighted_moves,
)

ARITH = TrueArithmeticCodec()
INTEGER = IntegerTruncationCodec()

capacity_skips = 0


def roundtrip_from_board(board, text, opts):
    """Round-trip a payload embedded from `board`'s position (not just the
    standard start). Returns None if the carrier ran out of capacity."""
    try:
        pgn, res = encode_to_pgn(board, text, ARITH, opts)
    except RuntimeError as e:
        if "message too long" in str(e):
            return None
        raise
    out = ARITH.decode_pgn(pgn)
    expected = text if isinstance(text, bytes) else text.encode("utf-8")
    assert out == expected, f"MISMATCH from {board.fen()}\n want: {expected!r}\n got:  {out!r}"
    return {"plies": res.plies, "bits": res.bits_used, "bpm": res.bpm}


def roundtrip(text, opts, codec_name="arithmetic"):
    board = chess.Board()
    try:
        if codec_name == "arithmetic":
            pgn, res = encode_to_pgn(board, text, ARITH, opts)
        else:
            pgn, res = encode_to_pgn(board, text, INTEGER)
    except RuntimeError as e:
        if "message too long" in str(e):
            global capacity_skips
            capacity_skips += 1  # legitimate carrier-capacity limit, not a codec bug
            return None
        raise
    if codec_name == "arithmetic":
        out = ARITH.decode_pgn(pgn, opts)
    else:
        out = INTEGER.decode_pgn(pgn)
    expected = text if isinstance(text, bytes) else text.encode("utf-8")
    assert out == expected, f"MISMATCH codec={codec_name} opts={opts}\n want: {expected!r}\n got:  {out!r}"
    return {"plies": res.plies, "bits": res.bits_used, "bpm": res.bpm}


def test_fixed_cases():
    cases = [
        "",
        "a",
        "Hello, world!",
        "The crow flies at midnight. Money is in the cayman account.",
        "Ünïcödé — 中文 — \U0001F512 emoji test",
        "x",
        "y" * 40,
        "The quick brown fox jumps over the lazy dog. " * 2,
        # Sentinel-path cases: payloads whose final bits are zeros.
        b"\x00\x00\x00",           # all-zero bytes -> all-zero bit tail
        "ends in spaces   ",        # trailing space bits
        b"hi\x00\x00\x00\x00",      # mixed tail
    ]
    for opts in (STEER_OPTS, PLAIN_OPTS):
        for text in cases:
            for codec in ("arithmetic", "integer"):
                roundtrip(text, opts, codec)


def test_random_fuzz():
    rng = random.Random(12345)
    alpha = "abcdefghijklmnopqrstuvwxyz .,!?\n\t0123456789"
    for _ in range(40):
        length = 1 + rng.randrange(30)
        text = "".join(rng.choice(alpha) for _ in range(length))
        opts = STEER_OPTS if rng.random() < 0.5 else PLAIN_OPTS
        codec = "arithmetic" if rng.random() < 0.5 else "integer"
        roundtrip(text, opts, codec)


def test_bits_bytes_identity():
    rng = random.Random(999)
    raw = bytes(rng.randrange(256) for _ in range(512))
    assert bits_to_bytes(bytes_to_bits(raw)) == raw


def test_mode_mismatch():
    """Decoding a steered game with plain opts must NOT recover the message."""
    board = chess.Board()
    pgn, _ = encode_to_pgn(board, "mode check", ARITH, STEER_OPTS)
    wrong = None
    try:
        wrong = ARITH.decode_pgn(pgn, PLAIN_OPTS).decode("utf-8", errors="replace")
    except RuntimeError:
        wrong = None  # threw: good
    assert wrong != "mode check", "plain-mode decode of steered game should not recover the message"


def test_header_mode_detection():
    """opts=None reads steering mode from PGN headers and round-trips."""
    board = chess.Board()
    pgn, _ = encode_to_pgn(board, "header modes", ARITH, STEER_OPTS)
    game_headers = {
        line.strip()[1:-1].split(' "', 1)[0]: line.strip()[1:-1].split(' "', 1)[1].rstrip('"')
        for line in pgn.splitlines()
        if line.startswith("[Stego")
    }
    assert game_headers["StegoCodec"] == "arithmetic"
    assert game_headers["StegoMode"] == "steered"
    assert game_headers["StegoBeta"] == "1"
    assert game_headers["StegoWindow"] == "10"
    assert opts_from_headers(game_headers).steering is True
    assert ARITH.decode_pgn(pgn).decode("utf-8") == "header modes"


def test_integer_mode_mismatch_header():
    """An integer-coded game fed to the arithmetic decoder must fail or give garbage."""
    board = chess.Board()
    pgn, _ = encode_to_pgn(board, "cross codec check", INTEGER)
    wrong = None
    try:
        wrong = ARITH.decode_pgn(pgn).decode("utf-8", errors="replace")
    except RuntimeError:
        wrong = None
    assert wrong != "cross codec check"


def test_message_too_long_raises():
    """A payload far beyond the carrier's capacity must raise RuntimeError."""
    board = chess.Board()
    try:
        ARITH.encode_message(board, "z" * 100000, PLAIN_OPTS)
    except RuntimeError as e:
        assert "message too long" in str(e)
        return
    raise AssertionError("expected RuntimeError for oversized payload")


def test_bpm_bounds():
    """Observed BPM stays in a sane band (0, log2 max branching]."""
    r = roundtrip("Hello, world! This is a test.", STEER_OPTS, "arithmetic")
    assert r is not None and 0 < r["bpm"] <= 6
    r2 = roundtrip("Hello, world! This is a test.", PLAIN_OPTS, "integer")
    assert r2 is not None and 0 < r2["bpm"] <= 6


def test_canonical_move_order():
    """Every codec must enumerate legal moves by UCI.

    python-chess and chess.js generate moves in their own internal order, so
    without an explicit canonical order the two implementations map the same
    bitstream onto different games. (test_interop.py proves the cross-language
    consequence; this pins the invariant locally.)
    """
    board = chess.Board()
    moves = canonical_moves(board)
    assert [m.uci() for m in moves] == sorted(m.uci() for m in moves)
    # The steering weights must line up with that same order.
    wm = weighted_moves(board, STEER_OPTS, 0)
    assert [m.uci() for m in wm["moves"]] == [m.uci() for m in moves]
    assert wm["cum"][-1] == sum(wm["weights"])


def test_steering_window_is_movetext_relative():
    """The steering window counts plies of the carrier movetext, not the
    board's absolute ply.

    python-chess keeps counting plies from a FEN's move number, so a carrier
    starting mid-game would otherwise be steered in a different ply range here
    than in the JS mirror, and the two would not decode each other.
    """
    fen = "rnbqkbnr/ppp2ppp/4p3/3pP3/8/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 3"
    board = chess.Board(fen)
    assert board.ply() != 0, "fixture should have a non-zero absolute ply"

    # Ply 0 of the movetext is inside the window; the board's absolute ply
    # (4 here) is irrelevant to the decision.
    steered = weighted_moves(board, STEER_OPTS, 0)
    unsteered = weighted_moves(board, PLAIN_OPTS, 0)
    assert steered["total"] > unsteered["total"], "ply 0 should be steered"

    # And a movetext ply past the window is not.
    late = weighted_moves(board, STEER_OPTS, STEER_OPTS.window)
    assert late["total"] == unsteered["total"]

    # End to end: a FEN-start carrier still round-trips.
    r = roundtrip_from_board(chess.Board(fen), "custom start", STEER_OPTS)
    assert r is not None and r["bpm"] > 0


def test_fen_start_roundtrips():
    """Carriers embedded from a non-standard start survive encode/decode."""
    for fen in ("rnbqkbnr/ppp2ppp/4p3/3pP3/8/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 3",
                "4k3/2P5/8/8/8/8/6K1/8 w - - 0 1"):
        assert chess.Board(fen).is_valid(), f"fixture position is illegal: {fen}"
        for opts in (STEER_OPTS, PLAIN_OPTS):
            roundtrip_from_board(chess.Board(fen), "from a custom start", opts)


# ---------------------------------------------------------------------------
# Steering weights must be whole numbers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("beta", [0, 1, 2, 8])
def test_integer_betas_encode(beta):
    """Whole-number betas, including 0, still work end to end."""
    r = roundtrip_from_board(chess.Board(), f"beta {beta}",
                             StegOpts(steering=True, beta=beta, window=10))
    assert r is not None and r["bpm"] > 0


@pytest.mark.parametrize("beta", [0.5, 1.5, 2.25])
def test_fractional_beta_rejected(beta):
    """A fractional beta must be refused at the boundary.

    Otherwise the bucket total becomes a float, Coder.D degrades from an exact
    int to a float, and the coder survives a short payload while dying with
    "OverflowError: int too large to convert to float" on a realistic one. The
    JavaScript mirror fails differently (a RangeError from BigInt), so this also
    keeps the two implementations honest about the same input.
    """
    with pytest.raises(ValueError, match="whole number"):
        StegOpts(steering=True, beta=beta, window=10)


def test_bad_window_rejected():
    for window in (0, -1, 2.5):
        with pytest.raises(ValueError):
            StegOpts(steering=True, beta=1, window=window)


def test_coder_state_stays_integral():
    """The core invariant the beta guard protects: Coder state is always int."""
    for beta in (0, 1, 2, 8):
        board = chess.Board()
        wm = weighted_moves(board, StegOpts(steering=True, beta=beta, window=10), 0)
        assert isinstance(wm["total"], int), f"beta={beta} produced {type(wm['total']).__name__}"
        coder = Coder()
        coder.narrow(wm, 0)
        for attr in ("lo", "hi", "D"):
            assert isinstance(getattr(coder, attr), int), (
                f"beta={beta} made Coder.{attr} a {type(getattr(coder, attr)).__name__}"
            )


# ---------------------------------------------------------------------------
# Header defaults must match web/headers.js
# ---------------------------------------------------------------------------


def test_untagged_pgn_defaults_to_plain():
    """An absent StegoMode means plain.

    web/headers.js reads it the same way. The two used to disagree (the app
    assumed steered), so a carrier with its Stego* tags stripped decoded in the
    browser but never here. test_interop.py now pins the agreement across both.
    """
    assert opts_from_headers({}).steering is False
    assert opts_from_headers({"StegoMode": "plain"}).steering is False
    assert opts_from_headers({"StegoMode": "steered"}).steering is True


def test_header_parsing_tolerates_junk():
    assert opts_from_headers({"StegoBeta": "not-a-number"}).beta == 1
    assert opts_from_headers({"StegoWindow": ""}).window == 10
    assert opts_from_headers({"StegoCodec": "integer"}).steering is False


def test_implausible_length_message_is_user_facing():
    """The decoder's 'no message here' error must not leak internal wording."""
    ordinary_game = (
        '[Event "Ordinary"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 *'
    )
    with pytest.raises(RuntimeError) as exc:
        ARITH.decode_pgn(ordinary_game)
    message = str(exc.value)
    assert "No hidden message" in message, message
    for leaked in ("Header decodes", "corrupt game", "implausible length —"):
        assert leaked not in message, f"internal wording {leaked!r} still surfaced: {message}"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
    if failures:
        sys.exit(1)
    print("ALL ROUNDTRIP TESTS PASS")
