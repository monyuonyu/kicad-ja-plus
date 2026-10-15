#!/bin/bash
# 材料のリスト（corpus.txt）を作り直す。GitHub の言語の分類で、星の多い順に
# 使い方: tools/bughunt/make_corpus_list.sh > tools/bughunt/corpus.txt   （gh にログインしていること）
set -euo pipefail
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
for L in "KiCad Layout" "KiCad Schematic" "KiCad Legacy Layout"; do
  gh search repos --language "$L" --sort stars --limit 300 --size "<300000" --json fullName \
    -q '.[]|"kicad https://github.com/\(.fullName)"'
done > "$T/list"
gh search repos --language Eagle --sort stars --limit 80 --size "<300000" --json fullName \
  -q '.[]|"eagle https://github.com/\(.fullName)"' >> "$T/list"
echo "# バグ探しの材料（公開リポジトリ）。1 行 = 種類 URL コミット"
echo "# 種類: kicad（KiCad の設計を含む） / eagle（Eagle の .brd/.sch/.lbr。取り込みの試験）"
echo "# 作り直し: tools/bughunt/make_corpus_list.sh > tools/bughunt/corpus.txt（$(date +%F) に GitHub の星の順で作成）"
# 今の HEAD のコミットで固定する（ls-remote の 1 行目だけ）
awk '!seen[$2]++' "$T/list" | while read -r kind url; do
  sha=$(timeout 30 git ls-remote "$url" HEAD 2>/dev/null | head -1 | cut -f1) || true
  [ -n "$sha" ] && echo "$kind $url $sha"
done | sort -k1,1 -k2,2
