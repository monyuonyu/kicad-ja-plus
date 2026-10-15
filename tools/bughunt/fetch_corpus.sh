#!/bin/bash
# corpus.txt のリポジトリを、決めたコミットで取ってくる（設計のファイルだけ。blob は要る物だけ落とす）
# 使い方: tools/bughunt/fetch_corpus.sh <corpus.txt> <置き場所> [並列数]
set -uo pipefail
LIST=$1; OUT=$2; JOBS=${3:-8}
mkdir -p "$OUT"
fetch_one() {
  kind=$1; url=$2; sha=$3; out=$4
  d="$out/$(echo "$url" | sed 's#https://github.com/##; s#/#__#')"
  [ -f "$d/.bughunt-ok" ] && return 0
  rm -rf "$d"; mkdir -p "$d"
  (
    cd "$d" || exit 1
    git init -q . && git remote add origin "$url"
    git config core.sparseCheckout true
    # 大文字小文字の違いも拾う（Eagle や Altium のファイル名はまちまち）
    cat > .git/info/sparse-checkout <<'EOF'
*.kicad_pcb
*.kicad_sch
*.kicad_pro
*.kicad_prl
*.kicad_dru
*.kicad_sym
*.kicad_mod
*.kicad_wks
*.kicad_dbl
fp-lib-table
sym-lib-table
*.sch
*.SCH
*.brd
*.BRD
*.lib
*.dcm
*.mod
*.pro
*.lbr
*.LBR
*.PcbDoc
*.pcbdoc
*.SchDoc
*.PcbLib
*.SchLib
*.gbr
*.GBR
*.gtl
*.GTL
*.gbl
*.GBL
*.gko
*.GKO
*.gm1
*.drl
*.DRL
*.xln
EOF
    timeout 600 git -c protocol.version=2 fetch -q --depth 1 --filter=blob:none origin "$sha" &&
      timeout 600 git checkout -q FETCH_HEAD
  ) >"$d.log" 2>&1 && { touch "$d/.bughunt-ok"; rm -f "$d.log"; echo "ok $url"; } || echo "NG $url（$d.log）"
}
export -f fetch_one
grep -v '^#' "$LIST" | awk 'NF==3' | xargs -P "$JOBS" -L1 bash -c 'fetch_one "$0" "$1" "$2" "'"$OUT"'"' > "$OUT.fetch.txt"
grep '^NG' "$OUT.fetch.txt" | head -20
grep -v '^#' "$LIST" | awk 'NF==3' | wc -l | xargs echo "リスト:"
ls -d "$OUT"/*/.bughunt-ok 2>/dev/null | wc -l | xargs echo "取れた:"
du -sh "$OUT" | cut -f1 | xargs echo "大きさ:"
