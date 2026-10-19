#!/usr/bin/env python3
"""
knetlist: 回路図・基板のネットリストを JSON に(kicad-local netlist。2026-10-20)

  kicad-local netlist <回路図.kicad_sch | 基板.kicad_pcb> [--json] [-o 出力.json]

  回路図: kicad-cli sch export netlist --format kicadxml を読み直したもの
  基板:   パッドのネット(基板に入っているつながり。回路図との食い違いを見るときは両方を出して比べる)
  形: {"source": "schematic"|"board", "components": [{ref, value, footprint, fields{}, pins[{number, name, type, net}]}],
       "nets": [{name, code, nodes[{ref, pin, name, type}]}]}
  --json が無ければ要約(部品・ネットの数と、ネットごとの端子)を表示
"""
import argparse, json, os, subprocess, sys, tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


_ESC = {"{slash}": "/", "{backslash}": "\\", "{lt}": "<", "{gt}": ">", "{colon}": ":", "{dblquote}": '"',
        "{comma}": ",", "{tab}": "\t", "{return}": "\n", "{space}": " "}


def unescape(name):
    """基板の中のネット名({slash} など)を、回路図のネットリストと同じ見た目に"""
    if "{" not in name:
        return name
    for k, v in _ESC.items():
        name = name.replace(k, v)
    return name


def from_sch(sch):
    with tempfile.TemporaryDirectory() as t:
        out = Path(t) / "n.xml"
        cli = "kicad-cli-local" if any((Path(p) / "kicad-cli-local").exists() for p in os.environ.get("PATH", "").split(os.pathsep)) else "kicad-cli"
        r = subprocess.run([cli, "sch", "export", "netlist", "--format", "kicadxml", "-o", str(out), sch],
                           capture_output=True, text=True)
        if not out.exists():
            sys.exit(f"ネットリストを書き出せなかった: {(r.stdout + r.stderr).strip()[-500:]}")
        root = ET.parse(out).getroot()
    libpins = {}
    for lp in root.iter("libpart"):
        libpins[(lp.get("lib"), lp.get("part"))] = [
            {"number": p.get("num"), "name": p.get("name"), "type": p.get("type")} for p in lp.iter("pin")]
    comps = {}
    for c in root.find("components").iter("comp"):
        ref = c.get("ref")
        fields = {f.get("name"): (f.text or "") for f in c.iter("field")}
        props = {p.get("name"): p.get("value") for p in c.findall("property")}
        ls = c.find("libsource")
        sp = c.find("sheetpath")
        comps[ref] = {"ref": ref, "value": (c.findtext("value") or ""), "footprint": (c.findtext("footprint") or ""),
                      "datasheet": (c.findtext("datasheet") or ""), "description": (c.findtext("description") or ""),
                      "lib": f"{ls.get('lib')}:{ls.get('part')}" if ls is not None else "",
                      "sheet": sp.get("names") if sp is not None else "",
                      "fields": fields, "properties": props,
                      "pins": [dict(p, net=None) for p in libpins.get((ls.get("lib"), ls.get("part")) if ls is not None else None, [])]}
    nets = []
    for n in root.find("nets").iter("net"):
        nodes = [{"ref": d.get("ref"), "pin": d.get("pin"), "name": d.get("pinfunction") or "", "type": d.get("pintype") or ""}
                 for d in n.iter("node")]
        nets.append({"name": n.get("name"), "code": int(n.get("code")), "nodes": nodes})
        for d in nodes:
            c = comps.get(d["ref"])
            if not c:
                continue
            pin = next((p for p in c["pins"] if p["number"] == d["pin"]), None)
            if pin is None:
                pin = {"number": d["pin"], "name": d["name"], "type": d["type"], "net": None}
                c["pins"].append(pin)
            pin["net"] = n.get("name")
    return {"source": "schematic", "file": sch, "components": list(comps.values()), "nets": nets}


def from_pcb(pcb, board=None):
    import pcbnew
    b = board or pcbnew.LoadBoard(pcb)
    comps, nets = [], {}
    for f in b.GetFootprints():
        fields = {fl.GetName(): fl.GetText() for fl in f.GetFields()}
        pins = []
        for p in f.Pads():
            num = p.GetNumber()
            net = unescape(p.GetNetname()) or None
            pin = {"number": num, "name": p.GetPinFunction(), "type": p.GetPinType(), "net": net}
            pins.append(pin)
            if net and num:
                nets.setdefault(net, []).append({"ref": f.GetReference(), "pin": num, "name": pin["name"], "type": pin["type"]})
        pos = f.GetPosition()
        comps.append({"ref": f.GetReference(), "value": f.GetValue(), "footprint": f.GetFPIDAsString(),
                      "side": "bottom" if f.IsFlipped() else "top",
                      "position": [round(pcbnew.ToMM(pos.x), 4), round(pcbnew.ToMM(pos.y), 4)],
                      "rotation": round(f.GetOrientationDegrees(), 3),
                      "fields": fields, "pins": pins})
    codes = {}
    for name, ni in b.GetNetsByName().items():
        codes[unescape(str(name))] = ni.GetNetCode()
    out = [{"name": n, "code": codes.get(n), "nodes": sorted(v, key=lambda d: (d["ref"], d["pin"]))} for n, v in nets.items()]
    out.sort(key=lambda n: (n["code"] is None, n["code"] or 0, n["name"]))
    return {"source": "board", "file": pcb, "components": comps, "nets": out}


def main():
    ap = argparse.ArgumentParser(prog="kicad-local netlist", description="回路図・基板のネットリストを JSON に")
    ap.add_argument("file", help=".kicad_sch か .kicad_pcb(.kicad_pro なら同じ名前の回路図)")
    ap.add_argument("--json", action="store_true", help="JSON で出す")
    ap.add_argument("-o", "--output", help="JSON を書くファイル(--json の代わりに)")
    a = ap.parse_args()
    f = a.file
    if f.endswith(".kicad_pro"):
        f = f[:-len(".kicad_pro")] + ".kicad_sch"
    if not Path(f).is_file():
        sys.exit(f"ファイルがない: {f}")
    d = from_pcb(f) if f.endswith(".kicad_pcb") else from_sch(f)
    if a.output:
        Path(a.output).write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return 0
    print(f"{'回路図' if d['source'] == 'schematic' else '基板'}: 部品 {len(d['components'])}、ネット {len(d['nets'])}")
    for n in d["nets"]:
        print(f"  {n['name']}: " + " ".join(f"{x['ref']}.{x['pin']}" for x in n["nodes"]))
    if a.output:
        print(f"→ {a.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
