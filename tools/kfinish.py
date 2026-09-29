#!/usr/bin/env python3
"""kicad-local finish: 基板の仕上げ（画面なし。pcbnew を使う。kicad-python で動かす）

使い方（どれも入力は変えず、出力に保存する。--json で機械が読む形）:
  kicad-local finish outline <入力> <出力> [--margin 3] [--round 1] [--replace]
      部品（クーテッド。無ければ外形）を囲む長方形の外形を作る。--replace で今の外形を消してから
  kicad-local finish zone <入力> <出力> --net GND [--layers F.Cu,B.Cu] [--clearance 0.3] [--priority 0]
      外形いっぱいに、そのネットのベタを作って塗る
  kicad-local finish stitch <入力> <出力> --net GND [--pitch 5] [--drill 穴] [--size 径]（既定は基板の今のビアの設定）
      そのネットのベタが両面にある所に、ほかの物に当たらないようスティッチングビアを格子状に置く
  kicad-local finish islands <入力> <出力>
      どこにもつながらないベタの島を取り除く（ゾーンの設定を「島は常に消す」にして塗り直す）
  kicad-local finish widen <入力> <出力> --net 名前 --current 2 [--rise 10] [--copper 35]
      流す電流（A）から IPC-2221 の式で必要な幅を出し、そのネットの細い配線を太くする
      （--rise 許す温度上昇 ℃、--copper 銅の厚さ µm。内層は外層の半分の電流で計算）
  kicad-local finish fill <入力> <出力>
      ベタを塗り直す
"""
import argparse
import fnmatch
import json
import math
import sys
from pathlib import Path

import pcbnew


def iu(mm):
    return pcbnew.FromMM(mm)


def mm(v):
    return round(pcbnew.ToMM(v), 3)


def edge_bbox(board):
    bb = board.GetBoardEdgesBoundingBox()
    return bb if bb.GetWidth() > 0 else None


def fill(board):
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())


def cmd_outline(board, a):
    bb = None
    for fp in board.GetFootprints():
        cy = fp.GetCourtyard(pcbnew.F_CrtYd)
        b = cy.BBox() if cy.OutlineCount() else None
        if b is None:
            cy = fp.GetCourtyard(pcbnew.B_CrtYd)
            b = cy.BBox() if cy.OutlineCount() else fp.GetBoundingBox(False)
        box = pcbnew.BOX2I(b.GetPosition(), b.GetSize())
        if bb is None:
            bb = box
        else:
            bb.Merge(box)
    if bb is None:
        return {"ok": False, "error": "部品が無い"}
    removed = 0
    if a.replace:
        for d in list(board.GetDrawings()):
            if d.GetLayer() == pcbnew.Edge_Cuts:
                board.Remove(d)
                removed += 1
    m, r = iu(a.margin), iu(a.round)
    x0, y0, x1, y1 = bb.GetLeft() - m, bb.GetTop() - m, bb.GetRight() + m, bb.GetBottom() + m
    shapes = []
    if r > 0:
        # 4 辺と 4 隅の円弧
        for (sx, sy, ex, ey) in ((x0 + r, y0, x1 - r, y0), (x1, y0 + r, x1, y1 - r),
                                 (x1 - r, y1, x0 + r, y1), (x0, y1 - r, x0, y0 + r)):
            s = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_SEGMENT)
            s.SetStart(pcbnew.VECTOR2I(sx, sy))
            s.SetEnd(pcbnew.VECTOR2I(ex, ey))
            shapes.append(s)
        for (cx, cy, sx, sy) in ((x0 + r, y0 + r, x0, y0 + r), (x1 - r, y0 + r, x1 - r, y0),
                                 (x1 - r, y1 - r, x1, y1 - r), (x0 + r, y1 - r, x0 + r, y1)):
            s = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_ARC)
            s.SetCenter(pcbnew.VECTOR2I(cx, cy))
            s.SetStart(pcbnew.VECTOR2I(sx, sy))
            s.SetArcAngleAndEnd(pcbnew.EDA_ANGLE(90, pcbnew.DEGREES_T), True)
            shapes.append(s)
    else:
        s = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_RECTANGLE)
        s.SetStart(pcbnew.VECTOR2I(x0, y0))
        s.SetEnd(pcbnew.VECTOR2I(x1, y1))
        shapes.append(s)
    for s in shapes:
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(iu(0.1))
        board.Add(s)
    return {"ok": True, "outline_mm": [mm(x0), mm(y0), mm(x1), mm(y1)],
            "size_mm": [mm(x1 - x0), mm(y1 - y0)], "removed_edges": removed}


def layer_ids(board, names):
    out = []
    for n in names.split(","):
        lid = board.GetLayerID(n)
        if lid < 0:
            raise SystemExit(f"層が無い: {n}")
        out.append(lid)
    return out


def cmd_zone(board, a):
    bb = edge_bbox(board)
    if bb is None:
        return {"ok": False, "error": "外形が無い（先に finish outline）"}
    net = board.FindNet(a.net)
    if net is None:
        return {"ok": False, "error": f"ネットが無い: {a.net}"}
    made = []
    for lid in layer_ids(board, a.layers):
        z = pcbnew.ZONE(board)
        z.SetLayer(lid)
        z.SetNetCode(net.GetNetCode())
        z.SetLocalClearance(iu(a.clearance))
        z.SetAssignedPriority(a.priority)
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
        ol = z.Outline()
        ol.NewOutline()
        for x, y in ((bb.GetLeft(), bb.GetTop()), (bb.GetRight(), bb.GetTop()),
                     (bb.GetRight(), bb.GetBottom()), (bb.GetLeft(), bb.GetBottom())):
            ol.Append(x, y)
        board.Add(z)
        made.append(board.GetLayerName(lid))
    fill(board)
    return {"ok": True, "net": a.net, "layers": made}


def netclass_clearance_mm(board_path, net):
    """プロジェクトの設定から、そのネットのネットクラスのクリアランス（mm）。無ければ None"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kdrc
    pro = Path(board_path).with_suffix(".kicad_pro")
    try:
        classes = {c.get("name"): c for c in json.loads(pro.read_text(encoding="utf-8"))
                   .get("net_settings", {}).get("classes", [])}
    except (OSError, ValueError):
        return None
    nc = kdrc.load_netclasses(pro)
    return (classes.get(kdrc.class_of(net, nc[1], nc[2])) or classes.get("Default") or {}).get("clearance")


def cmd_stitch(board, a):
    net = board.FindNet(a.net)
    if net is None:
        return {"ok": False, "error": f"ネットが無い: {a.net}"}
    code = net.GetNetCode()
    cu = list(board.GetEnabledLayers().CuStack())
    front, back = cu[0], cu[-1]
    fills = []
    for lid in (front, back):
        polys = pcbnew.SHAPE_POLY_SET()
        for z in board.Zones():
            if not z.GetIsRuleArea() and z.GetNetCode() == code and z.IsOnLayer(lid):
                polys.Append(z.GetFilledPolysList(lid))
        fills.append(polys)
    if not fills[0].OutlineCount() or not fills[1].OutlineCount():
        return {"ok": False, "error": f"{a.net} のベタが両面に無い（先に finish zone）"}
    bds = board.GetDesignSettings()
    # ビアの大きさ: 指定が無ければ今のビアの設定。基板の最小値は下回らない
    size = max(iu(a.size) if a.size else bds.GetCurrentViaSize(), bds.m_ViasMinSize)
    drill = max(iu(a.drill) if a.drill else bds.GetCurrentViaDrill(), bds.m_MinThroughDrill)
    r, hr = size // 2, drill // 2
    nc_clear = netclass_clearance_mm(a.input, a.net)
    base_clear = max(bds.m_MinClearance, iu(nc_clear) if nc_clear else 0) + iu(0.02)
    hole_min = bds.m_HoleToHoleMin + iu(0.02)
    bb = edge_bbox(board) or board.ComputeBoundingBox(False)
    pitch = iu(a.pitch)
    holes = [(t.GetPosition(), t.GetDrillValue() // 2) for t in board.GetTracks() if isinstance(t, pcbnew.PCB_VIA)]
    pads = [p for fp in board.GetFootprints() for p in fp.Pads()]
    holes += [(p.GetPosition(), max(p.GetDrillSize().x, p.GetDrillSize().y) // 2) for p in pads if p.HasHole()]
    obstacles = []   # (形を返す関数, クリアランス)
    for t in board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA):
            continue   # ビアは穴どうしの間隔で見る（下）。ほかのネットのビアの銅も見る
        if t.GetNetCode() != code:
            obstacles.append((t.GetEffectiveShape(t.GetLayer()), max(base_clear, t.GetOwnClearance(t.GetLayer()))))
    for t in board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA) and t.GetNetCode() != code:
            obstacles.append((t.GetEffectiveShape(front), max(base_clear, t.GetOwnClearance(front))))
    for pd in pads:
        for lay in (front, back):
            if pd.IsOnLayer(lay):
                # 同じネットのパッドの上にも置かない（クリアランスは 0 で銅が重ならなければよい）
                c = max(base_clear, pd.GetOwnClearance(lay)) if pd.GetNetCode() != code else 0
                obstacles.append((pd.GetEffectiveShape(lay), c))
    for d in board.GetDrawings():   # 銅の層の文字や図形
        if any(d.IsOnLayer(l) for l in cu):
            obstacles.append((d.GetEffectiveShape(), base_clear))
    placed = 0
    x = bb.GetLeft() + pitch // 2
    while x < bb.GetRight():
        y = bb.GetTop() + pitch // 2
        while y < bb.GetBottom():
            p = pcbnew.VECTOR2I(int(x), int(y))
            ok = all(f.Contains(p, -1, r + base_clear) for f in fills)   # ビアの縁から余裕を取って両面のベタの中
            if ok:
                ok = all((p - hp).EuclideanNorm() >= hr + hrad + hole_min for hp, hrad in holes)
            if ok:
                c = pcbnew.SHAPE_CIRCLE(p, r)
                ok = not any(sh.Collide(c, cl) for sh, cl in obstacles)
            if ok:
                v = pcbnew.PCB_VIA(board)
                v.SetPosition(p)
                v.SetDrill(drill)
                v.SetWidth(size)
                v.SetNetCode(code)
                board.Add(v)
                holes.append((p, hr))
                placed += 1
            y += pitch
        x += pitch
    fill(board)
    return {"ok": True, "net": a.net, "placed": placed, "pitch_mm": a.pitch, "via_mm": [mm(size), mm(drill)],
            "clearance_mm": mm(base_clear)}


def cmd_islands(board, a):
    n = 0
    for z in board.Zones():
        if not z.GetIsRuleArea():
            z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
            n += 1
    fill(board)
    return {"ok": True, "zones": n}


def ipc2221_width_mm(current, rise, copper_um, internal):
    """IPC-2221: I = k·ΔT^0.44·A^0.725（A は断面積 mil²、k は外層 0.048・内層 0.024）"""
    k = 0.024 if internal else 0.048
    area = (current / (k * rise ** 0.44)) ** (1 / 0.725)       # mil²
    thick_mil = copper_um / 25.4
    return area / thick_mil * 0.0254                            # mm


def cmd_widen(board, a):
    cu = list(board.GetEnabledLayers().CuStack())
    outer = {cu[0], cu[-1]}
    changed, need = 0, {}
    for t in board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA) or not fnmatch.fnmatchcase(t.GetNetname(), a.net):
            continue
        w = ipc2221_width_mm(a.current, a.rise, a.copper, t.GetLayer() not in outer)
        need[board.GetLayerName(t.GetLayer())] = round(w, 3)
        if t.GetWidth() < iu(w):
            t.SetWidth(iu(math.ceil(w * 100) / 100))
            changed += 1
    fill(board)
    return {"ok": True, "net": a.net, "current_a": a.current, "rise_c": a.rise, "copper_um": a.copper,
            "required_width_mm": need, "widened": changed,
            "note": "太くした配線がほかの物に近づいていないか、DRC で確かめること"}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n", 2)[2])
    ap.add_argument("what", choices=["outline", "zone", "stitch", "islands", "widen", "fill"])
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--margin", type=float, default=3.0)
    ap.add_argument("--round", type=float, default=1.0)
    ap.add_argument("--replace", action="store_true")
    ap.add_argument("--net")
    ap.add_argument("--layers", default="F.Cu,B.Cu")
    ap.add_argument("--clearance", type=float, default=0.3)
    ap.add_argument("--priority", type=int, default=0)
    ap.add_argument("--pitch", type=float, default=5.0)
    ap.add_argument("--drill", type=float)
    ap.add_argument("--size", type=float)
    ap.add_argument("--current", type=float)
    ap.add_argument("--rise", type=float, default=10.0)
    ap.add_argument("--copper", type=float, default=35.0)
    a = ap.parse_args()
    src = Path(a.input)
    if not src.is_file():
        sys.stderr.write(f"基板がない: {src}\n")
        return 2
    if a.what in ("zone", "stitch", "widen") and not a.net:
        sys.stderr.write(f"finish {a.what} には --net が要る\n")
        return 2
    if a.what == "widen" and not a.current:
        sys.stderr.write("finish widen には --current（A）が要る\n")
        return 2
    board = pcbnew.LoadBoard(str(src))
    res = {"outline": cmd_outline, "zone": cmd_zone, "stitch": cmd_stitch, "islands": cmd_islands,
           "widen": cmd_widen, "fill": lambda b, _: (fill(b), {"ok": True})[1]}[a.what](board, a)
    if res.get("ok"):
        pcbnew.SaveBoard(a.output, board)
        pro = src.with_suffix(".kicad_pro")
        dst_pro = Path(a.output).with_suffix(".kicad_pro")
        if pro.exists() and not dst_pro.exists():
            dst_pro.write_bytes(pro.read_bytes())
        res["output"] = a.output
    if a.json:
        json.dump(res, sys.stdout, ensure_ascii=False, indent=1)
        print()
    else:
        if res.get("ok"):
            print(" ".join(f"{k}={v}" for k, v in res.items() if k not in ("ok", "output")) + f" → {a.output}")
        else:
            print("できなかった: " + res.get("error", ""))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
