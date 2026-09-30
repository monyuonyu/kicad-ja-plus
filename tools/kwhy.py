#!/usr/bin/env python3
"""kicad-local why: 未配線の組ごとに「なぜ引けないか」を調べる（pcbnew を使う。kicad-python で動かす）

使い方:
  kicad-local why <基板.kicad_pcb> [--json] [--max N]

未配線の組（DRC の unconnected_items）の両端を結ぶ線を、配線の太さの半分とクリアランスの分だけ
膨らませた帯として、銅の層ごとに、その帯を塞ぐものを種類に分けて数える:
  keepout     配線禁止の区域（ルールエリア）
  other_net   ほかのネットの配線・ビア・パッド・ベタ
  board_edge  基板の外形
すべての銅の層を同じ種類が塞いでいれば、それを主な原因として出す。どの層にも妨げが無ければ
「直線上は空いている」（自動配線の探索の問題か、ルールの問題）。
直線で調べるので、回り道で引ける場合もある。結果は「どこを空ければ引けるか」の手がかりとして使う。

終了コード: 0 = 未配線なし、1 = 未配線あり（診断を出した）、2 = 使い方の誤り
"""
import argparse
import collections
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pcbnew

KIND_JA = {"keepout": "キープアウト（配線禁止の区域）", "other_net": "ほかのネットの銅", "board_edge": "基板の外形"}


def drc_unconnected(board_path):
    out = Path(tempfile.mkdtemp()) / "drc.json"
    r = subprocess.run(["kicad-cli-local", "pcb", "drc", "--format", "json", "-o", str(out), str(board_path)],
                       capture_output=True, text=True)
    if not out.exists():
        sys.stderr.write(f"DRC を実行できない: {board_path}\n{r.stdout}{r.stderr}")
        sys.exit(2)
    return json.loads(out.read_text(encoding="utf-8")).get("unconnected_items", [])


def iu(mm):
    return pcbnew.FromMM(mm)


def mm(v):
    return round(pcbnew.ToMM(v), 3)


def item_by_uuid(board, uuid):
    if hasattr(board, "ResolveItem"):
        it = board.ResolveItem(pcbnew.KIID(uuid), True)
    else:
        # 8.0 には ResolveItem が無い。GetItem は、見つからないと DELETED_BOARD_ITEM を返す
        it = board.GetItem(pcbnew.KIID(uuid))
        if it is not None and it.GetClass() == "DELETED_BOARD_ITEM":
            it = None
    # 共通の型（BOARD_ITEM）で返るので、パッドなどの本当の型にする
    return it.Cast() if it is not None and hasattr(it, "Cast") else it


def item_name(it):
    if isinstance(it, pcbnew.PAD):
        return f"{it.GetParentFootprint().GetReference()}.{it.GetNumber()}"
    if isinstance(it, pcbnew.PCB_VIA):
        return f"ビア({mm(it.GetPosition().x)}, {mm(it.GetPosition().y)})"
    if isinstance(it, (pcbnew.PCB_TRACK, pcbnew.PCB_ARC)):
        return f"配線({mm(it.GetStart().x)}, {mm(it.GetStart().y)})"
    if isinstance(it, pcbnew.ZONE):
        return f"ベタ({it.GetNetname() or it.GetZoneName()})"
    return it.GetClass()


def netclass_rules(pro_path, net):
    """プロジェクトの設定から、ネットのネットクラスの配線幅とクリアランス（mm）。無ければ None"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kdrc
    try:
        pro = json.loads(Path(pro_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    classes = {c.get("name"): c for c in pro.get("net_settings", {}).get("classes", [])}
    nc = kdrc.load_netclasses(pro_path)
    cls = classes.get(kdrc.class_of(net, nc[1], nc[2])) or classes.get("Default") or {}
    return cls.get("track_width"), cls.get("clearance")


def diagnose(board, a, b, net, pro_path):
    """a, b: 両端の点（VECTOR2I）。net: ネット名。層ごとの妨げ"""
    bds = board.GetDesignSettings()
    w_mm, c_mm = netclass_rules(pro_path, net)
    width = iu(w_mm) if w_mm else bds.GetCurrentTrackWidth()
    clear = iu(c_mm) if c_mm else bds.m_MinClearance
    half = width // 2
    seg = pcbnew.SHAPE_SEGMENT(a, b, 0)
    layers = [l for l in board.GetEnabledLayers().CuStack()]
    per_layer = {}
    for layer in layers:
        found = collections.defaultdict(list)
        # 配線禁止の区域
        for z in board.Zones():
            if z.GetIsRuleArea() and z.GetDoNotAllowTracks() and z.IsOnLayer(layer):
                if z.Outline().Collide(seg, half):
                    found["keepout"].append(z.GetZoneName() or "(名前なし)")
            elif not z.GetIsRuleArea() and z.IsOnLayer(layer) and z.GetNetname() != net and z.GetFilledPolysList(layer).OutlineCount():
                if z.GetFilledPolysList(layer).Collide(seg, half + clear):
                    found["other_net"].append(item_name(z))
        # ほかのネットの配線・ビア・パッド
        for t in board.GetTracks():
            if t.GetNetname() != net and t.IsOnLayer(layer):
                if t.GetEffectiveShape(layer).Collide(seg, half + max(clear, t.GetOwnClearance(layer))):
                    found["other_net"].append(item_name(t))
        for fp in board.GetFootprints():
            for p in fp.Pads():
                if p.GetNetname() != net and p.IsOnLayer(layer):
                    # 両端のパッド自身は数えない
                    if p.GetPosition() in (a, b):
                        continue
                    if p.GetEffectiveShape(layer).Collide(seg, half + max(clear, p.GetOwnClearance(layer))):
                        found["other_net"].append(item_name(p))
        # 基板の外形
        for d in board.GetDrawings():
            if d.GetLayer() == pcbnew.Edge_Cuts and d.GetEffectiveShape().Collide(seg, half + bds.m_CopperEdgeClearance):
                found["board_edge"].append("外形")
        per_layer[board.GetLayerName(layer)] = {k: sorted(set(v)) for k, v in found.items()}
    blocked = [l for l, f in per_layer.items() if f]
    free = [l for l, f in per_layer.items() if not f]
    kinds = set.intersection(*(set(f) for f in per_layer.values())) if per_layer and not free else set()
    if free:
        cause = f"直線上は {', '.join(free)} が空いている（自動配線の探索の問題か、ルールの問題）"
    elif kinds:
        cause = "すべての層を塞いでいる: " + "、".join(KIND_JA[k] for k in sorted(kinds))
    else:
        cause = "層ごとに違うものが塞いでいる（ビアで層を替えれば通れるかもしれない）"
    return {"net": net, "from": {"x": mm(a.x), "y": mm(a.y)}, "to": {"x": mm(b.x), "y": mm(b.y)},
            "length_mm": mm((b - a).EuclideanNorm()), "track_width_mm": mm(width), "clearance_mm": mm(clear),
            "layers": per_layer, "blocked_layers": blocked, "free_layers": free, "main_cause": sorted(kinds),
            "summary": cause}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n", 2)[2])
    ap.add_argument("board")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max", type=int, default=50, help="調べる未配線の組の数の上限（既定 50）")
    a = ap.parse_args()
    path = Path(a.board)
    if not path.is_file():
        sys.stderr.write(f"基板がない: {path}\n")
        return 2
    unconnected = drc_unconnected(path)
    board = pcbnew.LoadBoard(str(path))
    results = []
    for v in unconnected[:a.max]:
        items = v.get("items", [])
        if len(items) < 2:
            continue
        ends = [item_by_uuid(board, i.get("uuid", "")) for i in items[:2]]
        pts = [pcbnew.VECTOR2I(iu(i["pos"]["x"]), iu(i["pos"]["y"])) for i in items[:2]]
        net = ends[0].GetNetname() if ends[0] is not None else ""
        r = diagnose(board, pts[0], pts[1], net, path.with_suffix(".kicad_pro"))
        r["ends"] = [item_name(e) if e is not None else i.get("description", "") for e, i in zip(ends, items)]
        results.append(r)
    if a.json:
        json.dump({"board": str(path), "unconnected": len(unconnected), "diagnosed": results}, sys.stdout,
                  ensure_ascii=False, indent=1)
        print()
    else:
        print(f"未配線: {len(unconnected)} 組" + (f"（先頭 {a.max} 組を調べた）" if len(unconnected) > a.max else ""))
        for r in results:
            print(f"- {r['ends'][0]} ― {r['ends'][1]}  [{r['net']}]  {r['length_mm']}mm"
                  f"（幅 {r['track_width_mm']} / クリアランス {r['clearance_mm']}）")
            print(f"    {r['summary']}")
            for layer, f in r["layers"].items():
                if f:
                    detail = "; ".join(f"{KIND_JA[k]}: {', '.join(v[:4])}{' ほか' if len(v) > 4 else ''}"
                                       for k, v in f.items())
                    print(f"    {layer}: {detail}")
    return 0 if not unconnected else 1


if __name__ == "__main__":
    sys.exit(main())
