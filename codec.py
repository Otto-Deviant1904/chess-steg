"""Chess steganography codecs — Python mirror of web/stego.js.

Two codecs:

1. TrueArithmeticCodec — exact integer arithmetic coding over legal-move
   buckets. State is (lo, hi, D) as Python ints with real interval
   [lo/D, hi/D). The message is a 32-bit big-endian byte-length header
   followed by UTF-8 payload bits, treated as the binary fraction
   p = P/Q with Q = 2^nbits. Optional pawn-push steering adds weight
   1+beta to legal pawn pushes (non-captures) during the first `window`
   plies, applied identically on both sides of the codec.

2. IntegerTruncationCodec — the Cao-style baseline: per ply take
   k = floor(log2(N)) bits from the stream, interpret them as an index
   into the legal-move list, and play that move. The fractional part of
   log2(N) is discarded (the capacity loss the true coder recovers).

PGN header convention:
    [StegoCodec "arithmetic"|"integer"]
    [StegoMode  "steered"|"plain"]
    [StegoBeta  "<beta>"]
    [StegoWindow "<window>"]
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import chess
import chess.pgn

HEADER_BITS = 32  # big-endian byte-length prefix
MAX_DECODED_LENGTH = 1_000_000  # reject implausible headers


# ---------------------------------------------------------------------------
# Options / results
# ---------------------------------------------------------------------------


@dataclass
class StegOpts:
    steering: bool = False
    beta: int = 1
    window: int = 10

    def __post_init__(self):
        # beta and window scale bucket *widths*, and Coder multiplies them into
        # its exact-integer state. A fractional beta makes the bucket total a
        # float, so Coder.D silently becomes a float and the coder stops being
        # exact — it survives a short payload and then dies with
        # "OverflowError: int too large to convert to float" on a realistic one.
        # Reject it at the boundary instead, where the message can be useful.
        for name in ("beta", "window"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(
                    f"{name} must be a whole number, got {value!r} "
                    f"({type(value).__name__}). Fractional steering weights break the coder."
                )
        if self.beta < 0:
            raise ValueError(f"beta must be >= 0, got {self.beta}")
        if self.window < 1:
            raise ValueError(f"window must be >= 1, got {self.window}")


PLAIN_OPTS = StegOpts(steering=False)
STEER_OPTS = StegOpts(steering=True, beta=1, window=10)


@dataclass
class EncodeResult:
    plies: int
    bits_used: int
    target_bits: int
    bpm: float
    sans: list = field(default_factory=list)
    cumulative_bits: list = field(default_factory=list)  # bits after each ply

    @property
    def bits_embedded(self) -> int:
        return self.target_bits


# ---------------------------------------------------------------------------
# Bit helpers
# ---------------------------------------------------------------------------


def text_to_bytes(text: str) -> bytes:
    return text.encode("utf-8")


def bytes_to_text(raw: bytes) -> str:
    return raw.decode("utf-8")


def bytes_to_bits(data: bytes) -> list:
    bits = []
    for b in data:
        for i in range(7, -1, -1):
            bits.append((b >> i) & 1)
    return bits


def bits_to_bytes(bits: list) -> bytes:
    out = bytearray()
    for i in range(0, len(bits) - 7, 8):
        b = 0
        for j in range(8):
            b = (b << 1) | bits[i + j]
        out.append(b)
    return bytes(out)


def message_bits(text_or_bytes) -> list:
    """32-bit big-endian byte-length header + UTF-8 payload bits."""
    raw = text_or_bytes if isinstance(text_or_bytes, bytes) else text_to_bytes(text_or_bytes)
    header = [(len(raw) >> i) & 1 for i in range(HEADER_BITS - 1, -1, -1)]
    return header + bytes_to_bits(raw)


# ---------------------------------------------------------------------------
# Weighted move lists
# ---------------------------------------------------------------------------


def is_pawn_push(board: chess.Board, move: chess.Move) -> bool:
    """A pawn move that is not a capture (en passant counts as a capture)."""
    piece = board.piece_at(move.from_square)
    if piece is None or piece.piece_type != chess.PAWN:
        return False
    return not board.is_capture(move)


def canonical_moves(board: chess.Board) -> list:
    """Legal moves in the canonical order every codec in every language uses.

    python-chess and chess.js generate legal moves in different internal
    orders. Sorting by UCI (identical to chess.js's `lan`) is what makes a
    bitstream mean the same game on both sides of the wire.
    """
    return sorted(board.legal_moves, key=lambda m: m.uci())


def weighted_moves(board: chess.Board, opts: StegOpts, ply: int) -> dict:
    """Legal moves in canonical order with optional pawn-push steering.

    `ply` is the move's index in the carrier movetext (0-based). The steering
    window is defined against that index rather than board.ply() because
    python-chess keeps counting from a FEN's move number while chess.js counts
    from 0 — only the movetext index is identical on both sides.

    Returns {moves, sans, weights, total, cum} with cum[i] = sum of the
    weights before index i.
    """
    steer = opts.steering and ply < opts.window
    moves = canonical_moves(board)
    sans = [board.san(m) for m in moves]
    if steer:
        weights = [1 + opts.beta if is_pawn_push(board, m) else 1 for m in moves]
    else:
        weights = [1] * len(moves)
    cum = [0]
    for w in weights:
        cum.append(cum[-1] + w)
    return {"moves": moves, "sans": sans, "weights": weights, "total": cum[-1], "cum": cum}


# ---------------------------------------------------------------------------
# Arithmetic coder core
# ---------------------------------------------------------------------------


class Coder:
    """Exact integer arithmetic-coding state: real interval [lo/D, hi/D)."""

    def __init__(self):
        self.lo = 0
        self.hi = 1
        self.D = 1

    def narrow(self, wm: dict, i: int) -> None:
        """Narrow the interval into bucket i of the weighted list."""
        W = self.hi - self.lo
        lo = self.lo * wm["total"] + wm["cum"][i] * W
        hi = lo + wm["weights"][i] * W
        self.lo = lo
        self.hi = hi
        self.D *= wm["total"]

    def renormalize(self, sink) -> int:
        """Emit (decode) or consume (encode) renormalization bits until the
        interval straddles the midpoint. Each bit rescales the real interval
        by 2 (D unchanged)."""
        n = 0
        while True:
            if 2 * self.hi <= self.D:
                sink(0)
                self.lo *= 2
                self.hi *= 2
                n += 1
            elif 2 * self.lo >= self.D:
                sink(1)
                self.lo = 2 * self.lo - self.D
                self.hi = 2 * self.hi - self.D
                n += 1
            else:
                return n


# ---------------------------------------------------------------------------
# PGN helpers
# ---------------------------------------------------------------------------


def make_headers(codec: str, opts: StegOpts) -> dict:
    headers = {
        "Event": "Steganographic chess",
        "StegoCodec": codec,
        "StegoMode": "steered" if opts.steering else "plain",
        "StegoBeta": str(opts.beta),
        "StegoWindow": str(opts.window),
    }
    return headers


def build_pgn(board: chess.Board, sans: list, headers: dict) -> str:
    """Build a PGN string from the starting position of `board` and a SAN
    move list. Non-standard starting positions get SetUp/FEN headers."""
    game = chess.pgn.Game()
    if board.fen() != chess.STARTING_FEN:
        game.setup(board.copy(stack=False))
    game.headers.update(headers)
    replay = board.copy(stack=False)
    line = []
    for san in sans:
        mv = replay.parse_san(san)
        line.append(mv)
        replay.push(mv)
    game.add_line(line)
    return str(game)


# ---------------------------------------------------------------------------
# True arithmetic codec
# ---------------------------------------------------------------------------


class TrueArithmeticCodec:
    """Exact arithmetic coding over legal-move buckets (mirror of stego.js)."""

    name = "arithmetic"

    def encode_message(self, board: chess.Board, text, opts: StegOpts | None = None,
                       on_move=None) -> EncodeResult:
        """Encode `text` into moves played on `board` (mutated). Raises
        RuntimeError if the game ends before the message is fully embedded."""
        opts = opts or PLAIN_OPTS
        # 32-bit big-endian byte-length header, payload bits, then a sentinel 1.
        # The sentinel keeps the remainder fraction p strictly positive so the
        # coder never locks onto bucket 0 with an all-zero tail (which would
        # cycle the game into a threefold-repetition draw before the last bits
        # emit). The sentinel is never counted as an emitted bit.
        bits = message_bits(text) + [1]
        target = len(bits) - 1
        # Remainder fraction p = P/Q. Renormalization consumes p's leading
        # bits, so Q shrinks by one bit per emitted bit; once exhausted
        # (Q == 1) the remaining expansion is trailing zeros and p stays 0.
        P = 0
        for b in bits:
            P = (P << 1) | b
        Q = 1 << len(bits)

        start_fen = board.fen()
        coder = Coder()
        emitted = 0
        sans = []
        cumulative = []
        state = {"P": P, "Q": Q}

        def sink(bit):
            nonlocal emitted
            emitted += 1
            if bit == 0:
                if state["Q"] > 1:
                    state["Q"] >>= 1
            else:
                state["P"] -= state["Q"] >> 1
                state["Q"] >>= 1

        while True:
            if emitted >= target:
                return EncodeResult(
                    plies=len(sans),
                    bits_used=emitted,
                    target_bits=target,
                    bpm=emitted / max(1, len(sans)),
                    sans=sans,
                    cumulative_bits=cumulative,
                )
            if board.is_game_over():
                raise RuntimeError(
                    f"Game ended after {len(sans)} plies with {emitted}/{target} bits "
                    "embedded — message too long for this carrier game."
                )
            wm = weighted_moves(board, opts, len(sans))
            total = wm["total"]
            i = 0
            if total > 1:
                # r = floor((p - lo/D) * D*total/W): position of p within the buckets
                X = (state["P"] * coder.D - coder.lo * state["Q"]) * total
                WQ = (coder.hi - coder.lo) * state["Q"]
                if not (0 <= X < WQ * total):
                    raise RuntimeError("interval invariant broken")
                r = X // WQ
                while i + 1 < len(wm["moves"]) and wm["cum"][i + 1] <= r:
                    i += 1
            coder.narrow(wm, i)
            move = wm["moves"][i]
            sans.append(wm["sans"][i])
            board.push(move)
            coder.renormalize(sink)
            cumulative.append(emitted)
            if on_move is not None:
                on_move(wm["sans"][i], emitted)

    def decode_pgn(self, pgn_text: str, opts: StegOpts | None = None) -> bytes:
        """Decode the message from a carrier PGN. If `opts` is None the
        steering parameters are read from the PGN's Stego* headers."""
        game = chess.pgn.read_game(io.StringIO(pgn_text))
        if game is None:
            raise RuntimeError("Could not parse PGN.")
        if opts is None:
            opts = opts_from_headers(game.headers)
        board = game.board()  # honours SetUp/FEN headers
        sans = []
        node = game
        while node.variations:
            node = node.variations[0]
            sans.append(node.san())
        coder = Coder()
        bits: list = []
        expected_len = None
        for ply, san in enumerate(sans):
            wm = weighted_moves(board, opts, ply)
            try:
                idx = wm["sans"].index(san)
            except ValueError:
                raise RuntimeError(f"Illegal or unmatched move in carrier game: {san}") from None
            coder.narrow(wm, idx)
            board.push(wm["moves"][idx])
            coder.renormalize(bits.append)
            if expected_len is None and len(bits) >= HEADER_BITS:
                length = 0
                for j in range(HEADER_BITS):
                    length = length * 2 + bits[j]
                if length > MAX_DECODED_LENGTH:
                    raise RuntimeError(
                        "No hidden message here — the game decodes to an implausible message length. Either this is an ordinary game, or it was encoded with a different codec."
                    )
                expected_len = length
            if expected_len is not None and len(bits) >= HEADER_BITS + expected_len * 8:
                return bits_to_bytes(bits[HEADER_BITS:HEADER_BITS + expected_len * 8])
        raise RuntimeError("Game ended before a complete message was embedded.")


# ---------------------------------------------------------------------------
# Integer-truncation baseline (Cao-style)
# ---------------------------------------------------------------------------


class IntegerTruncationCodec:
    """Per ply: k = floor(log2(N)) bits map to an index into the legal-move
    list. Fractional bits are discarded — this is the capacity-loss baseline."""

    name = "integer"

    def encode_message(self, board: chess.Board, text, on_move=None) -> EncodeResult:
        bits = message_bits(text)
        target = len(bits)
        pos = 0
        sans = []
        cumulative = []
        while True:
            if pos >= target:
                return EncodeResult(
                    plies=len(sans),
                    bits_used=pos,
                    target_bits=target,
                    bpm=pos / max(1, len(sans)),
                    sans=sans,
                    cumulative_bits=cumulative,
                )
            if board.is_game_over():
                raise RuntimeError(
                    f"Game ended after {len(sans)} plies with {pos}/{target} bits "
                    "embedded — message too long for this carrier game."
                )
            moves = canonical_moves(board)
            sans_all = [board.san(m) for m in moves]
            N = len(moves)
            if N == 0:  # unreachable: is_game_over() covers mate and stalemate
                raise RuntimeError("No legal moves available to encode into.")
            k = N.bit_length() - 1  # floor(log2(N))
            if k <= 0:
                move, san = moves[0], sans_all[0]  # forced move, carries no information
            else:
                v = 0
                for _ in range(k):
                    if pos < target:
                        bit = bits[pos]
                        pos += 1
                    else:
                        bit = 0  # pad zeros past end
                    v = (v << 1) | bit
                # v < 2^k <= N, so the index is always legal.
                move, san = moves[v], sans_all[v]
            board.push(move)
            sans.append(san)
            cumulative.append(pos)
            if on_move is not None:
                on_move(san, pos)

    def decode_pgn(self, pgn_text: str, opts: StegOpts | None = None) -> bytes:
        game = chess.pgn.read_game(io.StringIO(pgn_text))
        if game is None:
            raise RuntimeError("Could not parse PGN.")
        board = game.board()  # honours SetUp/FEN headers
        sans = []
        node = game
        while node.variations:
            node = node.variations[0]
            sans.append(node.san())
        bits: list = []
        expected_len = None
        for san in sans:
            moves = canonical_moves(board)
            sans_all = [board.san(m) for m in moves]
            try:
                idx = sans_all.index(san)
            except ValueError:
                raise RuntimeError(f"Illegal or unmatched move in carrier game: {san}") from None
            k = len(moves).bit_length() - 1
            for j in range(k - 1, -1, -1):
                bits.append((idx >> j) & 1)
            board.push(moves[idx])
            if expected_len is None and len(bits) >= HEADER_BITS:
                length = 0
                for j in range(HEADER_BITS):
                    length = length * 2 + bits[j]
                if length > MAX_DECODED_LENGTH:
                    raise RuntimeError(
                        "No hidden message here — the game decodes to an implausible message length. Either this is an ordinary game, or it was encoded with a different codec."
                    )
                expected_len = length
            if expected_len is not None and len(bits) >= HEADER_BITS + expected_len * 8:
                return bits_to_bytes(bits[HEADER_BITS:HEADER_BITS + expected_len * 8])
        raise RuntimeError("Game ended before a complete message was embedded.")


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------


def encode_to_pgn(board: chess.Board, text, codec, opts: StegOpts | None = None) -> tuple:
    """Encode into a fresh game from `board`'s position and return (pgn, result)."""
    work = board.copy(stack=False)
    if isinstance(codec, TrueArithmeticCodec):
        result = codec.encode_message(work, text, opts)
        headers = make_headers(codec.name, opts or PLAIN_OPTS)
    else:
        result = codec.encode_message(work, text)
        headers = make_headers(codec.name, PLAIN_OPTS)
    return build_pgn(board, result.sans, headers), result


def opts_from_headers(headers) -> StegOpts:
    mode = headers.get("StegoMode", "plain")

    def _int(name, default):
        try:
            return int(headers.get(name, default))
        except (TypeError, ValueError):
            return default

    return StegOpts(
        steering=(mode == "steered"),
        beta=_int("StegoBeta", 1),
        window=_int("StegoWindow", 10),
    )
