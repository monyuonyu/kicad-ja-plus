#!/usr/bin/env python3
"""
kbom: 部品表(BOM)と回路図・基板の照合(kicad-local bom。2026-10-29)

  kicad-local bom check <回路図|基板> [--bom 部品表.xlsx|.csv] [--json]
      品番の末尾が表すリードの間隔(村田 RDE の P1/K1、TDK FG の FG1x/FG2x)とフットプリントの間隔を比べる。
      --bom を付けると、部品表の品番も使い、部品表と回路図の食い違い(部品番号の過不足・値の違い)も出す。
      食い違いがあれば終了コード 1
  kicad-local bom update <回路図> <部品表.xlsx> [-o 出力.xlsx] [--dry-run]
      部品表の値の列を回路図の値に合わせる(変わるセルだけ書き換え、ほかの中身・書式はそのまま)

品番の規則は tools/bomrules.toml(メーカーの資料で確かめたものだけ。足すときは出典も書く)。
部品表の列は見出しから探す: 部品番号(Reference・Ref・Designator・部品番号・記号)、品番(MPN・品番・型番・
Part Number)、値(Value・値・定数)。1 行に「C1,C2,C5-C7」のように何個も書いてあってよい。
"""
import argparse, csv, json, math, re, sys, tomllib, zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import knetlist  # noqa: E402

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
HEAD_REF = ("reference", "references", "ref", "refs", "designator", "designators", "部品番号", "記号", "回路記号", "リファレンス")
HEAD_PN = ("mpn", "品番", "型番", "メーカー型番", "メーカー品番", "part number", "partnumber", "manufacturer part number", "p/n")
HEAD_VAL = ("value", "値", "定数", "容量・抵抗値")
PN_FIELDS = ("MPN", "品番", "型番", "Manufacturer_Part_Number", "Manufacturer Part Number", "PartNumber", "Part Number", "P/N")


# ---------------------------------------------------------------- 部品表を読む
def _col(ref):
    """'C12' → (列番号 0 から, 行番号 1 から)"""
    m = re.match(r"([A-Z]+)(\d+)", ref)
    n = 0
    for ch in m.group(1):
        n = n * 26 + ord(ch) - 64
    return n - 1, int(m.group(2))


def _first_sheet(z):
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rid = wb.find("m:sheets/m:sheet", NS).get(REL)
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    target = next(r.get("Target") for r in rels if r.get("Id") == rid)
    return target.lstrip("/") if target.startswith("/") else "xl/" + target


def _shared(z):
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.iter("{%s}t" % NS["m"])) for si in root.findall("m:si", NS)]


def read_rows(path):
    """部品表 → [(行番号, {列番号: 文字列})]"""
    path = Path(path)
    if path.suffix.lower() in (".csv", ".tsv", ".txt"):
        raw = path.read_bytes()
        text = None
        for enc in ("utf-8-sig", "cp932"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                pass
        rows = list(csv.reader(text.splitlines(), delimiter="\t" if path.suffix.lower() == ".tsv" else ","))
        return [(i + 1, dict(enumerate(r))) for i, r in enumerate(rows)]
    with zipfile.ZipFile(path) as z:
        ss = _shared(z)
        sheet = ET.fromstring(z.read(_first_sheet(z)))
    rows = []
    for row in sheet.iter("{%s}row" % NS["m"]):
        cells = {}
        for c in row.findall("m:c", NS):
            col, _ = _col(c.get("r"))
            t = c.get("t")
            if t == "s":
                v = c.findtext("m:v", "", NS)
                cells[col] = ss[int(v)] if v else ""
            elif t == "inlineStr":
                cells[col] = "".join(x.text or "" for x in c.iter("{%s}t" % NS["m"]))
            else:
                cells[col] = c.findtext("m:v", "", NS) or ""
        rows.append((int(row.get("r")), cells))
    return rows


def expand_refs(text):
    """'C1,C2, C5-C7' 'R1 R2' 'C1、C2' → [C1, C2, C5, C6, C7]"""
    out = []
    for part in re.split(r"[,\s、，;]+", text.strip()):
        m = re.fullmatch(r"([A-Za-z_#]+)(\d+)\s*[-~〜]\s*(?:\1)?(\d+)", part)
        if m:
            out += [f"{m.group(1)}{k}" for k in range(int(m.group(2)), int(m.group(3)) + 1)]
        elif part:
            out.append(part)
    return out


def read_bom(path):
    """部品表 → {"refs": {部品番号: {"pn", "value", "row"}}, "cols": {...}, "header_row"}"""
    rows = read_rows(path)
    for rno, cells in rows[:30]:
        low = {k: str(v).strip().lower() for k, v in cells.items()}
        ref_col = next((k for k, v in low.items() if v in HEAD_REF), None)
        if ref_col is None:
            continue
        pn_col = next((k for k, v in low.items() if v in HEAD_PN), None)
        val_col = next((k for k, v in low.items() if v in HEAD_VAL), None)
        refs = {}
        for r2, c2 in rows:
            if r2 <= rno:
                continue
            for ref in expand_refs(str(c2.get(ref_col, ""))):
                refs[ref] = {"pn": str(c2.get(pn_col, "")).strip() if pn_col is not None else "",
                             "value": str(c2.get(val_col, "")).strip() if val_col is not None else "",
                             "row": r2, "count": len(expand_refs(str(c2.get(ref_col, ""))))}
        return {"refs": refs, "cols": {"ref": ref_col, "pn": pn_col, "value": val_col}, "header_row": rno}
    sys.exit(f"{path}: 部品番号の列の見出しが見つからない(見出しの例: {', '.join(HEAD_REF[:6])})")


# ---------------------------------------------------------------- 品番とフットプリント
def load_rules():
    return tomllib.loads((HERE / "bomrules.toml").read_text(encoding="utf-8")).get("pitch", [])


def pitch_of_pn(pn, rules):
    """品番 → (リードの間隔 mm, 規則の名前, 出典)。分からなければ None"""
    for r in rules:
        m = re.search(r["pattern"], pn.upper())
        if m:
            code = m.group(1)
            if code in r["codes"]:
                return float(r["codes"][code]), r["name"], r.get("src", "")
            return None
    return None


def pitch_of_footprint(fp):
    m = re.search(r"_P(\d+(?:\.\d+)?)mm", fp or "")
    return float(m.group(1)) if m else None


def board_pitches(pcb):
    """基板の 2 端子の挿入部品の、穴の中心どうしの距離 {部品番号: mm}"""
    try:
        import pcbnew
    except ImportError:
        return {}
    out = {}
    for f in pcbnew.LoadBoard(str(pcb)).GetFootprints():
        holes = [p.GetPosition() for p in f.Pads() if p.GetDrillSize().x > 0]
        if len(holes) == 2:
            out[f.GetReference()] = round(pcbnew.ToMM(math.dist((holes[0].x, holes[0].y), (holes[1].x, holes[1].y))), 3)
    return out


def part_number(fields):
    return next((fields[k].strip() for k in PN_FIELDS if fields.get(k, "").strip()), "")


def check(design, bom_path=None):
    src = str(design)
    data = knetlist.from_pcb(src) if src.endswith(".kicad_pcb") else knetlist.from_sch(src)
    comps = {c["ref"]: c for c in data["components"] if not c["ref"].startswith("#")}
    measured = board_pitches(src) if src.endswith(".kicad_pcb") else {}
    bom = read_bom(bom_path) if bom_path else None
    rules = load_rules()
    pitch, missing, extra, values = [], [], [], []
    for ref, c in sorted(comps.items(), key=lambda kv: (re.sub(r"\d", "", kv[0]), int(re.sub(r"\D", "", kv[0]) or 0))):
        pn = part_number(c.get("fields", {}))
        if bom and ref in bom["refs"] and bom["refs"][ref]["pn"]:
            pn = bom["refs"][ref]["pn"]
        if not pn:
            continue
        got = pitch_of_pn(pn, rules)
        if not got:
            continue
        want, rule, why = got
        have = pitch_of_footprint(c.get("footprint", ""))
        how = "フットプリントの名前"
        if ref in measured:
            have, how = measured[ref], "基板の穴の間隔"
        if have is None:
            pitch.append({"ref": ref, "pn": pn, "pn_pitch": want, "footprint": c.get("footprint", ""), "fp_pitch": None,
                          "ok": None, "rule": rule, "src": why, "note": "フットプリントの間隔が分からない"})
            continue
        pitch.append({"ref": ref, "pn": pn, "pn_pitch": want, "footprint": c.get("footprint", ""), "fp_pitch": have,
                      "ok": abs(want - have) < 0.05, "rule": rule, "src": why, "note": how})
    if bom:
        for ref in sorted(set(comps) - set(bom["refs"])):
            missing.append(ref)
        for ref in sorted(set(bom["refs"]) - set(comps)):
            extra.append(ref)
        for ref, row in sorted(bom["refs"].items()):
            if ref in comps and row["value"] and _norm(row["value"]) != _norm(comps[ref]["value"]):
                values.append({"ref": ref, "bom": row["value"], "design": comps[ref]["value"], "row": row["row"]})
    return {"pitch": pitch, "bom_missing": missing, "bom_extra": extra, "values": values}


def _norm(v):
    return re.sub(r"\s+", "", str(v)).replace("Ω", "").replace("μ", "µ").replace("u", "µ").lower()


# ---------------------------------------------------------------- 部品表の値を書き換える
def update(sch, xlsx, out=None, dry_run=False):
    data = knetlist.from_sch(str(sch))
    comps = {c["ref"]: c for c in data["components"]}
    bom = read_bom(xlsx)
    vcol = bom["cols"]["value"]
    if vcol is None:
        sys.exit(f"{xlsx}: 値の列の見出しが見つからない(見出しの例: {', '.join(HEAD_VAL)})")
    # 行ごと: その行の部品の値が全部同じなら、その値に(行の中で値が割れたら書き換えず知らせる)
    per_row = {}
    for ref, r in bom["refs"].items():
        if ref in comps:
            per_row.setdefault(r["row"], {"old": r["value"], "new": set(), "refs": []})
            per_row[r["row"]]["new"].add(comps[ref]["value"])
            per_row[r["row"]]["refs"].append(ref)
    changes, split = {}, []
    for row, d in sorted(per_row.items()):
        if len(d["new"]) > 1:
            split.append({"row": row, "refs": d["refs"], "values": sorted(d["new"])})
            continue
        new = next(iter(d["new"]))
        if _norm(new) != _norm(d["old"]):
            changes[row] = {"old": d["old"], "new": new, "refs": sorted(d["refs"])}
    if changes and not dry_run:
        write_cells(xlsx, out or xlsx, {(row, vcol): c["new"] for row, c in changes.items()})
    return {"changes": [{"row": r, **c} for r, c in sorted(changes.items())], "split": split,
            "written": None if dry_run or not changes else str(out or xlsx)}


def _cell_name(col, row):
    s, n = "", col + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return f"{s}{row}"


def write_cells(src, dst, values):
    """xlsx の決まったセルだけ文字列に書き換える(ほかの部品は一字も変えない。共有文字列は他のセルも
    使うので触らず、そのセルを inlineStr にする。書式 s= はそのまま)"""
    with zipfile.ZipFile(src) as z:
        sheet_name = _first_sheet(z)
        items = [(i, z.read(i.filename)) for i in z.infolist()]
    text = next(d for i, d in items if i.filename == sheet_name).decode("utf-8")
    for (row, col), val in values.items():
        name = _cell_name(col, row)
        esc = val.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        cell = re.search(r'<c r="%s"(?P<attrs>[^>]*?)(?:/>|>(?P<body>.*?)</c>)' % name, text, re.S)
        if cell:
            attrs = re.sub(r'\s+t="[^"]*"', "", cell.group("attrs"))
            text = text[:cell.start()] + f'<c r="{name}"{attrs} t="inlineStr"><is><t>{esc}</t></is></c>' + text[cell.end():]
            continue
        rowm = re.search(r'<row r="%d"[^>]*?(?:/>|>(.*?)</row>)' % row, text, re.S)
        if not rowm:
            raise SystemExit(f"{src}: 行 {row} が見つからない")
        new = f'<c r="{name}" t="inlineStr"><is><t>{esc}</t></is></c>'
        cells = list(re.finditer(r'<c r="([A-Z]+)%d"' % row, rowm.group(0)))
        at = next((m.start() for m in cells if _col(m.group(1) + str(row))[0] > col), None)
        body = rowm.group(0)
        if body.endswith("/>"):
            body = body[:-2] + ">" + new + "</row>"
        elif at is None:
            body = body[:-len("</row>")] + new + "</row>"
        else:
            body = body[:at] + new + body[at:]
        text = text[:rowm.start()] + body + text[rowm.end():]
    tmp = Path(str(dst) + ".tmp")
    with zipfile.ZipFile(tmp, "w") as z:
        for info, data in items:
            z.writestr(info, text.encode("utf-8") if info.filename == sheet_name else data)
    tmp.replace(dst)


# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(prog="kicad-local bom", description="部品表と回路図・基板の照合")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="品番のリード間隔とフットプリント、部品表と回路図の食い違い")
    c.add_argument("design")
    c.add_argument("--bom")
    c.add_argument("--json", action="store_true")
    u = sub.add_parser("update", help="部品表の値の列を回路図に合わせる")
    u.add_argument("sch")
    u.add_argument("xlsx")
    u.add_argument("-o", "--output")
    u.add_argument("--dry-run", action="store_true")
    u.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.cmd == "check":
        r = check(a.design, a.bom)
        bad = [p for p in r["pitch"] if p["ok"] is False]
        if a.json:
            print(json.dumps(r, ensure_ascii=False, indent=1))
        else:
            print(f"品番から間隔が分かる部品: {len(r['pitch'])} 個、食い違い {len(bad)} 個")
            for p in r["pitch"]:
                mark = "NG" if p["ok"] is False else ("OK" if p["ok"] else "?")
                print(f"  {mark} {p['ref']} {p['pn']}: 品番 {p['pn_pitch']:g}mm({p['rule']})"
                      f" / {p['note']} {p['fp_pitch'] if p['fp_pitch'] is not None else '—'}mm  {p['footprint']}")
            if a.bom:
                print(f"部品表に無い部品: {', '.join(r['bom_missing']) or 'なし'}")
                print(f"回路図に無い部品(部品表にだけある): {', '.join(r['bom_extra']) or 'なし'}")
                print(f"値の違い: {len(r['values'])} 件")
                for v in r["values"]:
                    print(f"  {v['ref']}(行 {v['row']}): 部品表 {v['bom']} / 回路図 {v['design']}")
        return 1 if bad or r["values"] or r["bom_missing"] or r["bom_extra"] else 0
    r = update(a.sch, a.xlsx, a.output, a.dry_run)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        for c in r["changes"]:
            print(f"  行 {c['row']}({', '.join(c['refs'])}): {c['old']} → {c['new']}")
        for s in r["split"]:
            print(f"  行 {s['row']}: 部品ごとに値が違うので書き換えない({', '.join(s['refs'])}: {' / '.join(s['values'])})")
        print(f"書き換え {len(r['changes'])} 行" + (f" → {r['written']}" if r["written"] else "(--dry-run)" if a.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
