#!/bin/bash
# KiCad 8.0.9 のソースにこのリポジトリの改良を当てる。
# 使い方: ./apply.sh [KiCad のソースの場所]   （省略すると ~/src/kicad-8.0.9 に 8.0.9 を取ってくる）
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
SRC=${1:-$HOME/src/kicad-8.0.9}
BASE=9c4f534cf1a8fa5b2f3e7d4e1804fad681de921a   # 8.0.9 のタグのコミット
if [ ! -d "$SRC/.git" ]; then
  git clone --depth 1 --branch 8.0.9 https://github.com/KiCad/kicad-source-mirror "$SRC"
fi
cd "$SRC"
if [ "$(git rev-parse HEAD)" != "$BASE" ]; then
  echo "注意: $SRC は 8.0.9 ($BASE) ではない。当たらない可能性がある" >&2
fi
# 00〜08 をまとめたもの（新しいファイルも含む）。個々のパッチは来歴の記録として patches/ にある
git apply --check --binary "$HERE/patches/ALL-combined.patch"
git apply --binary --whitespace=nowarn "$HERE/patches/ALL-combined.patch"
echo "当てた: $SRC（ビルドの手順は README）"
