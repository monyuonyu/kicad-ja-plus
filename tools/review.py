#!/usr/bin/env python3
"""
kicad-local review: 新しい版を 1 回で確認して、報告を 1 枚にまとめる(ローカル道具、2026-09-25)

  kicad-local review <新しい版> [前の版] [--sim 手順.toml ...] [--runs N] [--out 出力フォルダ]

  版の指定: フォルダ(中の .kicad_pro を探す)か、.kicad_pro / .kicad_sch / .kicad_pcb のどれか
  やること: ERC、DRC(回路図との不一致つき)、3D 画像、
            前の版があれば 部品表の差分・接続の差分・製造データの差分(層ごとの画像)・3D の差分、
            --sim の手順ごとに回路シミュレーション(新しい版の回路図で。部品の定格チェックつき)
  出力: <出力フォルダ>/報告.md と、その下に各結果
"""
import argparse, collections, datetime, json, re, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "ksim"))
import ksim  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
import klint  # noqa: E402

NOISE = ("lib_footprint_mismatch", "silk_edge_clearance", "silk_overlap", "silk_over_copper", "lib_symbol_issues")


def find_design(p):
    """フォルダかファイル → (回路図, 基板)"""
    p = Path(p).resolve()
    if p.is_dir():
        pros = [x for x in p.glob("*.kicad_pro")]
        if len(pros) != 1:
            pcbs = list(p.glob("*.kicad_pcb"))
            if len(pcbs) != 1:
                sys.exit(f"{p} に設計が 1 つに決まらない(.kicad_pro {len(pros)} 個、.kicad_pcb {len(pcbs)} 個)。ファイルで指定して")
            p = pcbs[0]
        else:
            p = pros[0]
    stem = p.with_suffix("")
    sch, pcb = stem.with_suffix(".kicad_sch"), stem.with_suffix(".kicad_pcb")
    return (sch if sch.exists() else None), (pcb if pcb.exists() else None)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def loc(v):
    p = v.get("pos") or (v.get("items") or [{}])[0].get("pos")
    return f"({p['x']:.2f}, {p['y']:.2f})" if p else ""


def check_list(title, arr):
    """違反の一覧 → (件数のまとめ, 詳細の行)"""
    main = [v for v in arr if v["type"] not in NOISE]
    err = sum(1 for v in main if v.get("severity") == "error")
    summary = f"{len(main)} 件" + (f"(エラー {err})" if err else "") + (f"、参考 {len(arr) - len(main)} 件" if len(arr) != len(main) else "")
    lines = []
    for t, n in collections.Counter(v["type"] for v in arr).most_common():
        lines.append(f"- {'**' + t + '**' if t not in NOISE else t}: {n}" + ("(参考)" if t in NOISE else ""))
        if t not in NOISE:
            for v in [x for x in arr if x["type"] == t][:10]:
                items = " ⇔ ".join(i.get("description", "")[:60] for i in v.get("items", [])[:2])
                lines.append(f"  - {loc(v)} {v.get('layer', '')} {items}")
    return (summary if arr else "なし"), lines, len(main)


def erc(sch, t):
    out = t / "erc.json"
    run(["kicad-cli-local", "sch", "erc", "--format", "json", "-o", str(out), str(sch)])
    if not out.exists():
        return "実行できない", [], 1
    d = json.loads(out.read_text())
    return check_list("ERC", [v for s in d.get("sheets", []) for v in s.get("violations", [])])


def drc(pcb, t):
    out = t / "drc.json"
    run(["kicad-cli-local", "pcb", "drc", "--schematic-parity", "--format", "json", "-o", str(out), str(pcb)])
    if not out.exists():
        return None
    d = json.loads(out.read_text())
    return {k: check_list(k, d.get(k, [])) for k in ("violations", "unconnected_items", "schematic_parity")}


# ---------------------------------------------------------------- 部品表・接続の差分

def netlist(sch):
    return ksim.parse_netlist(ksim.export_netlist(sch))


def refkey(r):
    return (re.sub(r'\d', '', r), int(re.sub(r'\D', '', r) or 0))


def refs_text(refs):
    """['R1','R2','R3','R5'] → 'R1〜R3, R5'"""
    refs = sorted(refs, key=refkey)
    out, i = [], 0
    while i < len(refs):
        j = i
        while (j + 1 < len(refs) and refkey(refs[j + 1])[0] == refkey(refs[i])[0]
               and refkey(refs[j + 1])[1] == refkey(refs[j])[1] + 1):
            j += 1
        out.append(refs[i] if j - i < 2 else f"{refs[i]}〜{refs[j]}" if j > i else refs[i])
        if j - i == 1:
            out.append(refs[j])
        i = j + 1
    return ", ".join(out)


def norm(v):
    return v.replace("\u03bc", "\u00b5")   # ギリシャ文字の μ とマイクロ記号の µ は同じとみなす


def bom_diff(a, b):
    """部品表の差。同じ変更をした部品はまとめる → (行, 変わった部品の数)"""
    ca, cb = a[0], b[0]
    groups = collections.OrderedDict()
    for r in sorted(set(ca) | set(cb), key=refkey):
        if r not in cb:
            key = f"削除 {ca[r]['value']}"
        elif r not in ca:
            key = f"追加 {cb[r]['value']}" + (f"({cb[r]['fields']['MPN']})" if cb[r]['fields'].get('MPN') else "")
        else:
            ch = []
            if norm(ca[r]["value"]) != norm(cb[r]["value"]):
                ch.append(f"値 {ca[r]['value']} → {cb[r]['value']}")
            fa, fb = ca[r].get("footprint", ""), cb[r].get("footprint", "")
            if fa != fb:
                ch.append(f"形状 {fa.split(':')[-1]} → {fb.split(':')[-1]}")
            for k in sorted(set(ca[r]["fields"]) | set(cb[r]["fields"])):
                if k in ("Footprint", "Datasheet", "Description"):
                    continue
                x, y = ca[r]["fields"].get(k, ""), cb[r]["fields"].get(k, "")
                if norm(x) != norm(y):
                    ch.append(f"{k} 「{x}」→「{y}」")
            if not ch:
                continue
            key = "変更 " + "、".join(ch)
        groups.setdefault(key, []).append(r)
    rows = [f"- **{refs_text(refs)}**: {key}" for key, refs in groups.items()]
    return rows, sum(len(v) for v in groups.values())


def net_diff(a, b):
    """接続(どの端子どうしがつながっているか)の差。ネット名が変わっただけのものは差にしない"""
    def groups(nl):
        g = {}
        for name, nodes in nl[1].items():
            pins = frozenset(f"{r}.{p}" for r, p, _ in nodes)
            if len(pins) > 1 or not name.startswith("unconnected"):
                g[pins] = name
        return g
    ga, gb = groups(a), groups(b)
    same = set(ga) & set(gb)
    old = [o for o in ga if o not in same]
    new = [n for n in gb if n not in same]
    # 前後のネットを 1 対 1 に対応づける(共通の端子が多い組から)
    pairs = sorted(((len(o & n), gb[n], ga[o], n, o) for n in new for o in old if o & n), reverse=True)
    match, used = {}, set()
    for _, _, _, n, o in pairs:
        if n not in match and o not in used:
            match[n] = o
            used.add(o)
    rows = []
    for n in sorted(new, key=lambda n: gb[n]):
        o = match.get(n)
        if o is None:
            rows.append(f"- 新しいネット **{gb[n]}**: {' '.join(sorted(n))}")
            continue
        add, rem = sorted(n - o), sorted(o - n)
        txt = f"- **{gb[n]}**" + (f"(前は {ga[o]})" if ga[o] != gb[n] else "") + ": "
        txt += "、".join(([f"つながった {' '.join(add)}"] if add else []) + ([f"外れた {' '.join(rem)}"] if rem else []))
        rows.append(txt)
    for o in old:
        if o not in used:
            rows.append(f"- 無くなったネット **{ga[o]}**: {' '.join(sorted(o))}")
    return rows


# ---------------------------------------------------------------- 本体

def main():
    ap = argparse.ArgumentParser(description="新しい版を 1 回で確認して報告を 1 枚にまとめる")
    ap.add_argument("new")
    ap.add_argument("old", nargs="?")
    ap.add_argument("--sim", nargs="*", default=[], help="回路シミュレーションの手順(TOML)。新しい版の回路図で回す")
    ap.add_argument("--runs", type=int, help="シミュレーションのばらつきの回数(手順の値を上書き)")
    ap.add_argument("--out")
    a = ap.parse_args()

    sch, pcb = find_design(a.new)
    osch, opcb = find_design(a.old) if a.old else (None, None)
    name = (sch or pcb).stem
    out = Path(a.out or f"review_{name}_{datetime.datetime.now():%Y%m%d_%H%M}").resolve()
    out.mkdir(parents=True, exist_ok=True)
    t = Path(tempfile.mkdtemp())
    summary, body = [], []

    def step(msg):
        print(f"… {msg}", flush=True)

    if sch:
        step("ERC")
        s, lines, n = erc(sch, t)
        summary.append(("ERC", s, "要確認" if n else "OK"))
        body += ["## ERC", ""] + (lines or ["なし"]) + [""]
    if sch:
        step("回路図の点検")
        items, _ = klint.lint(sch)
        key = lambda t: (t[0], t[2])   # ネット名の変わっただけのものは同じ指摘とみなす(中身=端子と文で比べる)
        old_items = klint.lint(osch)[0] if osch else None
        summary.append(("回路図の点検", f"要確認 {len(items)} 件" + (
            f"(前の版から 解消 {len(set(map(key, old_items)) - set(map(key, items)))}・新規 {len(set(map(key, items)) - set(map(key, old_items)))})"
            if old_items is not None else ""), "要確認" if items else "OK"))
        body += ["## 回路図の点検(設計の定石からの外れ。kicad-local lint)", ""]
        for cat, where, msg in items:
            new = old_items is not None and (cat, msg) not in {key(t) for t in old_items}
            body.append(f"- [{cat}] **{where}**: {msg}" + (" **(新規)**" if new else ""))
        if old_items:
            gone = [t for t in old_items if key(t) not in {key(u) for u in items}]
            if gone:
                body += ["", "前の版から解消したもの:"] + [f"- ~~[{c}] {w}: {m}~~" for c, w, m in gone]
        body += [""] if items or old_items else ["なし", ""]
    if pcb:
        step("DRC")
        d = drc(pcb, t)
        if d:
            for key, title in (("violations", "DRC"), ("unconnected_items", "未配線"), ("schematic_parity", "回路図との不一致")):
                s, lines, n = d[key]
                summary.append((title, s, "要確認" if n else "OK"))
                body += [f"## {title}", ""] + (lines or ["なし"]) + [""]
        ds = re.search(r"\(drillshape (\d+)\)", pcb.read_text(encoding="utf-8"))
        if ds and ds.group(1) != "0":
            summary.append(("出力設定", f"穴の印(drillshape {ds.group(1)})が有効", "要確認"))
            body += ["## 出力設定", "", f"- 穴の印(drillshape {ds.group(1)})が有効。ガーバーの全層に穴の印が入る。メーカー向けは「なし」が基本。", ""]
        step("3D 画像")
        run(["kicad-local", "render", str(pcb), "iso", str(out / "3D_斜め.png")])
        body += ["## 3D", "", "![](3D_斜め.png)", ""]

    if a.old:
        body += [f"## 前の版との比較", "", f"前の版: `{osch or opcb}`", ""]
        if sch and osch:
            step("部品表・接続の差分")
            na, nb = netlist(osch), netlist(sch)
            rows, n = bom_diff(na, nb)
            summary.append(("部品の変更", f"{n} 個" if rows else "なし", "—"))
            body += ["### 部品表の差分", ""] + (rows or ["なし"]) + [""]
            rows = net_diff(na, nb)
            summary.append(("接続の変更", f"{len(rows)} ネット" if rows else "なし", "—"))
            body += ["### 接続の差分(端子のつながりで比較。ネット名だけの変更は含まない)", ""] + (rows or ["なし"]) + [""]
        if pcb and opcb:
            step("製造データの差分")
            r = run(["kicad-local", "fabdiff", str(opcb), str(pcb), str(out / "製造データ比較")])
            md = out / "製造データ比較" / "差分まとめ.md"
            diff_layers = []
            if md.exists():
                txt = md.read_text(encoding="utf-8")
                diff_layers = re.findall(r"^## (.+?)\s*$", txt, re.M)
            lines = [l for l in r.stdout.splitlines() if l.strip()]
            files = re.findall(r"^(\S+\.(?:gbr|drl|gtl|gbl))\s*$", r.stdout, re.M)
            changed = re.findall(r"^(\S+\.(?:gbr|drl|gtl|gbl))\s*\n.*\n\s+差のある場所", r.stdout, re.M)
            short = [f.replace("board-", "").rsplit(".", 1)[0] for f in changed]
            summary.append(("製造データの差", f"{len(changed)}/{len(files)} ファイル" + (f"({', '.join(short)})" if short else ""), "—"))
            body += ["### 製造データの差(層ごと)", "", "詳細と画像: `製造データ比較/差分まとめ.md`", ""] + [f"    {l}" for l in lines] + [""]
            step("3D の差分")
            for side in ("top", "bottom"):
                png = f"3D差分_{'上面' if side == 'top' else '下面'}.png"
                r = run(["kicad-local", "renderdiff", str(opcb), str(pcb), side, str(out / png)])
                body += [f"{'上面' if side == 'top' else '下面'}: " + " ".join(l for l in r.stdout.splitlines() if "画素" in l), "", f"![]({png})", ""]

    if a.sim:
        body += ["## 回路シミュレーション(新しい版の回路図で)", ""]
        for spec in a.sim:
            spec = Path(spec).resolve()
            step(f"シミュレーション {spec.stem}")
            so = out / f"シミュレーション_{spec.stem}"
            cmd = ["kicad-local", "sim", "run", str(spec), "--out", str(so)] + (["--sch", str(sch)] if sch else [])
            if a.runs is not None:
                cmd += ["--runs", str(a.runs)]
            r = run(cmd)
            lines = [l for l in r.stdout.splitlines() if l.strip()]
            verdict = next((l for l in lines if l.startswith("判定")), (r.stderr.strip().splitlines() or ["失敗"])[-1])
            stress = next((l.strip() for l in lines if l.strip().startswith("部品の定格:")), "")
            summary.append((f"シミュ {spec.stem}", verdict.split("→")[0].replace("判定:", "").strip() + (f"。{stress}" if stress else ""),
                            "要確認" if "判定: OK" not in verdict else "注意" if re.search(r"注意 [1-9]", stress) else "OK"))
            body += [f"### {spec.stem}", "", f"詳細: `{so.name}/結果.md`", ""] + [f"    {l}" for l in lines[:-1]] + [""]

    md = [f"# 基板の確認: {name}", "",
          f"- 新しい版: `{sch or pcb}`" + (f"(回路図 {datetime.datetime.fromtimestamp(sch.stat().st_mtime):%Y-%m-%d %H:%M} 保存)" if sch else ""),
          f"- 前の版: `{osch or opcb}`" if a.old else "- 前の版: なし",
          f"- 実行: {datetime.datetime.now():%Y-%m-%d %H:%M}(kicad-local review)", "",
          "## まとめ", "", "| 項目 | 結果 | 判定 |", "|---|---|---|"]
    md += [f"| {k} | {v} | {'**' + j + '**' if j in ('要確認', '注意') else j} |" for k, v, j in summary]
    md += [""] + body
    (out / "報告.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print()
    for k, v, j in summary:
        print(f"  {j:4s} {k}: {v}")
    print(f"→ {out / '報告.md'}")
    return 1 if any(j == "要確認" for _, _, j in summary) else 0


if __name__ == "__main__":
    sys.exit(main())
