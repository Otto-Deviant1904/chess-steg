#!/usr/bin/env bash
# Download the benchmark corpus used by benchmark.py.
#
# The corpus is not committed (it is ~17 MB compressed, ~89 MB raw). This script
# fetches and decompresses it into data/, which is gitignored.
#
# Source: the official Lichess open database, which publishes one dump per
# month. The paper's benchmark used the January 2013 standard-rated dump; the
# first 1000 games of it are the evaluation set.
#
#   ./scripts/fetch_corpus.sh            # January 2013 (the paper's corpus)
#   ./scripts/fetch_corpus.sh 2013-06    # any other month
set -euo pipefail

MONTH="${1:-2013-01}"
YEAR="${MONTH%%-*}"
MM="${MONTH##*-}"

if [[ ! "$MM" =~ ^0[1-9]|1[0-2]$ ]]; then
  echo "error: month must look like YYYY-MM (got '$MONTH')" >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$REPO_ROOT/data"
BASE="https://database.lichess.org/standard/lichess_db_standard_rated_${YEAR}-${MM}"

mkdir -p "$DATA_DIR"
ZST="$DATA_DIR/lichess_db_standard_rated_${YEAR}-${MM}.pgn.zst"
PGN="$DATA_DIR/lichess_${YEAR}-${MM}.pgn"

if [[ -f "$PGN" ]]; then
  echo "==> $PGN already exists ($(du -h "$PGN" | cut -f1)); nothing to do"
  exit 0
fi

echo "==> downloading $BASE.pgn.zst"
if command -v curl >/dev/null 2>&1; then
  curl -fSL --retry 3 -o "$ZST" "$BASE.pgn.zst"
else
  wget -O "$ZST" "$BASE.pgn.zst"
fi

echo "==> decompressing to $PGN"
if command -v zstd >/dev/null 2>&1; then
  zstd -d -f "$ZST" -o "$PGN"
elif command -v unzstd >/dev/null 2>&1; then
  unzstd -f "$ZST" -o "$PGN"
elif .venv/bin/python -c "import zstandard" 2>/dev/null || python3 -c "import zstandard" 2>/dev/null; then
  python3 - "$ZST" "$PGN" <<'PY'
import sys
try:
    import zstandard
except ImportError:
    import subprocess, importlib.util
    raise SystemExit("install zstandard: pip install zstandard")
with open(sys.argv[1], "rb") as fin, open(sys.argv[2], "wb") as fout:
    zstandard.ZstdDecompressor().copy_stream(fin, fout)
PY
else
  echo "error: no zstd decompressor found. Install one, e.g. 'apt install zstd'" >&2
  echo "       or run: pip install zstandard" >&2
  exit 1
fi

rm -f "$ZST"
echo "==> done: $PGN ($(du -h "$PGN" | cut -f1))"
echo "    Now run: .venv/bin/python benchmark.py full 1000"
