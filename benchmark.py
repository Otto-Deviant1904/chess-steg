"""Benchmark for the chess-steganography paper's evaluation.

Arms:
  1. integer-truncation baseline (Cao-style, no steering)
  2. true arithmetic coder, plain (no steering)
  3. true arithmetic coder + pawn-push steering (beta=1, window=10)

Matched-pair design: every arm encodes the SAME per-game seeded payload,
embedding from the game's STARTING position (standard chess).

Run:
  .venv/bin/python benchmark.py pilot        # 100-game pilot + payload calibration
  .venv/bin/python benchmark.py full [N]     # full run (default 1000 games)

Set INCLUDE_ROWS=1 to also write results/benchmark_rows.json (per-game detail
including per-ply bit trajectories, ~3 MB). Not committed; make_figures.py needs
it to rebuild the paper's figures.
"""

import csv
import json
import math
import os
import random
import sys
import time
from pathlib import Path

import chess
import chess.pgn

from codec import (
    IntegerTruncationCodec,
    PLAIN_OPTS,
    STEER_OPTS,
    TrueArithmeticCodec,
    build_pgn,
    make_headers,
)

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
CORPUS = ROOT / "data" / "lichess_2013-01.pgn"

GLOBAL_SEED = 20260913
PILOT_GAMES = 100
FULL_GAMES = 1000
PAYLOAD_LADDER = [64, 56, 48, 40, 32, 24, 16]  # bytes; first that yields >=95% success in ALL arms
MIN_SUCCESS = 0.95
MAX_FULL_MINUTES = 50

ARITH = TrueArithmeticCodec()
INTEGER = IntegerTruncationCodec()
ARMS = [
    ("arm1_integer_baseline", "integer"),
    ("arm2_arithmetic_plain", "plain"),
    ("arm3_arithmetic_steered", "steered"),
]

# Per-game rows (including the full per-ply bit trajectory) are large: ~3 MB for
# a 1000-game run. The committed results hold summaries, paired statistics and
# the CSV only. Set this True to also write the per-game detail to
# results/benchmark_rows.json, which make_figures.py needs for the BPM
# trajectory figure and which is gitignored.
INCLUDE_ROWS = os.environ.get("INCLUDE_ROWS", "").strip().lower() in ("1", "true", "yes", "on")

ROWS_PATH = RESULTS / "benchmark_rows.json"

_GAMES_CACHE = {}


# ---------------------------------------------------------------------------
# Stats helpers (no scipy required)
# ---------------------------------------------------------------------------


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def stdev(xs):
    if len(xs) < 2:
        return float("nan")
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def betacf(a, b, x):
    """Continued fraction for the incomplete beta function (Numerical Recipes)."""
    MAXIT, EPS, FPMIN = 200, 3e-16, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < EPS:
            break
    return h


def betainc(a, b, x):
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * betacf(a, b, x) / a
    return 1.0 - math.exp(lbeta + b * math.log1p(-x) + a * math.log(x)) * betacf(b, a, 1.0 - x) / b


def paired_t_test(xs, ys):
    """Two-sided paired t-test. Returns (t_stat, df, p_value)."""
    diffs = [y - x for x, y in zip(xs, ys)]
    n = len(diffs)
    if n < 2:
        return float("nan"), n - 1, float("nan")
    md = mean(diffs)
    sd = stdev(diffs)
    if sd == 0:
        if md == 0:
            return 0.0, n - 1, 1.0
        return (float("inf") if md > 0 else float("-inf")), n - 1, 0.0
    t = md / (sd / math.sqrt(n))
    df = n - 1
    p = betainc(df / 2.0, 0.5, df / (df + t * t))  # two-sided
    return t, df, p


def cohens_d(xs, ys):
    diffs = [y - x for x, y in zip(xs, ys)]
    sd = stdev(diffs)
    return mean(diffs) / sd if sd else float("nan")


# ---------------------------------------------------------------------------
# Corpus + payloads
# ---------------------------------------------------------------------------


def load_starting_positions(n_games):
    """Return [(game_idx, original_plies)] for the first n standard games.
    Encoding always starts from the standard starting position (matched pairs)."""
    if n_games in _GAMES_CACHE:
        return _GAMES_CACHE[n_games]
    if not CORPUS.exists():
        # The corpus is ~89 MB and deliberately not committed, so a fresh clone
        # hits this on the documented first command. Say what to do instead of
        # surfacing a bare FileNotFoundError from three frames down.
        raise SystemExit(
            f"benchmark corpus not found: {CORPUS}\n"
            "It is ~89 MB and is not committed. Fetch it first:\n"
            "    ./scripts/fetch_corpus.sh\n"
            "The codec, its tests and the web app do not need it."
        )
    out = []
    with open(CORPUS, encoding="utf-8", errors="replace") as f:
        while len(out) < n_games:
            game = chess.pgn.read_game(f)
            if game is None:
                break
            if game.headers.get("FEN") and game.headers["FEN"] != chess.STARTING_FEN:
                continue  # only standard starting positions
            plies = 0
            node = game
            while node.variations:
                node = node.variations[0]
                plies += 1
            out.append((len(out), plies))
    _GAMES_CACHE[n_games] = out
    return out


def payload_for(game_idx, length_bytes):
    """Deterministic per-game payload (identical for every arm of a game)."""
    rng = random.Random((GLOBAL_SEED << 24) | game_idx)
    return rng.getrandbits(8 * length_bytes).to_bytes(length_bytes, "big")


# ---------------------------------------------------------------------------
# Core measurement
# ---------------------------------------------------------------------------


def run_arm(arm_name, arm_kind, payload, game_idx, record_trajectory=True):
    """Encode + decode one game in one arm. Returns a metrics dict."""
    board = chess.Board()
    traj = [] if record_trajectory else None
    cb = (lambda san, bits: traj.append(bits)) if record_trajectory else None
    t0 = time.perf_counter()
    try:
        if arm_kind == "integer":
            res = INTEGER.encode_message(board, payload, on_move=cb)
            opts = None
        else:
            opts = STEER_OPTS if arm_kind == "steered" else PLAIN_OPTS
            res = ARITH.encode_message(board, payload, opts, on_move=cb)
        encode_s = time.perf_counter() - t0
        headers = make_headers("integer" if arm_kind == "integer" else "arithmetic",
                               opts or PLAIN_OPTS)
        pgn = build_pgn(chess.Board(), res.sans, headers)
    except RuntimeError as e:
        return {
            "game": game_idx, "arm": arm_name, "success": False,
            "error": str(e), "encode_s": time.perf_counter() - t0, "decode_s": 0.0,
            "runtime_s": time.perf_counter() - t0,
            "plies": None, "bits": None, "bpm": None, "exact_match": False,
        }
    t1 = time.perf_counter()
    try:
        if arm_kind == "integer":
            out = INTEGER.decode_pgn(pgn)
        else:
            out = ARITH.decode_pgn(pgn, opts)
        decode_s = time.perf_counter() - t1
        exact = out == payload
        err = ""
    except RuntimeError as e:
        decode_s = time.perf_counter() - t1
        exact, err = False, str(e)
    bits = res.target_bits
    res_dict = {
        "game": game_idx, "arm": arm_name, "success": True, "error": err,
        "plies": res.plies, "bits": bits, "bits_emitted": res.bits_used,
        "bpm": bits / res.plies if res.plies else None,
        "exact_match": exact, "encode_s": encode_s, "decode_s": decode_s,
        "runtime_s": encode_s + decode_s,
    }
    if record_trajectory:
        res_dict["trajectory"] = traj
    return res_dict


def run_corpus(n_games, payload_len, start=0, verbose_every=50, label=""):
    """Run all arms over n corpus games. Returns (rows_by_arm, games_meta)."""
    games = load_starting_positions(start + n_games)[start:]
    rows = {name: [] for name, _ in ARMS}
    t_start = time.perf_counter()
    for k, (gidx, orig_plies) in enumerate(games):
        payload = payload_for(gidx, payload_len)
        for arm_name, kind in ARMS:
            rows[arm_name].append(run_arm(arm_name, kind, payload, gidx))
        if verbose_every and (k + 1) % verbose_every == 0:
            el = time.perf_counter() - t_start
            print(f"  [{label}] {k + 1}/{len(games)} games done ({el:.1f}s elapsed)", flush=True)
    return rows, games


def arm_summary(rows):
    ok = [r for r in rows if r["success"]]
    bpm = [r["bpm"] for r in ok]
    rt = [r["runtime_s"] for r in ok]
    return {
        "n": len(rows),
        "n_success": len(ok),
        "capacity_failure_rate": 1.0 - len(ok) / len(rows) if rows else float("nan"),
        "decode_error_rate": 1.0 - sum(r["exact_match"] for r in ok) / len(ok) if ok else float("nan"),
        "exact_match_rate": sum(r["exact_match"] for r in ok) / len(ok) if ok else float("nan"),
        "mean_bpm": mean(bpm), "std_bpm": stdev(bpm),
        "mean_plies": mean([r["plies"] for r in ok]),
        "mean_bits": mean([r["bits"] for r in ok]),
        "mean_runtime_s": mean(rt), "std_runtime_s": stdev(rt),
        "mean_encode_s": mean([r["encode_s"] for r in ok]),
        "mean_decode_s": mean([r["decode_s"] for r in ok]),
    }


# ---------------------------------------------------------------------------
# Pilot
# ---------------------------------------------------------------------------


def pilot():
    RESULTS.mkdir(exist_ok=True)
    print(f"Pilot: {PILOT_GAMES} games, calibrating payload length over {PAYLOAD_LADDER}", flush=True)
    calibration = []
    chosen = None
    for L in PAYLOAD_LADDER:
        print(f"Calibration attempt: payload = {L} bytes ({32 + 8 * L} bits incl. header)", flush=True)
        rows, _ = run_corpus(PILOT_GAMES, L, label=f"pilot/L={L}")
        sums = {name: arm_summary(rs) for name, rs in rows.items()}
        min_rate = min(s["n_success"] / s["n"] for s in sums.values())
        calibration.append({
            "payload_bytes": L, "payload_bits_total": 32 + 8 * L,
            "success_rates": {k: s["n_success"] / s["n"] for k, s in sums.items()},
            "min_success_rate": min_rate, "accepted": min_rate >= MIN_SUCCESS,
            "summaries": sums,
        })
        print(f"  min success rate across arms: {min_rate:.2f}", flush=True)
        if min_rate >= MIN_SUCCESS:
            chosen = L
            break
    if chosen is None:
        raise RuntimeError("No payload length on the ladder achieved >=95% success in all arms.")

    out = {
        "kind": "pilot",
        "n_games": PILOT_GAMES,
        "payload_bytes": chosen,
        "payload_bits_total": 32 + 8 * chosen,
        "global_seed": GLOBAL_SEED,
        "steering": {"beta": STEER_OPTS.beta, "window": STEER_OPTS.window},
        "corpus": CORPUS.name,
        "min_success_threshold": MIN_SUCCESS,
        "calibration": calibration,
        "summaries": {name: arm_summary(rs) for name, rs in rows.items()},
    }
    with open(RESULTS / "pilot.json", "w") as f:
        json.dump(out, f, indent=1)
    print(f"Saved results/pilot.json (payload {chosen} bytes)", flush=True)
    return out


# ---------------------------------------------------------------------------
# Full run
# ---------------------------------------------------------------------------


def full(n_games=FULL_GAMES):
    RESULTS.mkdir(exist_ok=True)
    pilot_path = RESULTS / "pilot.json"
    if pilot_path.exists():
        payload_len = json.loads(pilot_path.read_text())["payload_bytes"]
    else:
        payload_len = 32
    print(f"Full run: {n_games} games, payload = {payload_len} bytes "
          f"({32 + 8 * payload_len} bits incl. header)", flush=True)
    rows, games = run_corpus(n_games, payload_len, label="full")
    summaries = {name: arm_summary(rs) for name, rs in rows.items()}

    # Paired comparisons on games where both arms of the pair succeeded.
    def paired(name_a, name_b, key):
        a = {r["game"]: r for r in rows[name_a] if r["success"]}
        b = {r["game"]: r for r in rows[name_b] if r["success"]}
        common = sorted(set(a) & set(b))
        xa = [a[g][key] for g in common]
        xb = [b[g][key] for g in common]
        t, df, p = paired_t_test(xa, xb)
        return {
            "n_pairs": len(common),
            f"mean_{key}_{name_a}": mean(xa), f"mean_{key}_{name_b}": mean(xb),
            "t_stat": t, "df": df, "p_value": p, "cohens_d": cohens_d(xa, xb),
        }

    bpm_a1a3 = paired("arm1_integer_baseline", "arm3_arithmetic_steered", "bpm")
    rt_a1a3 = paired("arm1_integer_baseline", "arm3_arithmetic_steered", "runtime_s")
    bpm_a2a3 = paired("arm2_arithmetic_plain", "arm3_arithmetic_steered", "bpm")
    bpm_a1a2 = paired("arm1_integer_baseline", "arm2_arithmetic_plain", "bpm")

    imp_a1 = 100 * (summaries["arm3_arithmetic_steered"]["mean_bpm"]
                    / summaries["arm1_integer_baseline"]["mean_bpm"] - 1)
    imp_a2 = 100 * (summaries["arm3_arithmetic_steered"]["mean_bpm"]
                    / summaries["arm2_arithmetic_plain"]["mean_bpm"] - 1)

    out = {
        "kind": "full",
        "n_games": n_games,
        "payload_bytes": payload_len,
        "payload_bits_total": 32 + 8 * payload_len,
        "global_seed": GLOBAL_SEED,
        "steering": {"beta": STEER_OPTS.beta, "window": STEER_OPTS.window},
        "corpus": CORPUS.name,
        "summaries": summaries,
        "comparisons": {
            "steered_vs_integer_baseline_bpm": bpm_a1a3,
            "steered_vs_integer_baseline_runtime": rt_a1a3,
            "steered_vs_arithmetic_plain_bpm": bpm_a2a3,
            "plain_vs_integer_baseline_bpm": bpm_a1a2,
        },
        "improvement_pct_vs_integer_baseline": imp_a1,
        "improvement_pct_vs_arithmetic_plain": imp_a2,
    }
    with open(RESULTS / "benchmark_results.json", "w") as f:
        json.dump(out, f, indent=1)
    if INCLUDE_ROWS:
        with open(ROWS_PATH, "w") as f:
            json.dump({"n_games": n_games, "payload_bytes": payload_len, "rows": rows}, f)

    # Per-game CSV.
    with open(RESULTS / "benchmark.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["game", "arm", "success", "plies", "bits", "bpm", "exact_match",
                    "encode_s", "decode_s", "runtime_s", "error"])
        for name, _ in ARMS:
            for r in rows[name]:
                w.writerow([r["game"], r["arm"], r["success"], r.get("plies"), r.get("bits"),
                            "" if r.get("bpm") is None else f"{r['bpm']:.6f}",
                            r.get("exact_match"), f"{r['encode_s']:.6f}", f"{r['decode_s']:.6f}",
                            f"{r.get('runtime_s', 0):.6f}", r.get("error", "")])
    write_summary_md(out)
    print("Saved results/benchmark_results.json, results/benchmark.csv, results/summary.md", flush=True)
    return out


def write_summary_md(out):
    s = out["summaries"]
    c = out["comparisons"]
    a1, a2, a3 = (s["arm1_integer_baseline"], s["arm2_arithmetic_plain"],
                  s["arm3_arithmetic_steered"])
    lines = [
        "# Benchmark summary",
        "",
        f"- Games: {out['n_games']} (real Lichess corpus: `{out['corpus']}`, first "
        f"{out['n_games']} standard games; encoding always starts from the standard position)",
        f"- Payload: {out['payload_bytes']} seeded-random bytes per game "
        f"({out['payload_bits_total']} bits incl. 32-bit header), identical across arms (matched pairs)",
        f"- Steering: beta={out['steering']['beta']}, window W={out['steering']['window']} plies",
        f"- Global seed: {out['global_seed']}",
        "",
        "## Per-arm metrics",
        "",
        "| Metric | arm1 integer baseline | arm2 arithmetic plain | arm3 arithmetic steered |",
        "|---|---|---|---|",
        f"| Mean BPM | {a1['mean_bpm']:.4f} | {a2['mean_bpm']:.4f} | {a3['mean_bpm']:.4f} |",
        f"| Std BPM | {a1['std_bpm']:.4f} | {a2['std_bpm']:.4f} | {a3['std_bpm']:.4f} |",
        f"| Decode error rate | {100 * a1['decode_error_rate']:.3f}% | "
        f"{100 * a2['decode_error_rate']:.3f}% | {100 * a3['decode_error_rate']:.3f}% |",
        f"| Mean plies used | {a1['mean_plies']:.1f} | {a2['mean_plies']:.1f} | {a3['mean_plies']:.1f} |",
        f"| Mean encode+decode CPU runtime (s/game) | {a1['mean_runtime_s']:.4f} | "
        f"{a2['mean_runtime_s']:.4f} | {a3['mean_runtime_s']:.4f} |",
        f"| Capacity failure rate | {100 * a1['capacity_failure_rate']:.1f}% | "
        f"{100 * a2['capacity_failure_rate']:.1f}% | {100 * a3['capacity_failure_rate']:.1f}% |",
        "",
        "## Improvement",
        "",
        f"- arm3 vs arm1 (integer baseline): {out['improvement_pct_vs_integer_baseline']:.2f}% BPM improvement",
        f"- arm3 vs arm2 (plain arithmetic): {out['improvement_pct_vs_arithmetic_plain']:.2f}% BPM improvement",
        "",
        "## Paired statistics (arm3 steered vs arm1 integer baseline)",
        "",
        f"- BPM: n={c['steered_vs_integer_baseline_bpm']['n_pairs']}, "
        f"t={c['steered_vs_integer_baseline_bpm']['t_stat']:.3f}, "
        f"df={c['steered_vs_integer_baseline_bpm']['df']}, "
        f"p={c['steered_vs_integer_baseline_bpm']['p_value']:.3e}, "
        f"Cohen's d={c['steered_vs_integer_baseline_bpm']['cohens_d']:.3f}",
        f"- Runtime: t={c['steered_vs_integer_baseline_runtime']['t_stat']:.3f}, "
        f"p={c['steered_vs_integer_baseline_runtime']['p_value']:.3e}, "
        f"Cohen's d={c['steered_vs_integer_baseline_runtime']['cohens_d']:.3f}",
        "",
        "## Other paired comparisons",
        "",
        f"- BPM arm2 plain vs arm1 integer: p={c['plain_vs_integer_baseline_bpm']['p_value']:.3e}, "
        f"d={c['plain_vs_integer_baseline_bpm']['cohens_d']:.3f}",
        f"- BPM arm3 steered vs arm2 plain: p={c['steered_vs_arithmetic_plain_bpm']['p_value']:.3e}, "
        f"d={c['steered_vs_arithmetic_plain_bpm']['cohens_d']:.3f}",
        "",
    ]
    (RESULTS / "summary.md").write_text("\n".join(lines))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "pilot"
    if mode == "pilot":
        pilot()
    elif mode == "full":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else FULL_GAMES
        full(n)
    else:
        raise SystemExit("usage: benchmark.py [pilot|full [n_games]]")
