import pcbnew, sys
print("使用モジュール:", pcbnew.__file__)
def state(b): return f"{type(b.Footprints()).__name__}/{type(b.Tracks()).__name__}"
b=pcbnew.LoadBoard("in.kicad_pcb")
t=b.GetTracks()[0]; b.Remove(t); t=None
print("  配線を外して解放:", state(b))
ok=True
try:
    for ref in ("R1","D1","C1"):
        f=[x for x in b.GetFootprints() if x.GetReference()==ref][0]
        b.Remove(f)
        n=len(b.GetFootprints()); p=sum(len(list(x.Pads())) for x in b.GetFootprints())
    print("  部品を3個続けて外す: OK 残り", n, "部品 / パッド", p)
except Exception as e:
    ok=False; print("  部品を3個続けて外す: NG", type(e).__name__, e)
# 外したものを付け直す(thisown=0 側の辻褄)
g=pcbnew.PCB_TRACK(b); b.Add(g); g=None
f=pcbnew.FOOTPRINT(b); b.Add(f); b.Remove(f); b.Add(f); f=None
print("  付け外しを繰り返した後:", state(b), "部品", len(b.GetFootprints()))
b.Save("now/swigfix_out.kicad_pcb"); print("  保存OK")
