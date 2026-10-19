"""試験用の小さな基板: 3D モデルのある抵抗と、標準の 3D ライブラリにモデルの無い JST VH 横型 2 つ。
   使い方: kicad-python make_vh.py 出力.kicad_pcb"""
import sys, pcbnew
FP = "/usr/share/kicad/footprints"
b = pcbnew.NewBoard(sys.argv[1]) if hasattr(pcbnew, "NewBoard") else pcbnew.BOARD()
def mm(x, y): return pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))
for (x0, y0), (x1, y1) in (((100, 100), (150, 100)), ((150, 100), (150, 140)), ((150, 140), (100, 140)), ((100, 140), (100, 100))):
    s = pcbnew.PCB_SHAPE(b); s.SetShape(pcbnew.SHAPE_T_SEGMENT); s.SetLayer(pcbnew.Edge_Cuts)
    s.SetStart(mm(x0, y0)); s.SetEnd(mm(x1, y1)); s.SetWidth(pcbnew.FromMM(0.1)); b.Add(s)
for ref, lib, name, x, y in (("R1", "Resistor_SMD", "R_0805_2012Metric", 110, 110),
                             ("J1", "Connector_JST", "JST_VH_B2PS-VH_1x02_P3.96mm_Horizontal", 120, 120),
                             ("J2", "Connector_JST", "JST_VH_S4P-VH_1x04_P3.96mm_Horizontal", 125, 132)):
    f = pcbnew.FootprintLoad(f"{FP}/{lib}.pretty", name)
    f.SetReference(ref); f.SetPosition(mm(x, y)); b.Add(f)
    if ref == "J2":   # 高さの欄（--placeholder-boxes が使う）
        try:
            f.SetField("Height", "9.5")
        except Exception:   # 8.0.9: 欄を作って足す
            fl = pcbnew.PCB_FIELD(f, f.GetNextFieldId(), "Height"); fl.SetText("9.5"); fl.SetVisible(False); f.AddField(fl)
pcbnew.SaveBoard(sys.argv[1], b)
