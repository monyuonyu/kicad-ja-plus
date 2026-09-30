#!/usr/bin/env python3
"""kicad-local view: 基板の 2D の図を、注釈つきで画像にする（画面なし。pcbnew を使う。kicad-python で動かす）

使い方:
  kicad-local view <基板.kicad_pcb> [-o 出力.png] [--net 名前 ...] [--drc] [--ref] [--layers F.Cu,B.Cu]
                   [--region x0,y0,x1,y1] [--size 1600] [--json]

  --net      そのネットを明るく太く描き、ほかを薄くする（何度でも。* ? のワイルドカード可）
  --drc      DRC の違反に番号つきの赤い丸を付け、未配線の組をオレンジの線で結ぶ。番号と内容の一覧も出す
  --ref      部品番号を描く
  --layers   描く銅の層（既定はすべて）
  --region   この範囲（mm）だけを拡大して描く
  --json     画像のほかに、描いたもの（ネット・違反の番号と位置）を JSON で出す

AI や人が「見て」確かめるためのもの。色: 表の銅=赤、裏=青、内層=緑系、パッド=金、ビア=灰、ベタは薄く。
"""
import argparse
import fnmatch
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pcbnew
from PIL import Image, ImageDraw, ImageFont

LAYER_COLORS = {"F.Cu": (200, 52, 52), "B.Cu": (52, 92, 200)}
INNER = [(40, 150, 90), (150, 120, 40), (120, 60, 160), (40, 140, 150)]
PAD = (210, 170, 40)
VIA = (130, 130, 130)
EDGE = (40, 40, 40)
HIGHLIGHT = (20, 200, 60)
MARK = (230, 20, 20)
RATS = (255, 140, 0)


def font(size):
    for f in ("/usr/share/fonts/opentype/ipaexfont-gothic/ipaexg.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default()


def mm(v):
    return pcbnew.ToMM(v)


def run_drc(path):
    out = Path(tempfile.mkdtemp()) / "drc.json"
    subprocess.run(["kicad-cli-local", "pcb", "drc", "--format", "json", "-o", str(out), str(path)],
                   capture_output=True, text=True)
    return json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}


class Canvas:
    def __init__(self, x0, y0, x1, y1, size):
        self.x0, self.y0 = x0, y0
        self.scale = size / max(x1 - x0, y1 - y0)
        self.w, self.h = int((x1 - x0) * self.scale) + 1, int((y1 - y0) * self.scale) + 1
        self.img = Image.new("RGB", (self.w, self.h), (250, 250, 247))
        self.d = ImageDraw.Draw(self.img, "RGBA")

    def p(self, x, y):
        """mm → 画素"""
        return ((x - self.x0) * self.scale, (y - self.y0) * self.scale)

    def px(self, mm_len):
        return max(1, int(mm_len * self.scale))


def fade(c, amount):
    return tuple(int(v + (255 - v) * amount) for v in c)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n", 2)[2])
    ap.add_argument("board")
    ap.add_argument("-o", "--out")
    ap.add_argument("--net", action="append", default=[])
    ap.add_argument("--drc", action="store_true")
    ap.add_argument("--ref", action="store_true")
    ap.add_argument("--layers")
    ap.add_argument("--region")
    ap.add_argument("--size", type=int, default=1600)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    path = Path(a.board)
    if not path.is_file():
        sys.stderr.write(f"基板がない: {path}\n")
        return 2
    out = Path(a.out) if a.out else path.with_name(path.stem + "_view.png")
    board = pcbnew.LoadBoard(str(path))

    cu = [l for l in board.GetEnabledLayers().CuStack()]
    names = {l: board.GetLayerName(l) for l in cu}
    std = {l: pcbnew.BOARD.GetStandardLayerName(l) for l in cu}
    if a.layers:
        want = set(a.layers.split(","))
        cu = [l for l in cu if names[l] in want or std[l] in want]
    color = {}
    for i, l in enumerate(cu):
        color[l] = LAYER_COLORS.get(std[l], INNER[i % len(INNER)])

    def picked(net):
        return any(fnmatch.fnmatchcase(net, pat) for pat in a.net)

    # 描く範囲
    if a.region:
        x0, y0, x1, y1 = (float(v) for v in a.region.split(","))
    else:
        bb = board.GetBoardEdgesBoundingBox()
        if bb.GetWidth() == 0:
            bb = board.ComputeBoundingBox(False)
        m = 2.0
        x0, y0, x1, y1 = mm(bb.GetLeft()) - m, mm(bb.GetTop()) - m, mm(bb.GetRight()) + m, mm(bb.GetBottom()) + m
    c = Canvas(x0, y0, x1, y1, a.size)
    dim = 0.8 if a.net else 0.0

    # ベタ（薄く）
    for z in board.Zones():
        if z.GetIsRuleArea():
            continue
        for l in cu:
            if not z.IsOnLayer(l):
                continue
            polys = z.GetFilledPolysList(l)
            for i in range(polys.OutlineCount()):
                o = polys.Outline(i)
                pts = [c.p(mm(o.CPoint(k).x), mm(o.CPoint(k).y)) for k in range(o.PointCount())]
                if len(pts) > 2:
                    base = HIGHLIGHT if a.net and picked(z.GetNetname()) else color[l]
                    c.d.polygon(pts, fill=fade(base, 0.82 if not (a.net and picked(z.GetNetname())) else 0.6))
    # 配線禁止の区域（斜線の代わりに枠）
    for z in board.Zones():
        if z.GetIsRuleArea():
            o = z.Outline().Outline(0)
            pts = [c.p(mm(o.CPoint(k).x), mm(o.CPoint(k).y)) for k in range(o.PointCount())]
            if len(pts) > 2:
                c.d.polygon(pts, fill=(120, 0, 160, 40), outline=(120, 0, 160))
    # 外形
    # 8.0 の pcbnew は ERROR_LOC を Python に出していないので、TransformShapeToPolygon が使えない。
    # その時は、外形は基板の外形の多角形、パッドは GetEffectivePolygon で描く
    old_api = not hasattr(pcbnew, "ERROR_INSIDE")
    edges = []
    if old_api:
        poly = pcbnew.SHAPE_POLY_SET()
        board.GetBoardPolygonOutlines(poly)
        edges.append(poly)
    else:
        for d in board.GetDrawings():
            if d.GetLayer() == pcbnew.Edge_Cuts and isinstance(d, pcbnew.PCB_SHAPE):
                poly = pcbnew.SHAPE_POLY_SET()
                d.TransformShapeToPolygon(poly, pcbnew.Edge_Cuts, 0, pcbnew.FromMM(0.01), pcbnew.ERROR_INSIDE)
                edges.append(poly)
    for poly in edges:
        for i in range(poly.OutlineCount()):
            o = poly.Outline(i)
            pts = [c.p(mm(o.CPoint(k).x), mm(o.CPoint(k).y)) for k in range(o.PointCount())]
            if len(pts) > 1:
                c.d.line(pts + [pts[0]], fill=EDGE, width=2)
    # 配線（裏から）
    for l in reversed(cu):
        for t in board.GetTracks():
            if isinstance(t, pcbnew.PCB_VIA) or t.GetLayer() != l:
                continue
            hi = a.net and picked(t.GetNetname())
            col = HIGHLIGHT if hi else fade(color[l], dim)
            w = c.px(mm(t.GetWidth())) + (2 if hi else 0)
            c.d.line([c.p(mm(t.GetStart().x), mm(t.GetStart().y)), c.p(mm(t.GetEnd().x), mm(t.GetEnd().y))],
                     fill=col, width=w)
    # パッド
    for fp in board.GetFootprints():
        for p in fp.Pads():
            layer = next((l for l in cu if p.IsOnLayer(l)), None)
            if layer is None:
                continue
            if old_api:
                poly = p.GetEffectivePolygon()
            else:
                poly = pcbnew.SHAPE_POLY_SET()
                p.TransformShapeToPolygon(poly, layer, 0, pcbnew.FromMM(0.01), pcbnew.ERROR_INSIDE)
            hi = a.net and picked(p.GetNetname())
            for i in range(poly.OutlineCount()):
                o = poly.Outline(i)
                pts = [c.p(mm(o.CPoint(k).x), mm(o.CPoint(k).y)) for k in range(o.PointCount())]
                if len(pts) > 2:
                    c.d.polygon(pts, fill=HIGHLIGHT if hi else fade(PAD, dim))
    # ビア
    for t in board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA):
            x, y = c.p(mm(t.GetPosition().x), mm(t.GetPosition().y))
            r = c.px(mm(t.GetWidth() if old_api else t.GetWidth(pcbnew.F_Cu)) / 2)  # 8.0 は層を取らない
            hi = a.net and picked(t.GetNetname())
            c.d.ellipse([x - r, y - r, x + r, y + r], fill=HIGHLIGHT if hi else fade(VIA, dim))
    # 部品番号
    if a.ref:
        f = font(max(10, c.px(1.0)))
        for fp in board.GetFootprints():
            x, y = c.p(mm(fp.GetPosition().x), mm(fp.GetPosition().y))
            c.d.text((x, y), fp.GetReference(), fill=(0, 0, 0), font=f, anchor="mm", stroke_width=2, stroke_fill=(255, 255, 255))
    # DRC
    marks = []
    if a.drc:
        d = run_drc(path)
        f = font(max(12, c.px(1.2)))
        for v in d.get("unconnected_items", []):
            its = v.get("items", [])
            if len(its) >= 2:
                p0 = c.p(its[0]["pos"]["x"], its[0]["pos"]["y"])
                p1 = c.p(its[1]["pos"]["x"], its[1]["pos"]["y"])
                c.d.line([p0, p1], fill=RATS, width=3)
                marks.append({"no": len(marks) + 1, "type": "unconnected_items",
                              "pos": its[0]["pos"], "description": " ⇔ ".join(i.get("description", "") for i in its[:2])})
        noise = ("lib_footprint_mismatch", "lib_footprint_issues", "silk_edge_clearance", "silk_overlap",
                 "silk_over_copper")
        for v in d.get("violations", []):
            if v["type"] in noise:
                continue
            pos = v.get("pos") or (v.get("items") or [{}])[0].get("pos")
            if not pos:
                continue
            marks.append({"no": len(marks) + 1, "type": v["type"], "pos": pos, "layer": v.get("layer", ""),
                          "description": v.get("description", "")})
        for mk in marks:
            if mk["type"] == "unconnected_items":
                continue
            x, y = c.p(mk["pos"]["x"], mk["pos"]["y"])
            r = max(8, c.px(1.0))
            c.d.ellipse([x - r, y - r, x + r, y + r], outline=MARK, width=3)
            c.d.text((x + r + 2, y - r), str(mk["no"]), fill=MARK, font=f, stroke_width=2, stroke_fill=(255, 255, 255))

    c.img.save(out)
    info = {"image": str(out), "region_mm": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
            "pixels_per_mm": round(c.scale, 3), "layers": [names[l] for l in cu], "nets": a.net, "marks": marks}
    if a.json:
        json.dump(info, sys.stdout, ensure_ascii=False, indent=1)
        print()
    else:
        print(f"→ {out}（{c.w}×{c.h} 画素、1mm = {c.scale:.1f} 画素）")
        for mk in marks:
            print(f"  {mk['no']:3d}. {mk['type']}  ({mk['pos']['x']:.2f}, {mk['pos']['y']:.2f}) {mk.get('layer', '')} "
                  f"{mk['description'][:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
