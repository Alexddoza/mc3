#!/bin/bash
set -euo pipefail
[[ $# -eq 2 ]] || { echo 'Usage: bash tools/build-macos.sh /absolute/game-root /absolute/new-output-dir'; exit 2; }
BASE=$(cd "$(dirname "$0")/.." && pwd)
GAME=$(cd "$1" && pwd)
OUT=$2
bash "$BASE/tools/build-shell-macos.sh" "$OUT"
python3 "$BASE/tools/bundle-private-game.py" --shell-ipa "$OUT/MC3-shell-unsigned.ipa" --game-root "$GAME" --output "$OUT/MC3-unsigned.ipa"
