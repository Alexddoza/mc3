#!/bin/bash
set -euo pipefail
[[ $# -eq 2 ]] || { echo 'Usage: bash tools/build-macos.sh /absolute/game.iso /absolute/new-output-dir'; exit 2; }
BASE=$(cd "$(dirname "$0")/.." && pwd)
[[ $1 == /* && -f $1 ]] || { echo 'Game ISO must be an absolute path to a regular file.' >&2; exit 1; }
GAME=$1
OUT=$2
bash "$BASE/tools/build-shell-macos.sh" "$OUT"
python3 "$BASE/tools/bundle-private-game.py" --shell-ipa "$OUT/MC3-shell-unsigned.ipa" --game-iso "$GAME" --output "$OUT/MC3-unsigned.ipa"
