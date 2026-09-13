"""Cross-implementation interop test: codec.py must be bit-compatible with web/stego.js.

The README claims "a PGN hidden by the web app decodes with codec.py and vice
versa". Nothing enforced that, so this test does. Both implementations are run
over one shared case list, then every case is checked for:

  1. the two encoders pick the *same* move sequence (identical bucket ordering),
  2. JS decodes a Python-encoded carrier,
  3. Python decodes a JS-encoded carrier.

The node side runs once for the whole matrix (it is the slow part). Skipped
automatically when node is unavailable.
Run: .venv/bin/python -m pytest test_interop.py -q
"""

from __future__ import annotations

import io
import json
import os
import random
import shutil
import subprocess

import chess
import chess.pgn
import pytest

from codec import (
    IntegerTruncationCodec,
    STEER_OPTS,
    TrueArithmeticCodec,
    encode_to_pgn,
    opts_from_headers,
)

REPO = os.path.dirname(os.path.abspath(__file__))
INTEROP_JS = os.path.join(REPO, "web", "interop.mjs")
NODE = shutil.which("node")

ARITH = TrueArithmeticCodec()
INTEGER = IntegerTruncationCodec()

# Positions exercising en-passant, promotion, and a non-standard start
# (the FEN + SetUp header path). All three must be legal positions — the two
# chess libraries disagree on how to move generation from an illegal one.
FEN_EP = "rnbqkbnr/ppp2ppp/4p3/3pP3/8/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 3"
FEN_PROMO = "4k3/2P5/8/8/8/8/6K1/8 w - - 0 1"

FIXED_CASES = [
    "",
    "a",
    "Hello, world!",
    "The crow flies at midnight. Money is in the cayman account.",
    "Ünïcödé — 中文 — \U0001F512 emoji",
    "y" * 40,
    "The quick brown fox jumps over the lazy dog. " * 2,
    # All-zero payloads exercise the sentinel-bit path (zero bit tail).
    "\x00\x00\x00",
    "trailing spaces   ",
    "\x00A\x00\x00",
]

RANDOM_SEED = 20260913
COMPARED: list = []  # (index, case) pairs both sides encoded without skipping


def _build_cases() -> list:
    cases = []
    for text in FIXED_CASES:
        for steering in (True, False):
            for codec in ("arithmetic", "integer"):
                cases.append({"text": text, "steering": steering, "codec": codec})
    for fen in (FEN_EP, FEN_PROMO):
        for steering in (True, False):
            cases.append({"text": "from a custom start", "steering": steering,
                          "codec": "arithmetic", "startFen": fen})
    # Random fuzz with a fixed seed, so any failure is reproducible.
    rng = random.Random(RANDOM_SEED)
    alpha = "abcdefghijklmnopqrstuvwxyz .,!?\n\t0123456789"
    for _ in range(40):
        n = 1 + rng.randrange(30)
        cases.append({
            "text": "".join(rng.choice(alpha) for _ in range(n)),
            "steering": rng.random() < 0.5,
            "codec": "arithmetic" if rng.random() < 0.5 else "integer",
        })
    return cases


CASES = _build_cases()


def _label(c: dict) -> str:
    where = c.get("startFen", "startpos")
    return f"codec={c['codec']} steering={c['steering']} pos={where} text={c['text'][:20]!r}"


def _sans(pgn_text: str) -> list:
    node = chess.pgn.read_game(io.StringIO(pgn_text))
    sans = []
    while node.variations:
        node = node.variations[0]
        sans.append(node.san())
    return sans


def _read(workdir: str, name: str):
    with open(os.path.join(workdir, name), encoding="utf-8") as fh:
        return json.load(fh)


def _run_node(mode: str, workdir: str, spec_path: str) -> None:
    subprocess.run([NODE, INTEROP_JS, mode, workdir, spec_path],
                   check=True, capture_output=True, text=True)


@pytest.fixture(scope="module")
def matrix(tmp_path_factory):
    """Encode every case in both languages once, then hand back the results."""
    if NODE is None:
        pytest.skip("node not installed")
    workdir = str(tmp_path_factory.mktemp("interop"))
    spec_path = os.path.join(workdir, "spec.json")
    with open(spec_path, "w", encoding="utf-8") as fh:
        json.dump(CASES, fh, ensure_ascii=False)

    py_pgn, py_skip = {}, set()
    for i, case in enumerate(CASES):
        codec = ARITH if case["codec"] == "arithmetic" else INTEGER
        opts = STEER_OPTS if case["steering"] else None
        board = chess.Board(case["startFen"]) if case.get("startFen") else chess.Board()
        try:
            pgn, _ = encode_to_pgn(board, case["text"], codec, opts)
        except RuntimeError as exc:
            assert "message too long" in str(exc), exc
            py_skip.add(i)  # legitimate carrier-capacity limit
            continue
        with open(os.path.join(workdir, f"py_{i}.pgn"), "w", encoding="utf-8") as fh:
            fh.write(pgn)
        py_pgn[i] = pgn
    for i in py_skip:
        with open(os.path.join(workdir, f"py_{i}.skip"), "w") as fh:
            fh.write("carrier capacity reached")

    _run_node("gen", workdir, spec_path)
    _run_node("dec", workdir, spec_path)
    js_gen = _read(workdir, "js_result.json")
    js_dec = _read(workdir, "js_decoded.json")

    compared = []
    for i, case in enumerate(CASES):
        if i in py_skip or js_gen[i].get("skipped") == "capacity":
            continue
        compared.append((i, case))
    return {"dir": workdir, "py_pgn": py_pgn, "py_skip": py_skip,
            "js_gen": js_gen, "js_dec": js_dec, "compared": compared}


def test_matrix_is_non_trivial(matrix):
    """Guard against the matrix silently collapsing to all-skips."""
    assert len(CASES) > 80
    assert len(matrix["compared"]) > 50, (
        f"only {len(matrix['compared'])}/{len(CASES)} cases encoded on both sides"
    )


def test_fixture_positions_are_legal():
    """The two chess libraries generate moves differently from an illegal
    position, which would make this test fail for reasons unrelated to the
    codec. Catch a bad fixture here rather than as a confusing divergence."""
    for fen in (FEN_EP, FEN_PROMO):
        board = chess.Board(fen)
        assert board.is_valid(), f"test fixture position is illegal: {fen}"


def test_encoders_choose_identical_moves(matrix):
    """Same message + same options => byte-identical move sequences."""
    for i, case in matrix["compared"]:
        py_sans = _sans(matrix["py_pgn"][i])
        js_sans = matrix["js_gen"][i]["sans"]
        assert py_sans == js_sans, (
            f"encoders diverged ({_label(case)})\n"
            f"  python: {' '.join(py_sans[:30])}\n"
            f"  js:     {' '.join(js_sans[:30])}"
        )


def test_js_decodes_python_carriers(matrix):
    for i, case in matrix["compared"]:
        rec = matrix["js_dec"][i]
        assert rec.get("ok") is not False, (
            f"stego.js failed on a codec.py carrier ({_label(case)}): {rec.get('error')}"
        )
        if rec.get("ok"):
            assert rec["text"] == case["text"], (
                f"stego.js decoded the wrong text ({_label(case)})"
            )


def test_python_decodes_js_carriers(matrix):
    workdir = matrix["dir"]
    for i, case in matrix["compared"]:
        with open(os.path.join(workdir, f"js_{i}.pgn"), encoding="utf-8") as fh:
            js_pgn = fh.read()
        if case["codec"] == "arithmetic":
            # Steering params travel in the PGN headers; let the codec read them
            # back — the same path a real recipient takes.
            headers = dict(chess.pgn.read_game(io.StringIO(js_pgn)).headers)
            assert opts_from_headers(headers).steering == case["steering"]
            out = ARITH.decode_pgn(js_pgn)
        else:
            out = INTEGER.decode_pgn(js_pgn)
        assert out.decode("utf-8") == case["text"], (
            f"codec.py decoded the wrong text from a stego.js carrier ({_label(case)})"
        )


def test_untagged_pgns_agree_across_implementations(tmp_path):
    """Strip the Stego* tags and both implementations must reach the same verdict.

    This is the case the original interop matrix missed. codec.py read an absent
    StegoMode as plain; the web app read it as steered. So a carrier whose tags
    had been stripped decoded in the browser and failed in Python — the same
    bytes, two different answers, contradicting the README's compatibility
    claim. web/headers.js now reads it as plain to match.

    Each side is handed the stripped PGN with no opts, exactly as a recipient
    who only has the file would be.
    """
    steered, _ = encode_to_pgn(chess.Board(), "no tags here", ARITH, STEER_OPTS)
    stripped = "\n".join(l for l in steered.splitlines() if not l.startswith("[Stego")) + "\n"
    assert "[StegoMode" not in stripped

    # Python: an absent StegoMode is plain, so a steered carrier does not decode.
    headers = dict(chess.pgn.read_game(io.StringIO(stripped)).headers)
    assert "StegoMode" not in headers
    assert opts_from_headers(headers).steering is False
    py_failed = False
    try:
        got = ARITH.decode_pgn(stripped).decode("utf-8")
    except (RuntimeError, UnicodeDecodeError):
        py_failed = True
    else:
        py_failed = got != "no tags here"

    # The JS side must reach the same verdict, not the opposite one.
    spec = [{"text": "no tags here", "steering": False, "codec": "arithmetic",
             "stripStegoTags": True}]
    workdir = str(tmp_path)
    spec_path = os.path.join(workdir, "spec.json")
    with open(spec_path, "w", encoding="utf-8") as fh:
        json.dump(spec, fh)
    js_pgn_path = os.path.join(workdir, "py_0.pgn")
    with open(js_pgn_path, "w", encoding="utf-8") as fh:
        fh.write(stripped)
    _run_node("dec", workdir, spec_path)
    js_rec = _read(workdir, "js_decoded.json")[0]

    # Both must fail to recover the message (a steered carrier read as plain).
    assert py_failed, "python unexpectedly recovered a stripped steered carrier"
    assert js_rec.get("ok") is not True or js_rec.get("text") != "no tags here", (
        f"js recovered a message python could not: {js_rec}"
    )

    # And the rule itself, asserted directly on both sides.
    js_rule = subprocess.run(
        [NODE, "-e",
         "import('./web/headers.js').then(m=>{const o=m.optsFromPgn(process.argv[1]);"
         "console.log(JSON.stringify({steering:o.steering,modeStated:o.modeStated}));})",
         stripped],
        cwd=REPO, capture_output=True, text=True, check=True,
    )
    js_opts = json.loads(js_rule.stdout.strip().splitlines()[-1])
    assert js_opts == {"steering": False, "modeStated": False}, js_opts
    assert js_opts["steering"] == opts_from_headers(headers).steering, (
        "web/headers.js and codec.py disagree about an absent StegoMode"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
