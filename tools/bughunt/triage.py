#!/usr/bin/env python3
"""run.py / run_qa.py の結果（1 つ以上の出力フォルダ）をまとめ、同じ不具合を 1 件に寄せて Markdown にする。

使い方: triage.py [--corpus-list corpus.txt] [--src-marker /kicad-10.0.6/] -o report.md 出力フォルダ...
寄せ方: サニタイザの報告は（種類, KiCad の中の上から 3 つのフレーム）。UBSan は報告の場所も含める。
       報告の無いシグナル・時間切れは（コマンド, シグナル）。
再現用には、その件に当たったファイルのうち一番小さい物を 1 つ。
"""
import argparse, collections, json, os, re

ASAN_HDR = re.compile(r"ERROR: (AddressSanitizer|LeakSanitizer|UndefinedBehaviorSanitizer): ([\w-]+)(.*)")
UBSAN_HDR = re.compile(r"^(\S+?):(\d+):(\d+): runtime error: (.*)$")
FRAME = re.compile(r"^\s*#(\d+) 0x[0-9a-f]+ in (.+?) (\S+?)(?::(\d+))?(?::\d+)?$")
FRAME_NOFILE = re.compile(r"^\s*#(\d+) 0x[0-9a-f]+ +\((\S+)\+0x[0-9a-f]+\)")


def short_func(f):
    # 引数とテンプレートの中身を落とす（行番号で区別できる）
    f = re.sub(r"\(.*\)( const)?$", "", f)
    depth, out = 0, []
    for ch in f:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def norm_msg(m):
    m = re.sub(r"0x[0-9a-f]+", "ADDR", m)
    m = re.sub(r"-?\d+(\.\d+)?(e[+-]?\d+)?", "N", m)
    return m[:160]


def parse_reports(text, marker):
    """ログの中の報告を順に返す: dict(type, loc, frames, all_frames)"""
    lines = text.splitlines()
    reps = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = ASAN_HDR.search(ln)
        u = UBSAN_HDR.match(ln.strip())
        if m or u:
            if m:
                typ = f"{m.group(1).replace('Sanitizer', 'San')}: {m.group(2)}"
                loc = ""
            else:
                path, line_no, msg = u.group(1), u.group(2), u.group(4)
                typ = "UBSan: " + norm_msg(msg)
                loc = f"{os.path.basename(path)}:{line_no}"
            frames, j = [], i + 1
            # 報告の直後に来るスタック（最初の空行でなく、フレームが途切れるまで。ASan は数行あとに来る）
            started = False
            while j < len(lines) and j < i + 200:
                fm = FRAME.match(lines[j])
                if fm:
                    started = True
                    frames.append((short_func(fm.group(2)), fm.group(3), fm.group(4) or ""))
                elif FRAME_NOFILE.match(lines[j]):
                    started = True
                    frames.append(("?", FRAME_NOFILE.match(lines[j]).group(2), ""))
                elif started:
                    break
                elif UBSAN_HDR.match(lines[j].strip()) or ASAN_HDR.search(lines[j]):
                    break
                j += 1
            own = [f for f in frames if marker in f[1] and "/usr/include" not in f[1]]
            reps.append(dict(type=typ, loc=loc, frames=own, all_frames=frames))
            i = j if j > i else i + 1
            continue
        i += 1
    return reps


def fmt_frame(f):
    func, path, line = f
    p = path.split("/kicad-10.0.6/")[-1] if "/kicad-10.0.6/" in path else os.path.basename(path)
    return f"{func} ({p}:{line})" if line else f"{func} ({p})"


def repo_link(path, repos):
    """材料のパスを GitHub の固定リンクに（corpus/owner__repo/... の形のとき）"""
    m = re.search(r"/([^/]+?__[^/]+?)/(.*)$", path)
    if m and m.group(1) in repos:
        url, sha = repos[m.group(1)]
        return f"{url}/blob/{sha}/{m.group(2)}"
    if "/kicad-10.0.6/" in path:
        return "kicad:" + path.split("/kicad-10.0.6/")[-1]
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--json")
    ap.add_argument("--corpus-list")
    ap.add_argument("--src-marker", default="/kicad-10.0.6/")
    a = ap.parse_args()

    repos = {}
    if a.corpus_list and os.path.exists(a.corpus_list):
        for ln in open(a.corpus_list):
            p = ln.split()
            if len(p) == 3 and not ln.startswith("#"):
                repos[p[1].replace("https://github.com/", "").replace("/", "__")] = (p[1], p[2])

    runs, summaries = [], []
    for d in a.dirs:
        for root, _, files in os.walk(d):
            if "runs.jsonl" in files:
                for ln in open(os.path.join(root, "runs.jsonl")):
                    r = json.loads(ln)
                    r["_dir"] = root
                    runs.append(r)
            if "summary.json" in files:
                summaries.append(json.load(open(os.path.join(root, "summary.json"))))

    issues = collections.OrderedDict()

    def add(key, title, r, detail):
        it = issues.setdefault(key, dict(title=title, count=0, files=set(), cmds=collections.Counter(), best=None, detail=detail, kinds=set()))
        it["count"] += 1
        it["files"].add(r["file"])
        it["cmds"][r["cmd"]] += 1
        it["kinds"].add(r.get("kind", ""))
        if it["best"] is None or (r.get("size", 1 << 60) or 1 << 60) < (it["best"].get("size", 1 << 60) or 1 << 60):
            it["best"] = r

    for r in runs:
        text = ""
        if r.get("log"):
            try:
                text = open(os.path.join(r["_dir"], "logs", r["log"]), errors="replace").read()
            except OSError:
                pass
        reps = parse_reports(text, a.src_marker) if r.get("san") else []
        seen = set()
        for rep in reps:
            top = rep["frames"][:3]
            key = ("san", rep["type"], rep["loc"], tuple((f[0], os.path.basename(f[1]), f[2]) for f in top))
            if key in seen:
                continue
            seen.add(key)
            detail = [fmt_frame(f) for f in rep["frames"][:8]] or [fmt_frame(f) for f in rep["all_frames"][:6]]
            add(key, rep["type"] + (f" at {rep['loc']}" if rep["loc"] else ""), r, detail)
        if r.get("silent"):
            # 静かな食い違い: （確かめの名前, 食い違いの種類, ファイルの種類, 形式の版の年）で寄せる
            dl = os.path.join(r["_dir"], "logs", r["log"]) if r.get("log") else ""
            for k in r["silent"][:3]:
                ver = (r.get("ver") or "")[:5]
                add(("silent", r["cmd"], k, r.get("kind"), ver), f"{r['cmd'][4:]}: {k}（{r.get('kind')} {ver}）", r,
                    [os.path.relpath(dl, os.getcwd())] if dl else [])
            continue
        if r.get("timeout"):
            add(("timeout", r["cmd"]), f"時間切れ（{r['cmd']}）", r, [])
        elif (r.get("sig") or r["rc"] < 0) and not any(rep["type"].startswith("ASan") for rep in reps):
            add(("signal", r["cmd"], r.get("sig")), f"シグナル {r.get('sig')}、報告なし（{r['cmd']}）", r, [text[-600:]] if text else [])
        elif r.get("kind") == "qa" and r["rc"] != 0 and not reps:
            add(("qafail", r["cmd"]), f"単体試験の失敗 rc={r['rc']}（{r['cmd']}）", r, r.get("failed") or [text[-600:]])

    by_cmd = collections.Counter(r["cmd"] for r in runs)
    files = {r["file"] for r in runs}
    out = []
    out.append("# KiCad バグ探しの結果\n")
    out.append(f"- コマンドの実行: {len(runs)} 回、ファイル {len(files)} 個（重複なし）")
    if summaries:
        skipped = sum(s.get("items_skipped_budget", 0) for s in summaries)
        tot = summaries[0].get("kinds_total")
        out.append(f"- 材料の総数（重複なし）: {summaries[0].get('items_total')} {tot}、時間切れで回らなかった物 {skipped}")
    out.append(f"- コマンド別: " + ", ".join(f"{k} {v}" for k, v in by_cmd.most_common()))
    nsan = sum(1 for k in issues if k[0] == "san")
    out.append(f"- 別々の問題: サニタイザ {nsan} 件、時間切れ {sum(1 for k in issues if k[0]=='timeout')} 種、"
               f"報告なしのシグナル {sum(1 for k in issues if k[0]=='signal')} 種、"
               f"単体試験の失敗 {sum(1 for k in issues if k[0]=='qafail')} 組、"
               f"静かな食い違い {sum(1 for k in issues if k[0]=='silent')} 種\n")
    chk = collections.Counter((r["cmd"], bool(r.get("silent"))) for r in runs if r["cmd"].startswith("chk:"))
    out.append("- 確かめ（一致/食い違い）: " + ", ".join(f"{c[4:]} {chk[(c, False)]}/{chk[(c, True)]}" for c in sorted({c for c, _ in chk})) + "\n")

    order = {"san": 0, "signal": 1, "timeout": 2, "qafail": 3, "silent": 4}
    rows = sorted(issues.items(), key=lambda kv: (order.get(kv[0][0], 9), -len(kv[1]["files"])))
    out.append("| # | 種類 | 場所（KiCad の中の一番上） | 回数 | ファイル数 | コマンド | 再現用（一番小さい物） |")
    out.append("|---|---|---|---|---|---|---|")
    for n, (k, it) in enumerate(rows, 1):
        top = it["detail"][0] if (it["detail"] and k[0] == "san") else ""
        cmds = ", ".join(f"{c}×{v}" for c, v in it["cmds"].most_common(4))
        b = it["best"]
        out.append(f"| {n} | {it['title']} | {top} | {it['count']} | {len(it['files'])} | {cmds} | {repo_link(b['file'], repos)} ({b.get('size', '?')} B, {b['cmd']}) |")
    out.append("\n## 詳細（KiCad の中のフレーム、上から）\n")
    for n, (k, it) in enumerate(rows, 1):
        if k[0] == "timeout":
            secs = it["best"].get("secs")
            out.append(f"### {n}. {it['title']}\n- 例: {repo_link(it['best']['file'], repos)}（{secs}s）\n")
            continue
        b = it["best"]
        out.append(f"### {n}. {it['title']}\n- 再現: `{b['cmd']}` on {repo_link(b['file'], repos)}  ログ: {os.path.relpath(os.path.join(b['_dir'], 'logs', b['log'] or ''), os.getcwd())}")
        out.append("```\n" + "\n".join(it["detail"]) + "\n```\n")
    with open(a.out, "w") as f:
        f.write("\n".join(out) + "\n")
    if a.json:
        with open(a.json, "w") as f:
            json.dump([dict(key=list(map(str, k)), title=it["title"], count=it["count"], nfiles=len(it["files"]),
                            cmds=dict(it["cmds"]), repro=it["best"]["file"], repro_link=repo_link(it["best"]["file"], repos),
                            repro_cmd=it["best"]["cmd"], log=it["best"].get("log"), frames=it["detail"])
                       for k, it in rows], f, ensure_ascii=False, indent=1)
    print(f"runs={len(runs)} files={len(files)} issues={len(issues)} (san={nsan})")


if __name__ == "__main__":
    main()
