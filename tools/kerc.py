#!/usr/bin/env python3
"""kicad-local erc: ERC を回して要点を出す

使い方:
  kicad-local erc <回路図.kicad_sch> [--json]

終了コード: 0 = 要確認の項目なし、1 = あり、2 = 使い方の誤り・ERC を実行できない
ライブラリとの差分（lib_symbol_issues など）は参考として数えない。
"""
import argparse
import collections
import json
import subprocess
import sys
import tempfile
from pathlib import Path

NOISE = ("lib_symbol_issues", "lib_symbol_mismatch", "footprint_link_issues")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n", 2)[2])
    ap.add_argument("schematic")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    sch = Path(a.schematic)
    if not sch.is_file():
        sys.stderr.write(f"回路図がない: {sch}\n")
        return 2
    out = Path(tempfile.mkdtemp()) / "erc.json"
    r = subprocess.run(["kicad-cli-local", "sch", "erc", "--format", "json", "-o", str(out), str(sch)],
                       capture_output=True, text=True)
    if not out.exists():
        sys.stderr.write(f"ERC を実行できない: {sch}\n{r.stdout}{r.stderr}")
        return 2
    d = json.loads(out.read_text(encoding="utf-8"))
    arr = [dict(v, sheet=s.get("path", "")) for s in d.get("sheets", []) for v in s.get("violations", [])]
    main_ = [v for v in arr if v["type"] not in NOISE]
    if a.json:
        json.dump({"schematic": str(sch), "ok": not main_, "problems": len(main_), "violations": arr,
                   "noise_types": list(NOISE)}, sys.stdout, ensure_ascii=False, indent=1)
        print()
    else:
        c = collections.Counter(v["type"] for v in arr)
        print(f"ERC: {len(arr)} 件" + (f"(うち要確認 {len(main_)} 件)" if len(main_) != len(arr) else ""))
        for t, n in c.most_common():
            mark = "  " if t in NOISE else "! "
            print(f"  {mark}{t}: {n}")
            if t not in NOISE:
                for v in [x for x in arr if x["type"] == t][:8]:
                    p = (v.get("items") or [{}])[0].get("pos")
                    ps = f"({p['x']:.2f}, {p['y']:.2f})" if p else ""
                    print(f"       {ps} {v.get('sheet', '')} " + " ⇔ ".join(i.get("description", "")[:48]
                                                                          for i in v.get("items", [])[:2]))
        print("(先頭が ! の項目が要確認。ライブラリ差分は参考)")
    return 0 if not main_ else 1


if __name__ == "__main__":
    sys.exit(main())
