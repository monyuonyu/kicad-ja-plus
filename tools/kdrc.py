#!/usr/bin/env python3
"""kicad-local drc: DRC を回して要点を出す（KiCad 8 と 10 の基板に対応）

使い方:
  kicad-local drc <基板.kicad_pcb> [--refill] [--strict] [--json]

  --refill   ベタを塗り直してから調べる（基板のファイルは変えない）
  --strict   KiCad の DRC が「違反 0」でも見逃す次の 3 つも調べる（--refill も付く）
               1. ネットの無いパッド（同じ部品のほかのパッドはネットにつながっている）
               2. ネットクラスより細い配線（KiCad は既定でネットクラスの幅を強制しない）
               3. 古いベタ（塗り直すと DRC の結果が変わる）
  --json     結果を JSON で出す（機械が読む用）

終了コード: 0 = 要確認の項目なし、1 = あり、2 = 使い方の誤り・DRC を実行できない
回路図がある場合は、回路図との整合（--schematic-parity）も必ず調べる。
"""
import argparse
import collections
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sx  # noqa: E402

# 参考扱い（要確認に数えない）: ライブラリとの差分・シルクの警告
NOISE = ("lib_footprint_mismatch", "lib_footprint_issues", "silk_edge_clearance", "silk_overlap",
         "silk_over_copper", "lib_symbol_issues")
TOL_MM = 0.0005   # 幅の比べ方の許容（丸め）


def run_drc(board, refill):
    """kicad-cli の DRC を JSON で。refill ならベタを塗り直してから（基板は保存しない）"""
    out = Path(tempfile.mkdtemp()) / "drc.json"
    cmd = ["kicad-cli-local", "pcb", "drc", "--schematic-parity", "--format", "json", "-o", str(out)]
    if refill:
        cmd.append("--refill-zones")
    r = subprocess.run(cmd + [str(board)], capture_output=True, text=True)
    if not out.exists():
        sys.stderr.write(f"DRC を実行できない: {board}\n{r.stdout}{r.stderr}")
        sys.exit(2)
    return json.loads(out.read_text(encoding="utf-8"))


NET_NAMES = {}   # KiCad 8 の番号 → 名前（基板の先頭の (net 12 "/X") の一覧）


def load_net_names(board):
    NET_NAMES.clear()
    for n in sx.find(board, "net"):
        if len(n) > 2 and not isinstance(n[1], sx.Q):
            NET_NAMES[str(n[1])] = str(n[2])


def net_of(e):
    """ネットの名前。KiCad 8 は (net 12) と番号だけのことがある（配線）、パッドは (net 3 "GND")、
    KiCad 10 は (net "GND")。どれでも名前を返す。無ければ ''"""
    n = sx.one(e, "net")
    if n is None:
        return ""
    names = [x for x in n[1:] if isinstance(x, sx.Q)]
    if names:
        return str(names[-1])
    return NET_NAMES.get(str(n[1]), "") if len(n) > 1 and str(n[1]) != "0" else ""


def val(e, name):
    x = sx.one(e, name)
    return x[1] if x is not None and len(x) > 1 else None


def pad_is_copper(pad):
    layers = sx.one(pad, "layers")
    return layers is not None and any(str(l).endswith(".Cu") for l in layers[1:])


def pad_position(fp, pad):
    """パッドの基板上の位置（部品の位置と向きを反映）"""
    import math
    fa, pa = sx.one(fp, "at"), sx.one(pad, "at")
    if fa is None:
        return None
    fx, fy = float(fa[1]), float(fa[2])
    rot = math.radians(float(fa[3])) if len(fa) > 3 and str(fa[3]).replace(".", "", 1).lstrip("-").isdigit() else 0.0
    px, py = (float(pa[1]), float(pa[2])) if pa is not None else (0.0, 0.0)
    # KiCad の回転（y 下向きの座標で、正の角度は反時計回りに見える）
    x = fx + px * math.cos(rot) + py * math.sin(rot)
    y = fy - px * math.sin(rot) + py * math.cos(rot)
    return {"x": round(x, 3), "y": round(y, 3)}


def schematic_pins(board):
    """回路図のネットリストで「どこかにつながる」ピン {(ref, pin)}。回路図が無ければ None"""
    sch = board.with_suffix(".kicad_sch")
    if not sch.is_file():
        return None
    sys.path.insert(0, str(Path(__file__).resolve().parent / "ksim"))
    try:
        import ksim
        _, nets = ksim.parse_netlist(ksim.export_netlist(str(sch)))
    except SystemExit:
        return None
    return {(r, p) for name, nodes in nets.items() if not name.startswith("unconnected-") and len(nodes) > 1
            for r, p, _ in nodes}


def netless_pads(board, sch_pins):
    """ネットの無い銅のパッドのうち、つながるはずのもの。
    回路図があれば、そのピンが回路図でどこかにつながっているものだけ（回路図と基板の同期漏れ）。
    無ければ、同じ部品のほかのパッドはつながっているのにネットの無いもの（番号 0 や空の金具は除く。推測）"""
    issues, seen = [], set()
    for fp in sx.find(board, "footprint"):
        ref_p = sx.prop(fp, "Reference")
        ref = str(ref_p[2]) if ref_p is not None and len(ref_p) > 2 else "?"
        pads = [p for p in sx.find(fp, "pad") if len(p) > 2 and str(p[2]) != "np_thru_hole" and pad_is_copper(p)]
        with_net = [p for p in pads if net_of(p)]
        for p in pads:
            num = str(p[1])
            if net_of(p) or (ref, num) in seen:
                continue
            if sch_pins is not None:
                if (ref, num) not in sch_pins:
                    continue
                why = "回路図ではつながっている"
            else:
                if not with_net or num in ("", "0"):
                    continue
                why = "同じ部品のほかのパッドはつながっている。回路図が無いので推測"
            seen.add((ref, num))
            issues.append({"kind": "netless_pad", "ref": ref, "pad": num, "pos": pad_position(fp, p),
                           "description": f"{ref}.{num} にネットが無い（{why}）"})
    return issues


def load_netclasses(pro_path):
    """(クラス名 → 配線幅 mm, 明示の割り当て net → クラス, パターン [(pattern, クラス)])"""
    try:
        pro = json.loads(Path(pro_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ns = pro.get("net_settings", {})
    widths = {c.get("name"): c.get("track_width") for c in ns.get("classes", []) if c.get("track_width")}
    assign = {}
    for net, cls in (ns.get("netclass_assignments") or {}).items():
        assign[net] = cls[0] if isinstance(cls, list) and cls else cls
    patterns = [(p.get("pattern"), p.get("netclass")) for p in ns.get("netclass_patterns") or []]
    return widths, assign, patterns


def class_of(net, assign, patterns):
    if net in assign and assign[net]:
        return assign[net]
    for pat, cls in patterns:
        if pat and fnmatch.fnmatchcase(net, pat):
            return cls
    return "Default"


def narrow_tracks(board, pro_path):
    nc = load_netclasses(pro_path)
    if nc is None:
        return [], "プロジェクト（.kicad_pro）が無いので、ネットクラスの幅は調べていない"
    widths, assign, patterns = nc
    per_net = collections.defaultdict(lambda: {"n": 0, "min": 1e9, "pos": None})
    for kind in ("segment", "arc"):
        for t in sx.find(board, kind):
            net = net_of(t)
            w = val(t, "width")
            if not net or w is None:
                continue
            cls = class_of(net, assign, patterns)
            need = widths.get(cls)
            if need is None or float(w) >= need - TOL_MM:
                continue
            d = per_net[(net, cls, need)]
            d["n"] += 1
            if float(w) < d["min"]:
                d["min"] = float(w)
                s = sx.one(t, "start")
                d["pos"] = {"x": float(s[1]), "y": float(s[2])} if s is not None else None
    issues = [{"kind": "narrow_track", "net": net, "netclass": cls, "required_mm": need, "min_mm": d["min"],
               "count": d["n"], "pos": d["pos"],
               "description": f"{net}（{cls}）: {d['n']} 本が {d['min']:.3f}mm（ネットクラスは {need:.3f}mm）"}
              for (net, cls, need), d in sorted(per_net.items())]
    return issues, None


def vkey(v):
    p = v.get("pos") or (v.get("items") or [{}])[0].get("pos") or {}
    return (v.get("type"), round(p.get("x", 0), 2), round(p.get("y", 0), 2))


def stale_zones(board, drc_refilled):
    """塗り直しの前後で DRC の結果が違えば、ベタが古い"""
    before = run_drc(board, refill=False)
    a = collections.Counter(vkey(v) for v in before.get("violations", []) + before.get("unconnected_items", []))
    b = collections.Counter(vkey(v) for v in drc_refilled.get("violations", []) + drc_refilled.get("unconnected_items", []))
    gone, new = a - b, b - a
    if not gone and not new:
        return []
    return [{"kind": "stale_zone_fill", "disappear": sum(gone.values()), "appear": sum(new.values()),
             "description": f"ベタが古い: 塗り直すと {sum(gone.values())} 件が消え、{sum(new.values())} 件が現れる"
                            "（塗り直した結果で判定している。ファイルは KiCad で塗り直して保存すること）"}]


def items_text(v):
    return " ⇔ ".join(i.get("description", "")[:48] for i in v.get("items", [])[:2])


def human(d, strict, note):
    def show(title, arr):
        c = collections.Counter(v["type"] for v in arr)
        main = [v for v in arr if v["type"] not in NOISE]
        print(f"{title}: {len(arr)} 件" + (f"(うち要確認 {len(main)} 件)" if len(main) != len(arr) else ""))
        for t, n in c.most_common():
            mark = "  " if t in NOISE else "! "
            print(f"  {mark}{t}: {n}")
            if t not in NOISE:
                for v in [x for x in arr if x["type"] == t][:8]:
                    p = v.get("pos") or (v.get("items") or [{}])[0].get("pos")
                    ps = f"({p['x']:.2f}, {p['y']:.2f})" if p else ""
                    print(f"       {ps} {v.get('layer', '')} {items_text(v)}")
    show("DRC 違反", d.get("violations", []))
    show("未配線", d.get("unconnected_items", []))
    show("回路図との不一致", d.get("schematic_parity", []))
    if strict is not None:
        print(f"厳しい確認: {len(strict)} 件")
        for kind in ("netless_pad", "narrow_track", "stale_zone_fill"):
            arr = [s for s in strict if s["kind"] == kind]
            if arr:
                print(f"  ! {kind}: {len(arr)}")
                for s in arr[:12]:
                    p = s.get("pos")
                    ps = f"({p['x']:.2f}, {p['y']:.2f}) " if p else ""
                    print(f"       {ps}{s['description']}")
        if note:
            print(f"  （{note}）")
    print("(先頭が ! の項目が要確認。ライブラリ差分・シルクの警告は参考)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n", 2)[2])
    ap.add_argument("board")
    ap.add_argument("--refill", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    board = Path(a.board)
    if not board.is_file():
        sys.stderr.write(f"基板がない: {board}\n")
        return 2

    d = run_drc(board, refill=a.refill or a.strict)
    strict, note = None, None
    if a.strict:
        tree = sx.parse(board.read_text(encoding="utf-8"))
        load_net_names(tree)
        narrow, note = narrow_tracks(tree, board.with_suffix(".kicad_pro"))
        strict = netless_pads(tree, schematic_pins(board)) + narrow + stale_zones(board, d)

    bad = [v for v in d.get("violations", []) if v["type"] not in NOISE]
    bad += d.get("unconnected_items", []) + [v for v in d.get("schematic_parity", []) if v["type"] not in NOISE]
    problems = len(bad) + len(strict or [])

    if a.json:
        out = {"board": str(board), "ok": problems == 0, "problems": problems,
               "violations": d.get("violations", []), "unconnected_items": d.get("unconnected_items", []),
               "schematic_parity": d.get("schematic_parity", []), "noise_types": list(NOISE)}
        if strict is not None:
            out["strict"] = strict
            if note:
                out["strict_note"] = note
        json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
        print()
    else:
        human(d, strict, note)
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
