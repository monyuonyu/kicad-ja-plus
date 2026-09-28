#!/usr/bin/env python3
"""
pcbsync: 回路図の変更を基板に反映する(画面の「回路図から基板を更新」の画面なし版。ローカル道具、2026-09-25)

  kicad-local pcbsync <回路図.kicad_sch> <基板.kicad_pcb> <出力.kicad_pcb> [--at x,y] [--delete]

  - 回路図にあって基板に無い部品を、フットプリントのライブラリから読み込んで --at の位置(既定: 基板の右外)に並べる
  - 端子のネットを回路図どおりにする(ネット名も)
  - つながりが変わったネットの配線は消す(つながりの違う配線を残すと短絡になるため) → 引き直しが要るネットとして表示
  - 値・フットプリント名の違いは表示(値は直す。フットプリントの差し替えはしない)
  - 基板にだけある部品は表示(--delete で消す)
  あとは kicad-local route で置いて配線する(findspace / place / autoroute)。
"""
import argparse, re, subprocess, sys, tempfile
from pathlib import Path
import pcbnew

HERE = Path(__file__).resolve().parent
FPLIB = Path("/usr/share/kicad/footprints")


def netlist(sch):
    out = Path(tempfile.mkdtemp()) / "n.net"
    subprocess.run(["kicad-cli", "sch", "export", "netlist", "--format", "kicadsexpr", "-o", str(out), str(sch)],
                   capture_output=True)
    t = out.read_text(encoding="utf-8")
    comps = {}
    for m in re.finditer(r'\(comp \(ref "([^"]+)"\)(.*?)(?=\n    \(comp |\n  \(libparts|\n  \)\n)', t, re.S):
        blk = m.group(2)
        v = re.search(r'\(value "([^"]*)"\)', blk)
        fp = re.search(r'\(footprint "([^"]*)"\)', blk)
        comps[m.group(1)] = {"value": v.group(1) if v else "", "fp": fp.group(1) if fp else "", "pins": {}}
    for m in re.finditer(r'\(net \(code "\d+"\) \(name "([^"]*)"\)(.*?)(?=\n    \(net |\n  \)|\Z)', t, re.S):
        for ref, pin in re.findall(r'\(node \(ref "([^"]+)"\) \(pin "([^"]+)"\)', m.group(2)):
            if ref in comps:
                comps[ref]["pins"][pin] = m.group(1)
    return comps


def main():
    ap = argparse.ArgumentParser(description="回路図の変更を基板に反映する(画面なし)")
    ap.add_argument("sch")
    ap.add_argument("pcb")
    ap.add_argument("out")
    ap.add_argument("--at", help="新しい部品を並べ始める位置 x,y(mm)。既定は基板の右外")
    ap.add_argument("--delete", action="store_true", help="基板にだけある部品を消す")
    ap.add_argument("--swap", action="store_true", help="フットプリントが回路図と違う部品を差し替える(位置・向き・面は保つ。その部品の配線は消す)")
    a = ap.parse_args()

    comps = netlist(a.sch)
    b = pcbnew.LoadBoard(a.pcb)
    fps = {f.GetReference(): f for f in b.GetFootprints()}

    def netinfo(name):
        ni = b.FindNet(name)
        if not ni:
            ni = pcbnew.NETINFO_ITEM(b, name)
            b.Add(ni)
        return ni

    # 変更前のつながり(ネット → 端子の集合)
    before = {}
    for f in b.GetFootprints():
        for p in f.Pads():
            if p.GetNetname():
                before.setdefault(p.GetNetname(), set()).add(f"{f.GetReference()}.{p.GetNumber()}")

    bb = b.GetBoardEdgesBoundingBox()
    x0, y0 = (float(v) for v in a.at.split(",")) if a.at else (pcbnew.ToMM(bb.GetRight()) + 10, pcbnew.ToMM(bb.GetTop()))
    added = []
    for ref, c in sorted(comps.items()):
        if ref in fps or not c["fp"] or ref.startswith("#"):
            continue
        lib, name = c["fp"].split(":", 1)
        f = pcbnew.FootprintLoad(str(FPLIB / f"{lib}.pretty"), name)
        if f is None:
            print(f"  × {ref}: フットプリント {c['fp']} がライブラリに無い")
            continue
        f.SetReference(ref)
        f.SetValue(c["value"])
        f.SetFPIDAsString(c["fp"])
        f.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x0), pcbnew.FromMM(y0 + 8 * len(added))))
        b.Add(f)
        fps[ref] = f
        added.append(ref)
        print(f"  追加 {ref}({c['value']}、{c['fp']})→ ({x0:.1f}, {y0 + 8 * (len(added) - 1):.1f}) に仮置き")

    changed_val = []
    swapped = []
    for ref, c in comps.items():
        f = fps.get(ref)
        if not f:
            continue
        if f.GetValue() != c["value"]:
            changed_val.append(f"{ref}: {f.GetValue()} → {c['value']}")
            f.SetValue(c["value"])
        if c["fp"] and f.GetFPIDAsString() != c["fp"]:
            if not a.swap:
                print(f"  ! {ref}: フットプリントが回路図と違う(基板 {f.GetFPIDAsString()} / 回路図 {c['fp']})。--swap で差し替え")
            else:
                lib, name = c["fp"].split(":", 1)
                nf = pcbnew.FootprintLoad(str(FPLIB / f"{lib}.pretty"), name)
                if nf is None:
                    print(f"  × {ref}: フットプリント {c['fp']} がライブラリに無い")
                else:
                    nf.SetReference(ref)
                    nf.SetValue(f.GetValue())
                    nf.SetFPIDAsString(c["fp"])
                    if f.GetLayer() == pcbnew.B_Cu:
                        nf.Flip(nf.GetPosition(), False)
                    nf.SetPosition(f.GetPosition())
                    nf.SetOrientation(f.GetOrientation())
                    oldnets = {p.GetNumber(): p.GetNetname() for p in f.Pads()}
                    b.Remove(f)
                    b.Add(nf)
                    for p in nf.Pads():
                        if oldnets.get(p.GetNumber()):
                            p.SetNet(netinfo(oldnets[p.GetNumber()]))
                    print(f"  差し替え {ref}: {f.GetFPIDAsString()} → {c['fp']}(位置・向きはそのまま。配線は引き直し)")
                    swapped.append(ref)
                    fps[ref] = nf
                    f = nf
        for p in f.Pads():
            want = c["pins"].get(p.GetNumber())
            if want and p.GetNetname() != want:
                p.SetNet(netinfo(want))
    for v in changed_val:
        print(f"  値 {v}")

    extra = [r for r in fps if r not in comps and not r.startswith(("#", "H", "kibuzzard"))]
    for r in extra:
        print(f"  基板にだけある部品: {r}" + ("(消した)" if a.delete else ""))
        if a.delete:
            b.Remove(fps[r])

    after = {}
    for f in b.GetFootprints():
        for p in f.Pads():
            if p.GetNetname():
                after.setdefault(p.GetNetname(), set()).add(f"{f.GetReference()}.{p.GetNumber()}")
    # 端子の組が変わったネットの配線を消す(新しい部品が加わっただけのネットは、既存の配線を残してよい)
    newrefs = set(added)
    # 差し替えた部品につながる配線は、端子の位置が変わるので消す
    for ref in swapped:
        nets = {p.GetNetname() for p in fps[ref].Pads() if p.GetNetname() and p.GetNetname() not in ("GND", "/GND")}
        for t in list(b.GetTracks()):
            if t.GetNetname() in nets:
                b.Remove(t)
        print(f"  {ref} のネット {', '.join(sorted(nets))} の配線を消した(GND はベタなので残す)")
    redo = []
    for net in set(before) | set(after):
        old = before.get(net, set())
        new = {x for x in after.get(net, set()) if x.split(".")[0] not in newrefs}
        if old != new or (after.get(net, set()) - old):
            redo.append(net)
    removed = 0
    for t in list(b.GetTracks()):
        if t.GetNetname() in redo:
            old = before.get(t.GetNetname(), set())
            new = {x for x in after.get(t.GetNetname(), set()) if x.split(".")[0] not in newrefs}
            if old != new:   # 既存の端子どうしのつながりが変わった → 残すと短絡
                b.Remove(t)
                removed += 1
    pcbnew.SaveBoard(a.out, b)
    print(f"\n引き直しが要るネット({len(redo)}):")
    for net in sorted(redo):
        print(f"  {net}: " + " ".join(sorted(after.get(net, set()))))
    print(f"消した配線 {removed} 本 → {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
