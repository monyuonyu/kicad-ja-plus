#!/usr/bin/env python3
"""kicad-local autoroute: 基板全体を Freerouting で自動配線する（画面なし。pcbnew を使う。kicad-python で動かす）

使い方:
  kicad-local autoroute <入力.kicad_pcb> <出力.kicad_pcb> [--passes 20] [--timeout 600] [--threads N]
                        [--rip-up] [--jar freerouting.jar] [--json]

流れ: 元の基板の未配線を数える → DSN に書き出す → Freerouting（画面なし）→ SES を取り込む →
      ベタを塗り直す → 出力に保存（入力は変えない）→ 出力を DRC にかけ、未配線と新しく増えた違反を出す。

  --rip-up   既存の配線とビアを剥がしてから配線し直す（既定は、今ある配線を残して足りない所だけ）
  --jar      Freerouting の jar（既定: 環境変数 FREEROUTING_JAR、~/.local/share/kicad-ja-plus/freerouting.jar ほか）
             入手先: https://github.com/freerouting/freerouting/releases （Java 21 以降が要る）

終了コード: 0 = 未配線なし・新しい違反なし、1 = 残った、2 = 使い方の誤り・実行できない
"""
import argparse
import collections
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pcbnew

JARS = ["~/.local/share/kicad-ja-plus/freerouting.jar", "~/.local/share/freerouting/freerouting.jar",
        "~/cad-mcp-lab/tools/freerouting/freerouting-2.4.1.jar"]
NOISE = ("lib_footprint_mismatch", "lib_footprint_issues", "silk_edge_clearance", "silk_overlap", "silk_over_copper")


def find_jar(arg):
    for c in [arg, os.environ.get("FREEROUTING_JAR")] + JARS:
        if c and Path(c).expanduser().is_file():
            return Path(c).expanduser()
    return None


def drc(path):
    out = Path(tempfile.mkdtemp()) / "drc.json"
    subprocess.run(["kicad-cli-local", "pcb", "drc", "--format", "json", "-o", str(out), str(path)],
                   capture_output=True, text=True)
    return json.loads(out.read_text(encoding="utf-8")) if out.exists() else None


def types(d):
    return collections.Counter(v["type"] for v in d.get("violations", []) if v["type"] not in NOISE)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n", 2)[2])
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--passes", type=int, default=20)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--threads", type=int)
    ap.add_argument("--rip-up", action="store_true")
    ap.add_argument("--jar")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    src, dst = Path(a.input), Path(a.output)
    if not src.is_file():
        sys.stderr.write(f"基板がない: {src}\n")
        return 2
    jar = find_jar(a.jar)
    if jar is None:
        sys.stderr.write("Freerouting の jar が見つからない。--jar か FREEROUTING_JAR で指定する\n"
                         "入手先: https://github.com/freerouting/freerouting/releases\n")
        return 2
    if not shutil.which("java"):
        sys.stderr.write("java が無い（Freerouting には Java 21 以降が要る）\n")
        return 2

    before = drc(src)
    if before is None:
        sys.stderr.write(f"DRC を実行できない: {src}\n")
        return 2
    work = Path(tempfile.mkdtemp(prefix="kautoroute_"))
    board = pcbnew.LoadBoard(str(src))
    ripped = 0
    if a.rip_up:
        for t in list(board.GetTracks()):
            board.Remove(t)
            ripped += 1
    dsn, ses = work / "board.dsn", work / "board.ses"
    if not pcbnew.ExportSpecctraDSN(board, str(dsn)):
        sys.stderr.write("DSN に書き出せない\n")
        return 2

    cmd = ["java", "-jar", str(jar), "-de", str(dsn), "-do", str(ses), "-mp", str(a.passes),
           "--gui.enabled=false"]
    if a.threads:
        cmd += ["-mt", str(a.threads)]
    log = work / "freerouting.log"
    try:
        with open(log, "w") as f:
            r = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, timeout=a.timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
    if not ses.exists():
        sys.stderr.write(f"Freerouting が結果を出さなかった（{'時間切れ' if timed_out else '失敗'}）。ログ: {log}\n")
        return 2

    # SES は配線とビアをすべて置き換えるので、取り込む前に剥がす
    for t in list(board.GetTracks()):
        board.Remove(t)
    if not pcbnew.ImportSpecctraSES(board, str(ses)):
        sys.stderr.write(f"SES を取り込めない: {ses}\n")
        return 2
    board.BuildListOfNets()
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(str(dst), board)
    pro = src.with_suffix(".kicad_pro")
    if pro.exists() and not dst.with_suffix(".kicad_pro").exists():
        shutil.copy(pro, dst.with_suffix(".kicad_pro"))

    after = drc(dst)
    new_types = types(after) - types(before)
    tracks = sum(1 for t in board.GetTracks() if not isinstance(t, pcbnew.PCB_VIA))
    vias = sum(1 for t in board.GetTracks() if isinstance(t, pcbnew.PCB_VIA))
    res = {"input": str(src), "output": str(dst), "unconnected_before": len(before.get("unconnected_items", [])),
           "unconnected_after": len(after.get("unconnected_items", [])), "tracks": tracks, "vias": vias,
           "ripped": ripped, "new_violations": dict(new_types), "timed_out": timed_out, "log": str(log),
           "unconnected": [" ⇔ ".join(i.get("description", "") for i in v.get("items", [])[:2])
                           for v in after.get("unconnected_items", [])]}
    res["ok"] = res["unconnected_after"] == 0 and not new_types
    if a.json:
        json.dump(res, sys.stdout, ensure_ascii=False, indent=1)
        print()
    else:
        print(f"未配線 {res['unconnected_before']} → {res['unconnected_after']}、配線 {tracks} 本・ビア {vias} 個"
              + (f"（剥がした {ripped}）" if ripped else "") + (f"、時間切れ（{a.timeout} 秒）" if timed_out else ""))
        for u in res["unconnected"][:10]:
            print(f"  残った: {u}")
        for t, n in new_types.items():
            print(f"  ! 新しく増えた違反: {t} {n} 件")
        print(f"→ {dst}（Freerouting のログ: {log}）")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
