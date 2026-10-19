#!/usr/bin/env python3
"""
krender: kicad-local render / step の中身(2026-10-20)

  kicad-local render <基板> [視点] [出力.png] [--placeholder-boxes] [--height 5] [-w 1600 -h 1200]
      視点は top / bottom / left / right / front / back / iso / iso-hq(出力を省くと <基板名>_<視点>.png)
  kicad-local step <基板> [出力.step] [--placeholder-boxes] [--height 5] [kicad-cli pcb export step の引数 ...]

どちらも先に 3D モデルの無いフットプリントを一覧にする(STEP・画像では胴体が抜けるため)。
--placeholder-boxes で、無いモデルを F.Fab / F.CrtYd の外形の箱に置き換えた基板の写しから作る(元の基板は変えない)。
"""
import argparse, os, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
VIEWS = {"top": ["--side", "top"], "bottom": ["--side", "bottom"], "left": ["--side", "left"],
         "right": ["--side", "right"], "front": ["--side", "front"], "back": ["--side", "back"],
         "iso": ["--rotate", "-45,0,45", "--perspective"],
         "iso-hq": ["--rotate", "-45,0,45", "--perspective", "--quality", "high", "--floor"]}


def prepare(pcb, boxes, height, tmp):
    """3D モデルを点検し、--placeholder-boxes なら箱を入れた写しを作って、その道筋を返す"""
    subprocess.run(["kicad-python", str(HERE / "k3d.py"), "check", pcb], stdout=subprocess.DEVNULL)
    if not boxes:
        return pcb
    out = Path(tmp) / Path(pcb).name
    pro = Path(pcb).with_suffix(".kicad_pro")
    if pro.exists():
        shutil.copy(pro, out.with_suffix(".kicad_pro"))
    r = subprocess.run(["kicad-python", str(HERE / "k3d.py"), "boxes", pcb, str(out), "--height", str(height)])
    if r.returncode != 0 or not out.exists():
        sys.exit("箱を入れた写しを作れなかった")
    return str(out)


def main():
    cmd = sys.argv[1]
    ap = argparse.ArgumentParser(prog=f"kicad-local {cmd}", add_help=True, conflict_handler="resolve")
    ap.add_argument("pcb")
    ap.add_argument("--placeholder-boxes", action="store_true", help="無い 3D モデルを外形の箱で代用")
    ap.add_argument("--height", type=float, default=5.0, help="箱の高さ mm(部品の欄 Height が無いとき。既定 5)")
    if cmd == "render":
        ap.add_argument("views", nargs="?", default="iso")
        ap.add_argument("out", nargs="?")
        ap.add_argument("-w", "--width", default="1600")
        ap.add_argument("-h", "--height-px", default="1200")
        a = ap.parse_args(sys.argv[2:])
    else:
        ap.add_argument("out", nargs="?")
        a, rest = ap.parse_known_args(sys.argv[2:])
        if a.out and a.out.startswith("-"):
            rest.insert(0, a.out); a.out = None
    if not Path(a.pcb).is_file():
        sys.exit(f"基板がない: {a.pcb}")
    stem = a.pcb[:-len(".kicad_pcb")] if a.pcb.endswith(".kicad_pcb") else a.pcb
    with tempfile.TemporaryDirectory() as tmp:
        src = prepare(a.pcb, a.placeholder_boxes, a.height, tmp)
        if cmd == "step":
            out = a.out or stem + ".step"
            r = subprocess.run(["kicad-cli-local", "pcb", "export", "step", *rest, "-o", os.path.abspath(out), src],
                               capture_output=True, text=True)
            msg = (r.stdout + r.stderr).strip()
            print("\n".join(l for l in msg.splitlines() if l.strip() and not l.startswith("Export time")))
            print(f"→ {out}" if r.returncode == 0 else f"失敗(終了コード {r.returncode})")
            return r.returncode
        v = a.views
        if v not in VIEWS:
            sys.exit(f"視点 {v} は無い。使えるのは {' / '.join(VIEWS)}")
        out = a.out or f"{stem}_{v}.png"
        r = subprocess.run(["kicad-cli-local", "pcb", "render", *VIEWS[v], "-w", a.width, "-h", a.height_px,
                            "-o", os.path.abspath(out), src], capture_output=True, text=True)
        ok = r.returncode == 0 and Path(out).exists()
        print(f"→ {out}" if ok else f"× 失敗\n{(r.stdout + r.stderr)[-800:]}")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
