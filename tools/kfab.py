#!/usr/bin/env python3
"""
kfab: 発注前の確認と、基板屋への入稿データ(kicad-local fab。2026-10-29)

  kicad-local fab <基板.kicad_pcb> [-o 入稿.zip] [--name brake] [--paste] [--check-only] [--force] [--json]

  1. 発注前の確認(どれか引っかかれば zip は作らず終了コード 1。--force で作る)
     - 製造データの出力設定のドリルマーク(drillshape)が「なし」でない(穴の位置に印が入ったガーバーになる)
     - DRC の要確認(違反・未配線・回路図との不一致。kicad-local drc と同じ数え方)
     - 基板の外形(Edge.Cuts)が無い
  2. ガーバー(銅箔の全層・レジスト・シルク・外形。--paste でペースト。ドリルマークなし)と、ドリル(Excellon、mm、
     めっき穴とめっき無しの穴を 1 つにまとめる)を書き、要る層がそろっているかを確かめて zip に
  中身は P板.com の入稿の形(ガーバー RS-274X ＋ Excellon のドリル 1 本、mm、ドリルマークなし)。ほかの基板屋も同じ形で受ける
  --name: zip の中のファイル名とガーバーの %TF.ProjectId に使う名前(英字。基板の名前が日本語だと、ファイル名が
          日本語になり ProjectId は「____」に化ける。基板屋のアップローダーや CAM には日本語を嫌うところがある)
  --paste: メタルマスク用にペースト層も入れる
  --check-only: 確認だけ(zip を作らない)

終了コード: 0 = 書けた(確認に引っかかりなし)、1 = 確認に引っかかった・書けなかった、2 = 使い方の誤り
"""
import argparse, json, os, re, shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def cli():
    paths = os.environ.get("PATH", "").split(os.pathsep)
    return "kicad-cli-local" if any((Path(p) / "kicad-cli-local").exists() for p in paths) else "kicad-cli"


def plot_params(text):
    m = re.search(r"\(pcbplotparams(.*?)\n\t\t\)", text, re.S) or re.search(r"\(pcbplotparams(.*?)\)\s*\)", text, re.S)
    return m.group(1) if m else ""


def checks(board, text):
    """発注前の確認 → [{"kind", "message"}]"""
    found = []
    pp = plot_params(text)
    m = re.search(r"\(drillshape\s+(\d+)\)", pp)
    if m and m.group(1) != "0":
        shape = {"1": "小さい印", "2": "実寸の印"}.get(m.group(1), m.group(1))
        found.append({"kind": "drill_marks", "message": f"製造データの出力設定でドリルマークが「{shape}」(drillshape {m.group(1)})。"
                      "そのままガーバーを出すと穴の位置に印が入る(このコマンドの出力は「なし」にして出す)"})
    if '"Edge.Cuts"' not in text and "Edge.Cuts" not in re.findall(r"\(layer \"?([^\")]+)", text):
        found.append({"kind": "no_outline", "message": "基板の外形(Edge.Cuts)の線が無い"})
    r = subprocess.run([sys.executable, str(HERE / "kdrc.py"), str(board), "--json"], capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)
    except ValueError:
        found.append({"kind": "drc_failed", "message": "DRC を実行できなかった: " + (r.stderr or r.stdout).strip()[-300:]})
    else:
        if d.get("problems"):
            kinds = {}
            for v in d.get("violations", []) + d.get("schematic_parity", []):
                if v["type"] not in d.get("noise_types", []):
                    kinds[v["type"]] = kinds.get(v["type"], 0) + 1
            if d.get("unconnected_items"):
                kinds["未配線"] = len(d["unconnected_items"])
            found.append({"kind": "drc", "message": f"DRC の要確認 {d['problems']} 件: "
                          + "、".join(f"{k} {n}" for k, n in sorted(kinds.items())) + "(kicad-local drc で場所を)"})
    return found


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"失敗(終了コード {r.returncode}): {' '.join(cmd)}\n{(r.stderr or r.stdout).strip()[-800:]}")
    return r


def make(board, out, paste=False, name=None):
    text = board.read_text(encoding="utf-8")
    pp = plot_params(text)
    aux = re.search(r"\(useauxorigin\s+(yes|true)\)", pp) is not None
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        # 写しに: ドリルマークは「なし」に(元のファイルは変えない)
        stem = name or board.stem
        work = t / (stem + ".kicad_pcb")
        work.write_text(re.sub(r"\(drillshape\s+\d+\)", "(drillshape 0)", text), encoding="utf-8")
        for side in board.parent.glob(board.stem + ".kicad_pro"):
            shutil.copy(side, t / (stem + ".kicad_pro"))
        g = t / "gbr"
        g.mkdir()
        # 層は入稿に要るものを決めて出す(基板の出力設定の層の選び方は人によって違い、外形やレジストが抜けていた)
        copper = re.findall(r'\(\d+\s+"((?:F|B|In\d+)\.Cu)"', text.split("(setup", 1)[0])
        layers = copper + ["F.Mask", "B.Mask", "F.SilkS", "B.SilkS", "Edge.Cuts"] + (["F.Paste", "B.Paste"] if paste else [])
        opts = ["--subtract-soldermask"] if re.search(r"\(subtractmaskfromsilk\s+(yes|true)\)", pp) else []
        opts += ["--use-drill-file-origin"] if aux else []
        run([cli(), "pcb", "export", "gerbers", "--layers", ",".join(layers), *opts, "-o", str(g) + os.sep, str(work)])
        run([cli(), "pcb", "export", "drill", "--format", "excellon", "--excellon-units", "mm",
             "--drill-origin", "plot" if aux else "absolute", "-o", str(g) + os.sep, str(work)])
        files = sorted(p for p in g.iterdir() if p.is_file() and p.stat().st_size > 0)
        names = [p.name for p in files]
        need = {"表の銅箔": r"(F_Cu|\.gtl$)", "裏の銅箔": r"(B_Cu|\.gbl$)", "外形": r"(Edge_Cuts|\.gm1$)",
                "表のレジスト": r"(F_Mask|\.gts$)", "裏のレジスト": r"(B_Mask|\.gbs$)", "ドリル": r"\.drl$"}
        missing = [k for k, pat in need.items() if not any(re.search(pat, n, re.I) for n in names)]
        if missing:
            raise SystemExit(f"入稿に要る層が出ていない: {'・'.join(missing)}(基板の出力設定の層の選び方を確かめる)。"
                             f"出たファイル: {', '.join(names) or 'なし'}")
        drills = [p for p in files if p.suffix.lower() == ".drl"]
        holes = sum(len(re.findall(r"^X[-0-9.]+Y", p.read_text(errors="replace"), re.M)) for p in drills)
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for p in files:
                z.write(p, p.name)
        return {"zip": str(out), "files": [p.name for p in files], "drill_files": [p.name for p in drills], "holes": holes,
                "drill_origin": "plot" if aux else "absolute"}


def main():
    ap = argparse.ArgumentParser(prog="kicad-local fab", description=__doc__.split("\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("\n", 2)[2])
    ap.add_argument("board")
    ap.add_argument("-o", "--output")
    ap.add_argument("--paste", action="store_true")
    ap.add_argument("--name", help="zip の中のファイル名・ProjectId に使う英字の名前")
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    board = Path(a.board)
    if not board.is_file() or board.suffix != ".kicad_pcb":
        print(f"基板(.kicad_pcb)がない: {board}", file=sys.stderr)
        return 2
    found = checks(board, board.read_text(encoding="utf-8"))
    result = {"board": str(board), "checks": found, "written": None}
    if not a.check_only and (not found or a.force):
        out = Path(a.output or board.with_name(f"{board.stem}_入稿.zip"))
        if a.name and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", a.name):
            print(f"--name は英数字と _ . - だけ: {a.name}", file=sys.stderr)
            return 2
        result["written"] = make(board, out, a.paste, a.name)
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        print(f"発注前の確認: {'引っかかりなし' if not found else str(len(found)) + ' 件'}")
        for f in found:
            print(f"  ! {f['message']}")
        w = result["written"]
        if w:
            print(f"入稿データ: {w['zip']}({len(w['files'])} ファイル、ドリル {', '.join(w['drill_files'])} に穴 {w['holes']} 個、mm、"
                  f"めっき穴とめっき無しを 1 つに、ドリルマークなし)")
            for n in w["files"]:
                print(f"    {n}")
            if not a.name and not board.stem.isascii():
                print(f"  注意: ファイル名とガーバーの ProjectId が日本語の名前({board.stem})。日本語を嫌う基板屋には --name brake のように英字の名前を")
        elif found and not a.check_only:
            print("入稿データは作らなかった(直してからもう一度。このまま作るなら --force)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
