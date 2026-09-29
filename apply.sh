#!/bin/bash
# KiCad のソースにこのリポジトリの改良を当てる。
# 使い方: ./apply.sh [版] [KiCad のソースの場所]
#   版は patches/ の下のフォルダ名（既定 10.0.6。KiCad 8 形式のまま使うなら 8.0.9）
#   ソースの場所を省くと ~/src/kicad-<版> に、その版のタグを取ってくる
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
VER=${1:-10.0.6}
SRC=${2:-$HOME/src/kicad-$VER}
P="$HERE/patches/$VER"
[ -d "$P" ] || { echo "その版のパッチが無い: $VER（あるのは: $(ls "$HERE/patches" | tr '\n' ' ')）" >&2; exit 2; }
BASE=$(sed -n 's/^base commit: //p' "$P/BASE.txt")
if ! git -C "$SRC" rev-parse --git-dir >/dev/null 2>&1; then
  git clone --depth 1 --branch "$VER" https://github.com/KiCad/kicad-source-mirror "$SRC"
fi
cd "$SRC"
if [ "$(git rev-parse HEAD)" != "$BASE" ]; then
  echo "注意: $SRC は $VER ($BASE) ではない。当たらない可能性がある" >&2
fi
if [ -f "$P/ALL-combined.patch" ]; then
  # 8.0.9: 00〜08 をまとめたもの（新しいファイルも含む）。個々のパッチは来歴の記録
  git apply --check --binary "$P/ALL-combined.patch"
  git apply --binary --whitespace=nowarn "$P/ALL-combined.patch"
else
  # 10.0.6 以降: 番号順のパッチ（本家の MR は作者の名前つき）
  for f in "$P"/[0-9]*.patch; do
    git apply --binary --whitespace=nowarn "$f" || { echo "当たらない: $(basename "$f")" >&2; exit 1; }
  done
fi
echo "当てた: $SRC（$VER。ビルドの手順は README）"
