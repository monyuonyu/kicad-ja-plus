#!/usr/bin/env python3
"""
k3d: 基板の 3D モデルの点検と、無いモデルの代わりの箱(kicad-local models / step / render から使う。2026-10-20)

  kicad-python k3d.py check <基板> [--json]
      3D モデルの無い(ファイルが見つからない・指定が無い)フットプリントの一覧。あれば終了コード 1
  kicad-python k3d.py boxes <基板> <出力.kicad_pcb> [--height 5] [--field Height]
      無いモデルの代わりに、F.Fab(無ければ F.CrtYd、それも無ければパッド)の外形の箱を STEP で作り、
      その箱をモデルにした基板の写しを書く(元の基板は変えない)。箱の高さは部品の欄 Height(mm)、無ければ --height

モデルの場所は ${KICADn_3DMODEL_DIR}・${KISYS3DMOD}(未設定なら /usr/share/kicad/3dmodels)・${KIPRJMOD}・
基板からの相対で探す。.wrl の指定は同じ名前の .step/.stp があれば見つかった扱い(STEP の書き出しと同じ)。
"""
import argparse, json, math, os, re, sys
from pathlib import Path
import pcbnew

SYS3D = "/usr/share/kicad/3dmodels"


def resolve(name, board_dir):
    """モデルのファイル名を実際の場所にする。見つからなければ None"""
    def var(k, raw):
        if k == "KIPRJMOD":
            return str(board_dir)
        v = os.environ.get(k)
        if v:
            return v
        if re.fullmatch(r"KICAD\d*_3DMODEL_DIR|KISYS3DMOD", k):
            return SYS3D
        return raw
    p = re.sub(r"\$\{(\w+)\}|\$\((\w+)\)", lambda m: var(m.group(1) or m.group(2), m.group(0)), name)
    p = Path(p).expanduser()
    if not p.is_absolute():
        p = Path(board_dir) / p
    cands = [p]
    if p.suffix.lower() in (".wrl", ".vrml"):
        cands += [p.with_suffix(s) for s in (".step", ".stp", ".STEP", ".STP")]
    for c in cands:
        if c.is_file():
            return str(c)
    return None


def field(f, name):
    for fl in f.GetFields():
        if fl.GetName().lower() == name.lower():
            return fl.GetText()
    return None


def scan(b, board_dir):
    """フットプリントごとに: models(指定と見つかった場所)、状態 ok / missing / none"""
    res = []
    for f in b.GetFootprints():
        ref = f.GetReference()
        ms = [m for m in f.Models()]
        shown = [m for m in ms if m.m_Show]
        info = {"ref": ref, "footprint": f.GetFPIDAsString(), "value": f.GetValue(),
                "side": "bottom" if f.IsFlipped() else "top", "pads": len(list(f.Pads())),
                "models": [{"file": m.m_Filename, "found": resolve(m.m_Filename, board_dir), "shown": bool(m.m_Show)} for m in ms]}
        board_only = bool(f.GetAttributes() & getattr(pcbnew, "FP_BOARD_ONLY", 0))
        if any(m["found"] for m in info["models"] if m["shown"]):
            info["status"] = "ok"
        elif shown:
            info["status"] = "missing"      # 指定はあるが、ファイルが無い
        elif ms:
            info["status"] = "hidden"       # 指定はあるが全部「表示しない」(意図したもの)
        elif info["pads"] and not board_only:
            info["status"] = "none"         # 指定が無い
        else:
            info["status"] = "skip"         # パッドの無い物・基板のみ(ロゴ・取り付け穴など)
        res.append(info)
    return res


def local_pts(f, layers):
    """フットプリントの層 layers の図形の点を、表・回転 0 の部品の座標(mm、y は下向き)で"""
    pos = f.GetPosition()
    a = math.radians(f.GetOrientationDegrees())
    ca, sa = math.cos(a), math.sin(a)
    flip = f.IsFlipped()
    def tolocal(x, y):
        dx, dy = pcbnew.ToMM(x - pos.x), pcbnew.ToMM(y - pos.y)
        # 画面の y は下向きなので、角度 a の回転を戻すのは (dx, dy) を -a … KiCad の向きは反時計回りが正
        lx = dx * ca - dy * sa
        ly = dx * sa + dy * ca
        return (lx, -ly) if flip else (lx, ly)
    pts = []
    for it in f.GraphicalItems():
        if it.GetLayer() not in layers or not isinstance(it, pcbnew.PCB_SHAPE):
            continue
        sh = it.GetShape()
        if sh == pcbnew.SHAPE_T_POLY:
            ps = it.GetPolyShape()
            for i in range(ps.TotalVertices()):
                v = ps.CVertex(i)
                pts.append(tolocal(v.x, v.y))
        elif sh in (pcbnew.SHAPE_T_SEGMENT, pcbnew.SHAPE_T_RECT):
            s, e = it.GetStart(), it.GetEnd()
            pts += [tolocal(s.x, s.y), tolocal(e.x, e.y)]
            if sh == pcbnew.SHAPE_T_RECT:
                pts += [tolocal(s.x, e.y), tolocal(e.x, s.y)]
        else:   # 円・円弧・曲線は外接の箱の角
            bb = it.GetBoundingBox()
            for x in (bb.GetLeft(), bb.GetRight()):
                for y in (bb.GetTop(), bb.GetBottom()):
                    pts.append(tolocal(x, y))
    return pts


def outline(f):
    """箱の底面(部品の座標の長方形 x0,y0,x1,y1)と、どこから取ったか"""
    for name, layers in (("F.Fab", (pcbnew.F_Fab, pcbnew.B_Fab)), ("F.CrtYd", (pcbnew.F_CrtYd, pcbnew.B_CrtYd))):
        pts = local_pts(f, layers)
        if len(pts) >= 2:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            if max(xs) - min(xs) > 0.01 and max(ys) - min(ys) > 0.01:
                return (min(xs), min(ys), max(xs), max(ys)), name
    pos = f.GetPosition()
    bb = None
    for p in f.Pads():
        pb = p.GetBoundingBox()
        bb = pb if bb is None else (bb.Merge(pb) or bb)
    if bb is None:
        return None, None
    # パッドの外接の箱は回転を戻していないので、0/90/180/270 度以外ではやや大きめ
    f2pts = []
    a = math.radians(f.GetOrientationDegrees()); ca, sa = math.cos(a), math.sin(a)
    for x in (bb.GetLeft(), bb.GetRight()):
        for y in (bb.GetTop(), bb.GetBottom()):
            dx, dy = pcbnew.ToMM(x - pos.x), pcbnew.ToMM(y - pos.y)
            lx, ly = dx * ca - dy * sa, dx * sa + dy * ca
            f2pts.append((lx, -ly) if f.IsFlipped() else (lx, ly))
    xs, ys = [p[0] for p in f2pts], [p[1] for p in f2pts]
    return (min(xs), min(ys), max(xs), max(ys)), "pads"


def step_box(path, x0, y0, x1, y1, h, name="placeholder"):
    """直方体の STEP(AP214。mm)。x0..x1, y0..y1 はモデルの座標(y は上向き)、z は 0..h。色は薄い紫(仮の印)"""
    ents = []
    def e(s):
        ents.append(s)
        return f"#{len(ents)}"
    def pt(p): return e(f"CARTESIAN_POINT('',({p[0]:.6f},{p[1]:.6f},{p[2]:.6f}))")
    def dr(d): return e(f"DIRECTION('',({d[0]:.6f},{d[1]:.6f},{d[2]:.6f}))")
    base = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]       # 上から見て反時計回り
    V = {}
    for i, (x, y) in enumerate(base):
        for k, z in ((0, 0.0), (1, h)):
            V[(i, k)] = (e(f"VERTEX_POINT('',{pt((x, y, z))})"), (x, y, z))
    E = {}
    def edge(a, b):
        if (b, a) in E:
            return E[(b, a)], ".F."
        if (a, b) not in E:
            pa, pb = V[a][1], V[b][1]
            d = [pb[j] - pa[j] for j in range(3)]
            L = math.sqrt(sum(c * c for c in d))
            ln = e(f"LINE('',{pt(pa)},{e(f'VECTOR({chr(39)}{chr(39)},{dr([c / L for c in d])},{L:.6f})')})")
            E[(a, b)] = e(f"EDGE_CURVE('',{V[a][0]},{V[b][0]},{ln},.T.)")
        return E[(a, b)], ".T."
    def face(loop, origin, normal, xdir):
        oes = []
        for i in range(len(loop)):
            ec, sense = edge(loop[i], loop[(i + 1) % len(loop)])
            oes.append(e(f"ORIENTED_EDGE('',*,*,{ec},{sense})"))
        el = e(f"EDGE_LOOP('',({','.join(oes)}))")
        fb = e(f"FACE_OUTER_BOUND('',{el},.T.)")
        ax = e(f"AXIS2_PLACEMENT_3D('',{pt(origin)},{dr(normal)},{dr(xdir)})")
        pl = e(f"PLANE('',{ax})")
        return e(f"ADVANCED_FACE('',({fb}),{pl},.T.)")
    n = len(base)
    faces = [face([(i, 1) for i in range(n)], (x0, y0, h), (0, 0, 1), (1, 0, 0)),           # 上(外向き +Z、反時計回り)
             face([(i, 0) for i in reversed(range(n))], (x0, y0, 0), (0, 0, -1), (1, 0, 0))]  # 下
    for i in range(n):
        j = (i + 1) % n
        (xa, ya), (xb, yb) = base[i], base[j]
        L = math.hypot(xb - xa, yb - ya)
        tx, ty = (xb - xa) / L, (yb - ya) / L
        faces.append(face([(i, 0), (j, 0), (j, 1), (i, 1)], (xa, ya, 0), (ty, -tx, 0), (tx, ty, 0)))
    shell = e(f"CLOSED_SHELL('',({','.join(faces)}))")
    solid = e(f"MANIFOLD_SOLID_BREP('{name}',{shell})")
    # 単位と製品の構造
    lu = e("( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) )")
    au = e("( NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.) )")
    su = e("( NAMED_UNIT(*) SI_UNIT($,.STERADIAN.) SOLID_ANGLE_UNIT() )")
    unc = e(f"UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),{lu},'distance_accuracy_value','confusion accuracy')")
    ctx = e(f"( GEOMETRIC_REPRESENTATION_CONTEXT(3) GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT(({unc})) "
            f"GLOBAL_UNIT_ASSIGNED_CONTEXT(({lu},{au},{su})) REPRESENTATION_CONTEXT('Context #1','3D Context with UNIT and UNCERTAINTY') )")
    orig = e(f"AXIS2_PLACEMENT_3D('',{pt((0, 0, 0))},{dr((0, 0, 1))},{dr((1, 0, 0))})")
    rep = e(f"ADVANCED_BREP_SHAPE_REPRESENTATION('',({orig},{solid}),{ctx})")
    actx = e("APPLICATION_CONTEXT('core data for automotive mechanical design processes')")
    e(f"APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2000,{actx})")
    mctx = e(f"PRODUCT_CONTEXT('',{actx},'mechanical')")
    prod = e(f"PRODUCT('{name}','{name}','',({mctx}))")
    pdf = e(f"PRODUCT_DEFINITION_FORMATION('','',{prod})")
    pdc = e(f"PRODUCT_DEFINITION_CONTEXT('part definition',{actx},'design')")
    pd = e(f"PRODUCT_DEFINITION('design','',{pdf},{pdc})")
    pds = e(f"PRODUCT_DEFINITION_SHAPE('','',{pd})")
    e(f"SHAPE_DEFINITION_REPRESENTATION({pds},{rep})")
    col = e("COLOUR_RGB('',0.75,0.55,0.85)")
    fas = e(f"FILL_AREA_STYLE('',(FILL_AREA_STYLE_COLOUR('',{col})))")
    sss = e(f"SURFACE_SIDE_STYLE('',(SURFACE_STYLE_FILL_AREA({fas})))")
    psa = e(f"PRESENTATION_STYLE_ASSIGNMENT((SURFACE_STYLE_USAGE(.BOTH.,{sss})))")
    si = e(f"STYLED_ITEM('color',({psa}),{solid})")
    e(f"MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION('',({si}),{ctx})")
    body = "\n".join(f"#{i + 1}={s};" for i, s in enumerate(ents))
    Path(path).write_text(
        "ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('placeholder box'),'2;1');\n"
        f"FILE_NAME('{name}.step','2026-01-01T00:00:00',(''),(''),'kicad-local k3d','kicad-local k3d','');\n"
        "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\nENDSEC;\nDATA;\n"
        f"{body}\nENDSEC;\nEND-ISO-10303-21;\n")


def show(rows, as_json):
    bad = [r for r in rows if r["status"] in ("missing", "none")]
    if as_json:
        print(json.dumps({"ok": not bad, "missing": [r for r in rows if r["status"] == "missing"],
                          "no_model": [r for r in rows if r["status"] == "none"],
                          "counts": {s: sum(1 for r in rows if r["status"] == s) for s in ("ok", "missing", "none", "hidden", "skip")}},
                         ensure_ascii=False, indent=1))
        return bad
    n_ok = sum(1 for r in rows if r["status"] == "ok")
    if not bad:
        print(f"3D モデル: {n_ok} 個すべて見つかった")
        return bad
    print(f"注意: 3D モデルの無いフットプリントが {len(bad)} 個(見つかったもの {n_ok} 個)。STEP・3D 画像ではこれらの胴体が抜ける"
          "(--placeholder-boxes で外形の箱に置き換えられる)", file=sys.stderr)
    for r in bad:
        what = "ファイルが無い: " + ", ".join(m["file"] for m in r["models"] if m["shown"]) if r["status"] == "missing" else "モデルの指定が無い"
        print(f"  {r['ref']:8s} {r['footprint']}  {what}", file=sys.stderr)
    return bad


def main():
    ap = argparse.ArgumentParser(description="3D モデルの点検と代わりの箱")
    sp = ap.add_subparsers(dest="cmd", required=True)
    c = sp.add_parser("check"); c.add_argument("pcb"); c.add_argument("--json", action="store_true")
    x = sp.add_parser("boxes"); x.add_argument("pcb"); x.add_argument("out")
    x.add_argument("--height", type=float, default=5.0, help="欄が無い部品の箱の高さ mm(既定 5)")
    x.add_argument("--field", default="Height", help="高さを読む部品の欄の名前(既定 Height。値は mm、単位の mm は付けてもよい)")
    x.add_argument("--json", action="store_true")
    a = ap.parse_args()
    b = pcbnew.LoadBoard(a.pcb)
    bdir = Path(a.pcb).resolve().parent
    rows = scan(b, bdir)
    if a.cmd == "check":
        return 1 if show(rows, a.json) else 0
    out = Path(a.out).resolve()
    mdir = out.parent / (out.stem + ".boxes")
    mdir.mkdir(parents=True, exist_ok=True)
    fps = {f.GetReference(): f for f in b.GetFootprints()}
    # 写しは別の場所に書くので、基板からの相対・${KIPRJMOD} の指定は見つかった場所(絶対)に書き換える
    for r in rows:
        f = fps[r["ref"]]
        if any(m["found"] and ("KIPRJMOD" in m["file"] or not re.match(r"[\\/$~]|[A-Za-z]:", m["file"])) for m in r["models"]):
            ms = [pcbnew.FP_3DMODEL(m) for m in f.Models()]
            f.Models().clear()
            for m, info in zip(ms, r["models"]):
                if info["found"]:
                    m.m_Filename = info["found"]
                f.Models().push_back(m)
    made = []
    for r in rows:
        if r["status"] not in ("missing", "none"):
            continue
        f = fps[r["ref"]]
        box, src = outline(f)
        if not box:
            continue
        hs = field(f, a.field)
        try:
            h = float(re.sub(r"\s*mm\s*$", "", hs)) if hs else a.height
        except ValueError:
            h = a.height
        x0, y0, x1, y1 = box
        p = mdir / (re.sub(r"[^\w.-]", "_", r["ref"]) + ".step")
        step_box(p, x0, -y1, x1, -y0, h, re.sub(r"[^\w.-]", "_", r["ref"]))
        # 見つからない指定は消して(STEP の書き出しの「見つからない」を繰り返さない)、箱だけにする
        f.Models().clear()
        m = pcbnew.FP_3DMODEL()
        m.m_Filename = str(p)
        f.Models().push_back(m)
        made.append({"ref": r["ref"], "from": src, "height": h, "size": [round(x1 - x0, 3), round(y1 - y0, 3)], "file": str(p)})
    pcbnew.SaveBoard(str(out), b)
    if a.json:
        print(json.dumps({"boxes": made, "out": str(out)}, ensure_ascii=False, indent=1))
    else:
        for m in made:
            print(f"  箱 {m['ref']:8s} {m['size'][0]}×{m['size'][1]}×{m['height']} mm({m['from']} の外形)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
