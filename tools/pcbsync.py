#!/usr/bin/env python3
"""
pcbsync: 回路図の変更を基板に反映する(画面の「回路図から基板を更新」の画面なし版。ローカル道具、2026-09-25)

  kicad-local pcbsync <回路図.kicad_sch> <基板.kicad_pcb> <出力.kicad_pcb> [--at x,y] [--delete] [--swap] [--dry-run] [--json]
  kicad-local pcb update-from-schematic [--schematic 回路図] -o 出力 <基板> [--dry-run] [--json]   (kicad-cli 風の別名。
      kicad-cli-local pcb update-from-schematic も同じ)

  - 回路図にあって基板に無い部品を、フットプリントのライブラリから読み込んで --at の位置(既定: 基板の右外)に並べる
  - 端子のネットを回路図どおりにする(ネット名も)
  - つながりが変わったネットの配線は消す(つながりの違う配線を残すと短絡になるため) → 引き直しが要るネットとして表示
  - 値・フットプリント名の違いは表示(値は直す。フットプリントの差し替えはしない)
  - 欄(Datasheet・MPN など回路図の欄)を写す(--no-fields で写さない)
  - 基板にだけある部品は表示(--delete で消す)
  - 書いたあと回路図ともう一度照合し(部品の有無・値・フットプリント・端子のネット)、さらに本物の DRC の
    --schematic-parity でも照合する(--no-drc で省く)。残りがあれば終了コード 1
  - --dry-run は書かずに変わる所だけ、--json で機械が読む形
  部品は部品番号で対応させる(画面の F8 の既定は回路図の記号の UUID。番号を振り直した直後は画面で)。
  あとは kicad-local route で置いて配線する(findspace / place / autoroute)。
"""
import argparse, json, re, subprocess, sys, tempfile
from pathlib import Path
import pcbnew

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import knetlist
FPLIB = Path("/usr/share/kicad/footprints")


def netlist(sch):
    """回路図の部品: 値・フットプリント・欄・端子のネット(knetlist の kicadxml 読み)"""
    d = knetlist.from_sch(str(sch))
    comps = {}
    for c in d["components"]:
        comps[c["ref"]] = {"value": c["value"], "fp": c["footprint"],
                           "fields": {k: v for k, v in c["fields"].items() if k not in ("Footprint", "Reference", "Value")},
                           "pins": {p["number"]: p["net"] for p in c["pins"] if p.get("net")}}
    return comps


def set_field(f, name, text):
    try:
        f.SetField(name, text)
    except Exception:   # 8.0.9: 無ければ作る
        for fl in f.GetFields():
            if fl.GetName() == name:
                fl.SetText(text)
                return
        fl = pcbnew.PCB_FIELD(f, f.GetNextFieldId(), name)
        fl.SetText(text)
        fl.SetVisible(False)
        f.AddField(fl)


def parity(sch, pcb, board=None):
    """回路図と基板の食い違い(部品の有無・値・フットプリント・端子のネット)。DRC の --schematic-parity と同じ観点"""
    s = {c["ref"]: c for c in knetlist.from_sch(str(sch))["components"] if not c["ref"].startswith("#")}
    b = {c["ref"]: c for c in knetlist.from_pcb(str(pcb), board)["components"]}
    out = []
    for r in sorted(set(s) - set(b)):
        if s[r]["footprint"]:
            out.append({"kind": "missing_footprint", "ref": r})
    for r in sorted(set(b) - set(s)):
        if not r.startswith(("#", "H", "kibuzzard")) and r != "REF**":
            out.append({"kind": "extra_footprint", "ref": r})
    for r in sorted(set(s) & set(b)):
        if s[r]["value"] != b[r]["value"]:
            out.append({"kind": "value", "ref": r, "sch": s[r]["value"], "pcb": b[r]["value"]})
        if s[r]["footprint"] and s[r]["footprint"] != b[r]["footprint"]:
            out.append({"kind": "footprint", "ref": r, "sch": s[r]["footprint"], "pcb": b[r]["footprint"]})
        sp = {p["number"]: p["net"] for p in s[r]["pins"] if p.get("net")}
        for p in b[r]["pins"]:
            want = sp.get(p["number"])
            if want and p["net"] != want:
                out.append({"kind": "net", "ref": r, "pin": p["number"], "sch": want, "pcb": p["net"]})
    return out


def drc_parity(sch, pcb, pro):
    """本物の DRC(kicad-cli pcb drc --schematic-parity)で照合。基板と回路図を同じ名前で一時の場所に並べる
    (回路図は下の階層ごとシンボリックリンク、できなければ写し)。戻り値: 食い違いの一覧、DRC が動かなければ None"""
    import json as _j, os, shutil
    sch, pcb = Path(sch).resolve(), Path(pcb).resolve()
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        for f in sch.parent.glob("*.kicad_sch"):
            try:
                os.symlink(f, t / f.name)
            except OSError:
                shutil.copy(f, t / f.name)
        root = t / "__parity__.kicad_sch"
        try:
            os.symlink(sch, root)
        except OSError:
            shutil.copy(sch, root)
        if pro and Path(pro).exists():
            shutil.copy(pro, t / "__parity__.kicad_pro")
        shutil.copy(pcb, t / "__parity__.kicad_pcb")
        cli = "kicad-cli-local" if shutil.which("kicad-cli-local") else "kicad-cli"
        subprocess.run([cli, "pcb", "drc", "--schematic-parity", "--format", "json", "-o", str(t / "d.json"),
                        str(t / "__parity__.kicad_pcb")], capture_output=True)
        if not (t / "d.json").exists():
            return None
        d = _j.loads((t / "d.json").read_text(encoding="utf-8"))
        return [{"type": v.get("type"), "description": v.get("description"),
                 "items": [i.get("description") for i in v.get("items", [])]} for v in d.get("schematic_parity", [])]


def main():
    ap = argparse.ArgumentParser(prog="kicad-local pcbsync",
                                 description="回路図の変更を基板に反映する(画面の「回路図から基板を更新」F8 の画面なし版)")
    ap.add_argument("files", nargs="+", help="<回路図> <基板> <出力>(kicad-cli 風に <基板> だけでも: 回路図は --schematic か同じ名前)")
    ap.add_argument("--schematic", "-s", help="回路図(省くと基板と同じ名前の .kicad_sch)")
    ap.add_argument("-o", "--output", help="出力の基板")
    ap.add_argument("--at", help="新しい部品を並べ始める位置 x,y(mm)。既定は基板の右外")
    ap.add_argument("--delete", action="store_true", help="基板にだけある部品を消す")
    ap.add_argument("--swap", action="store_true", help="フットプリントが回路図と違う部品を差し替える(位置・向き・面は保つ。その部品の配線は消す)")
    ap.add_argument("--no-fields", action="store_true", help="欄(Datasheet・MPN など)は写さない")
    ap.add_argument("--no-drc", action="store_true", help="書いたあとの DRC の照合(--schematic-parity)を省く")
    ap.add_argument("--dry-run", action="store_true", help="基板を書かず、変わる所だけを表示")
    ap.add_argument("--json", action="store_true", help="結果を JSON で(変更・引き直しのネット・照合の残り)")
    a = ap.parse_args()
    fs = list(a.files)
    if len(fs) == 3:
        a.sch, a.pcb, a.out = fs
    elif len(fs) == 2 and (fs[0].endswith(".kicad_sch") or a.schematic is None and not fs[1].endswith(".kicad_pcb")):
        (a.sch, a.pcb), a.out = fs, a.output
    elif len(fs) == 2:
        a.pcb, a.out = fs; a.sch = a.schematic
    else:
        a.pcb = fs[0]; a.sch = a.schematic; a.out = a.output
    if not a.sch:
        a.sch = str(Path(a.pcb).with_suffix(".kicad_sch"))
    if not a.out and not a.dry_run:
        ap.error("出力の基板(-o)を指定する(元の基板は上書きしない。見るだけなら --dry-run)")
    if a.out and Path(a.out).resolve() == Path(a.pcb).resolve():
        ap.error("出力が元の基板と同じ。別の名前にする")
    for f in (a.sch, a.pcb):
        if not Path(f).is_file():
            ap.error(f"ファイルがない: {f}")
    rep = {"added": [], "values": [], "fields": [], "footprints": [], "swapped": [], "pad_nets": [],
           "extra": [], "deleted": [], "missing_library": [], "redo_nets": [], "tracks_removed": 0}
    say = (lambda *x, **k: None) if a.json else print

    comps = netlist(a.sch)
    b = pcbnew.LoadBoard(a.pcb)
    fps = {f.GetReference(): f for f in b.GetFootprints()}

    # 基板の中のネット名は「/」などを {slash} にしてある。回路図のネットリストは元の見た目なので、見た目でそろえる
    byname = {knetlist.unescape(str(n)): ni for n, ni in b.GetNetsByName().items()}
    def nname(p):
        return knetlist.unescape(p.GetNetname())

    def netinfo(name):
        ni = byname.get(name) or b.FindNet(name)
        if not ni:
            ni = pcbnew.NETINFO_ITEM(b, name)
            b.Add(ni)
            byname[name] = ni
        return ni

    # 変更前のつながり(ネット → 端子の集合)
    before = {}
    for f in b.GetFootprints():
        for p in f.Pads():
            if nname(p):
                before.setdefault(nname(p), set()).add(f"{f.GetReference()}.{p.GetNumber()}")

    bb = b.GetBoardEdgesBoundingBox()
    x0, y0 = (float(v) for v in a.at.split(",")) if a.at else (pcbnew.ToMM(bb.GetRight()) + 10, pcbnew.ToMM(bb.GetTop()))
    added = []
    for ref, c in sorted(comps.items()):
        if ref in fps or not c["fp"] or ref.startswith("#"):
            continue
        lib, name = c["fp"].split(":", 1)
        f = pcbnew.FootprintLoad(str(FPLIB / f"{lib}.pretty"), name)
        if f is None:
            say(f"  × {ref}: フットプリント {c['fp']} がライブラリに無い")
            rep["missing_library"].append({"ref": ref, "footprint": c["fp"]})
            continue
        f.SetReference(ref)
        f.SetValue(c["value"])
        f.SetFPIDAsString(c["fp"])
        f.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x0), pcbnew.FromMM(y0 + 8 * len(added))))
        b.Add(f)
        fps[ref] = f
        added.append(ref)
        rep["added"].append({"ref": ref, "value": c["value"], "footprint": c["fp"], "at": [round(x0, 3), round(y0 + 8 * (len(added) - 1), 3)]})
        say(f"  追加 {ref}({c['value']}、{c['fp']})→ ({x0:.1f}, {y0 + 8 * (len(added) - 1):.1f}) に仮置き")

    changed_val = []
    swapped = []
    for ref, c in comps.items():
        f = fps.get(ref)
        if not f:
            continue
        if f.GetValue() != c["value"]:
            changed_val.append(f"{ref}: {f.GetValue()} → {c['value']}")
            rep["values"].append({"ref": ref, "old": f.GetValue(), "new": c["value"]})
            f.SetValue(c["value"])
        if c["fp"] and f.GetFPIDAsString() != c["fp"]:
            rep["footprints"].append({"ref": ref, "old": f.GetFPIDAsString(), "new": c["fp"], "swap": a.swap})
            if not a.swap:
                say(f"  ! {ref}: フットプリントが回路図と違う(基板 {f.GetFPIDAsString()} / 回路図 {c['fp']})。--swap で差し替え")
            else:
                lib, name = c["fp"].split(":", 1)
                nf = pcbnew.FootprintLoad(str(FPLIB / f"{lib}.pretty"), name)
                if nf is None:
                    say(f"  × {ref}: フットプリント {c['fp']} がライブラリに無い")
                    rep["missing_library"].append({"ref": ref, "footprint": c["fp"]})
                else:
                    nf.SetReference(ref)
                    nf.SetValue(f.GetValue())
                    nf.SetFPIDAsString(c["fp"])
                    if f.GetLayer() == pcbnew.B_Cu:
                        nf.Flip(nf.GetPosition(), getattr(pcbnew, "FLIP_DIRECTION_TOP_BOTTOM", False))
                    nf.SetPosition(f.GetPosition())
                    nf.SetOrientation(f.GetOrientation())
                    oldnets = {p.GetNumber(): nname(p) for p in f.Pads()}
                    b.Remove(f)
                    b.Add(nf)
                    for p in nf.Pads():
                        if oldnets.get(p.GetNumber()):
                            p.SetNet(netinfo(oldnets[p.GetNumber()]))
                    say(f"  差し替え {ref}: {f.GetFPIDAsString()} → {c['fp']}(位置・向きはそのまま。配線は引き直し)")
                    swapped.append(ref)
                    rep["swapped"].append(ref)
                    fps[ref] = nf
                    f = nf
        for p in f.Pads():
            want = c["pins"].get(p.GetNumber())
            if want and nname(p) != want:
                rep["pad_nets"].append({"ref": ref, "pad": p.GetNumber(), "old": nname(p), "new": want})
                p.SetNet(netinfo(want))
        if not a.no_fields:
            have = {fl.GetName(): fl.GetText() for fl in f.GetFields()}
            for k, v in c["fields"].items():
                if have.get(k, "") != v and (v or k in have):
                    rep["fields"].append({"ref": ref, "field": k, "old": have.get(k), "new": v})
                    say(f"  欄 {ref}.{k}: {have.get(k)!r} → {v!r}")
                    set_field(f, k, v)
    for v in changed_val:
        say(f"  値 {v}")

    extra = [r for r in fps if r not in comps and not r.startswith(("#", "H", "kibuzzard"))]
    for r in extra:
        say(f"  基板にだけある部品: {r}" + ("(消した)" if a.delete else ""))
        rep["deleted" if a.delete else "extra"].append(r)
        if a.delete:
            b.Remove(fps[r])

    after = {}
    for f in b.GetFootprints():
        for p in f.Pads():
            if nname(p):
                after.setdefault(nname(p), set()).add(f"{f.GetReference()}.{p.GetNumber()}")
    # 端子の組が変わったネットの配線を消す(新しい部品が加わっただけのネットは、既存の配線を残してよい)
    newrefs = set(added)
    # 差し替えた部品につながる配線は、端子の位置が変わるので消す
    for ref in swapped:
        nets = {nname(p) for p in fps[ref].Pads() if nname(p) and nname(p) not in ("GND", "/GND")}
        for t in list(b.GetTracks()):
            if knetlist.unescape(t.GetNetname()) in nets:
                b.Remove(t)
        say(f"  {ref} のネット {', '.join(sorted(nets))} の配線を消した(GND はベタなので残す)")
    redo = []
    for net in set(before) | set(after):
        old = before.get(net, set())
        new = {x for x in after.get(net, set()) if x.split(".")[0] not in newrefs}
        if old != new or (after.get(net, set()) - old):
            redo.append(net)
    removed = 0
    for t in list(b.GetTracks()):
        if knetlist.unescape(t.GetNetname()) in redo:
            old = before.get(knetlist.unescape(t.GetNetname()), set())
            new = {x for x in after.get(knetlist.unescape(t.GetNetname()), set()) if x.split(".")[0] not in newrefs}
            if old != new:   # 既存の端子どうしのつながりが変わった → 残すと短絡
                b.Remove(t)
                removed += 1
    rep["redo_nets"] = [{"net": n, "nodes": sorted(after.get(n, set()))} for n in sorted(redo)]
    rep["tracks_removed"] = removed
    rep["dry_run"] = a.dry_run
    if a.dry_run:
        rc = 0
    else:
        pcbnew.SaveBoard(a.out, b)
        # 照合: 書いた基板を回路図ともう一度比べる(DRC の --schematic-parity と同じ観点。残りがあれば終了コード 1)
        rep["parity"] = parity(a.sch, a.out, b)
        rep["drc_parity"] = None if a.no_drc else drc_parity(a.sch, a.out, Path(a.pcb).with_suffix(".kicad_pro"))
        rc = 1 if rep["parity"] or rep["drc_parity"] else 0
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return rc
    print(f"\n引き直しが要るネット({len(redo)}):")
    for net in sorted(redo):
        print(f"  {net}: " + " ".join(sorted(after.get(net, set()))))
    if a.dry_run:
        print(f"(--dry-run: 基板は書いていない。消える配線 {removed} 本)")
        return 0
    print(f"消した配線 {removed} 本 → {a.out}")
    if rep["parity"]:
        print(f"照合: 回路図との食い違いが {len(rep['parity'])} 件残った")
        for x in rep["parity"][:20]:
            print("  " + " ".join(f"{k}={v}" for k, v in x.items()))
    else:
        print("照合: 回路図と一致(部品の有無・値・フットプリント・端子のネット)")
    if rep["drc_parity"] is None and not a.no_drc:
        print("DRC の照合(--schematic-parity): 動かせなかった")
    elif rep["drc_parity"] is not None:
        print(f"DRC の照合(--schematic-parity): {len(rep['drc_parity'])} 件" + ("" if rep["drc_parity"] else "(一致)"))
        for x in rep["drc_parity"][:20]:
            print(f"  [{x['type']}] {x['description']}  {' / '.join(i for i in x['items'] if i)}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
