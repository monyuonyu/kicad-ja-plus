# 回帰試験の材料: Net-(C5-Pad1) の配線を消し、R10.2 と C5.1 の間を両面の配線禁止の区域で塞いだ基板
import sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
for t in list(b.GetTracks()):
    if t.GetNetname() == "Net-(C5-Pad1)":
        b.Remove(t)
fp = {f.GetReference(): f for f in b.GetFootprints()}
pa = [p for p in fp["R10"].Pads() if p.GetNumber() == "2"][0].GetPosition()
pb = [p for p in fp["C5"].Pads() if p.GetNumber() == "1"][0].GetPosition()
mid = pcbnew.VECTOR2I((pa.x + pb.x) // 2, (pa.y + pb.y) // 2)
z = pcbnew.ZONE(b)
z.SetIsRuleArea(True)
z.SetDoNotAllowTracks(True)
z.SetDoNotAllowVias(True)
z.SetZoneName("keepout-test")
ls = pcbnew.LSET()
ls.AddLayer(pcbnew.F_Cu)
ls.AddLayer(pcbnew.B_Cu)
z.SetLayerSet(ls)
d = pcbnew.FromMM(1.5)
ol = z.Outline()
ol.NewOutline()
for x, y in ((-d, -d), (d, -d), (d, d), (-d, d)):
    ol.Append(mid.x + x, mid.y + y)
b.Add(z)
b.Save(sys.argv[2])
