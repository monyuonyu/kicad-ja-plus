"""bom の試験の材料: デモの基板の C4〜C7 に品番(村田 RDE・TDK FG の 2.5mm/5.0mm)を入れた基板と、
interf_u の部品表(Excel の形。共有文字列・書式つき。R3 の値だけ回路図と違い、回路図に無い RX9 がある)
使い方: kicad-python make_bom.py <in.kicad_pcb> <出力フォルダ>"""
import os, sys, zipfile
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
caps = [f for f in b.GetFootprints() if "C_Disc_D5.1mm_W3.2mm_P5.00mm" in f.GetFPIDAsString()]
caps.sort(key=lambda f: f.GetReference())
pns = ["RDER71H104K0K1H03B", "RDER71H104K0P1H03B", "FG28X7R1H104KNT06", "FG18X7R1H104KNT06"]
for f, pn in zip(caps, pns):
    f.SetField("MPN", pn)
    print(f.GetReference(), pn)
b.Save(os.path.join(sys.argv[2], "bom.kicad_pcb"))
ss = ["部品番号", "値", "品番", "数量", "C1,C4-C6", "47uF", "R2", "1K", "R3", "22K", "C2 C3", "47pF", "RX9", "1K"]
# 日本語の Excel の見出しにはふりがな(<rPh>)が付く(「品番」が「品番ヒンバン」と読まれた。8.0.9-ja.5 の報告)
FURIGANA = {"品番": "ヒンバン", "部品番号": "ブヒンバンゴウ"}
def si(t):
    if t in FURIGANA:
        return f'<si><r><t>{t}</t></r><rPh sb="0" eb="{len(t)}"><t>{FURIGANA[t]}</t></rPh><phoneticPr fontId="1"/></si>'
    return f"<si><t>{t}</t></si>"
rows = [("部品番号", "値", "品番"), ("C1,C4-C6", "47uF", None), ("R2", "1K", None), ("R3", "22K", None), ("C2 C3", "47pF", None), ("RX9", "1K", None)]
idx = {t: i for i, t in enumerate(ss)}
sheet = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>']
for r, row in enumerate(rows, 1):
    cells = "".join(f'<c r="{"ABC"[k]}{r}" s="1" t="s"><v>{idx[v]}</v></c>' for k, v in enumerate(row) if v)
    sheet.append(f'<row r="{r}">{cells}</row>')
sheet.append("</sheetData></worksheet>")
files = {
 "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/></Types>',
 "_rels/.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
 "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="BOM" sheetId="1" r:id="rId1"/></sheets></workbook>',
 "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/></Relationships>',
 "xl/sharedStrings.xml": '<?xml version="1.0" encoding="UTF-8"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' + "".join(si(t) for t in ss) + "</sst>",
 "xl/worksheets/sheet1.xml": "".join(sheet),
}
with zipfile.ZipFile(os.path.join(sys.argv[2], "bom.xlsx"), "w") as z:
    for n, d in files.items(): z.writestr(n, d)
