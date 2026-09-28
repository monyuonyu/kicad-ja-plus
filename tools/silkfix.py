#!/usr/bin/env python3
"""
silkfix: 部品番号の文字を、ほかのシルク・端子・基板の縁に重ならない所へ移す(kicad-route の silk より広く探す)
  kicad-python silkfix.py <入力.kicad_pcb> <出力.kicad_pcb> 部品番号 ... [--r 10]
  部品の中心から近い順に、半径 r mm までの格子(0.25mm)と 横/縦 を試す。
"""
import sys, math
import pcbnew

MM, FM = pcbnew.ToMM, pcbnew.FromMM


def boxes(b, skip_text):
    out = []
    for f in b.GetFootprints():
        items = list(f.GraphicalItems()) + [f.Reference(), f.Value()]
        for it in items:
            if it.GetLayer() not in (pcbnew.F_SilkS, pcbnew.B_SilkS):
                continue
            if it is skip_text or (it.Type() == pcbnew.PCB_FIELD_T and not it.IsVisible()):
                continue
            if skip_text is not None and it.Type() == pcbnew.PCB_FIELD_T and it.GetParentFootprint() and \
                    it.GetParentFootprint().GetReference() == skip_text.GetParentFootprint().GetReference() and \
                    it.GetName() == skip_text.GetName():
                continue
            out.append((it.GetLayer(), it.GetBoundingBox()))
        for p in f.Pads():
            out.append(("pad", p.GetBoundingBox()))
    for d in b.GetDrawings():
        if d.GetLayer() in (pcbnew.F_SilkS, pcbnew.B_SilkS):
            out.append((d.GetLayer(), d.GetBoundingBox()))
    return out


def main():
    a = sys.argv[1:]
    r = 10.0
    if "--r" in a:
        i = a.index("--r"); r = float(a[i + 1]); del a[i:i + 2]
    src, dst, refs = a[0], a[1], a[2:]
    b = pcbnew.LoadBoard(src)
    edge = b.GetBoardEdgesBoundingBox()
    edge.Inflate(-FM(0.5))
    for ref in refs:
        f = b.FindFootprintByReference(ref)
        t = f.Reference()
        obs = [bx for L, bx in boxes(b, t) if L == "pad" or L == t.GetLayer()]
        c = f.GetBoundingBox(False, False).GetCenter() if hasattr(f, "GetBoundingBox") else f.GetPosition()
        orig = (t.GetTextPos(), t.GetTextAngle())
        best = None
        steps = int(r / 0.25)
        cands = sorted(((dx * 0.25, dy * 0.25) for dx in range(-steps, steps + 1) for dy in range(-steps, steps + 1)
                        if (dx * dx + dy * dy) * 0.0625 <= r * r), key=lambda v: v[0] ** 2 + v[1] ** 2)
        for dx, dy in cands:
            for ang in (0, 90):
                t.SetTextAngle(pcbnew.EDA_ANGLE(ang, pcbnew.DEGREES_T))
                t.SetTextPos(pcbnew.VECTOR2I(c.x + FM(dx), c.y + FM(dy)))
                bb = t.GetBoundingBox()
                bb.Inflate(FM(0.15))
                if not edge.Contains(bb):
                    continue
                if any(bb.Intersects(o) for o in obs):
                    continue
                best = (dx, dy, ang)
                break
            if best:
                break
        if best:
            p = t.GetTextPos()
            print(f"  {ref}: 文字を ({MM(p.x):.2f}, {MM(p.y):.2f})・{best[2]}° へ")
        else:
            t.SetTextPos(orig[0]); t.SetTextAngle(orig[1])
            print(f"  {ref}: 半径 {r}mm 以内に空きが無い(そのまま)")
    pcbnew.SaveBoard(dst, b)
    return 0


if __name__ == "__main__":
    sys.exit(main())
