#!/usr/bin/env python3
"""ASan/UBSan つきの kicad-cli に材料を食わせて、落ちた・止まった・サニタイザが何か言った物だけを残す。

使い方:
  run.py --cli build/kicad/kicad-cli [--gerber build/gerbview/kicad_gerber] \
         --out out/ --shard 0/8 --jobs 4 --timeout 180 --budget-min 300 材料の場所...

出力（out/）:
  runs.jsonl   1 コマンド 1 行（file, kind, cmd, rc, sig, timeout, secs, san）
  logs/*.log   サニタイザの報告・シグナル・時間切れがあった回の出力（先頭 256KB）
  summary.json 件数のまとめ
ファイルは中身のハッシュで重複を除き、ハッシュでシャードに分ける（同じ材料なら毎回同じ割り振り）。
"""
import argparse, hashlib, json, os, random, re, shutil, signal, subprocess, sys, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import silent  # noqa: E402

SAN_RE = re.compile(rb"ERROR: AddressSanitizer|runtime error:|ERROR: LeakSanitizer|AddressSanitizer:DEADLYSIGNAL|"
                    rb"ERROR: UndefinedBehaviorSanitizer")
KEEP_BYTES = 256 * 1024

PCB_EXT = {".kicad_pcb"}
FOREIGN_PCB_EXT = {".pcbdoc", ".cspcbdoc", ".cmpcbdoc", ".swpcbdoc", ".cpa"}
GERBER_EXT = {".gbr", ".gtl", ".gbl", ".gko", ".gm1", ".drl", ".xln", ".gbo", ".gto", ".gbs", ".gts"}


def head(path, n=4096):
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except OSError:
        return b""


def classify(path):
    """ファイル 1 つを、どのコマンドの組で試すかに振り分ける（None は使わない）"""
    low = path.lower()
    ext = os.path.splitext(low)[1]
    if ext in PCB_EXT:
        return "pcb"
    if ext == ".kicad_sch":
        return "sch"
    if ext == ".kicad_sym":
        return "symlib"
    if ext in FOREIGN_PCB_EXT:
        return "import"
    if ext in GERBER_EXT:
        return "gerber"
    h = head(path)
    if ext == ".brd":
        if b"PCBNEW-BOARD" in h[:200]:
            return "pcb"          # KiCad 旧形式の基板
        return "import"           # Eagle（XML）・Allegro などは取り込みから
    if ext == ".sch":
        if h.startswith(b"EESchema Schematic"):
            return "sch"          # KiCad 旧形式の回路図
        return None               # Eagle の回路図は kicad-cli では読めない
    if ext == ".lib" and h.startswith(b"EESchema-LIBRARY"):
        return "symlib"
    if ext == ".schlib":
        return "symlib"
    if ext == ".lbr":
        return "lbr"              # Eagle のライブラリ: シンボルとフットプリントの両方
    if ext == ".mod" and h.startswith(b"PCBNEW-LibModule"):
        return "fplib"
    if ext == ".pcblib":
        return "fplib"
    if ext == ".pcb" and (b"ACCEL_ASCII" in h[:100] or b"PCAD" in h[:200]):
        return "import"
    if ext == ".asc" and h.startswith(b"!PADS-"):
        return "import"
    if ext == ".json" and b'"docType"' in h and (b"easyeda" in h.lower() or b'"head"' in h):
        return "import"
    if ext == ".txt" and h.startswith(b"A!"):
        return "import"           # Fabmaster
    return None


def file_hash(path):
    h = hashlib.sha1()
    if os.path.isdir(path):
        for root, dirs, files in os.walk(path):
            dirs.sort()
            for f in sorted(files):
                h.update(f.encode())
                h.update(file_hash(os.path.join(root, f)).encode())
        return h.hexdigest()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def enumerate_items(roots):
    items = {}
    for root in roots:
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d != ".git"]
            for d in list(dirs):
                if d.lower().endswith(".pretty"):
                    p = os.path.join(dirpath, d)
                    if any(f.endswith(".kicad_mod") for f in os.listdir(p)):
                        items.setdefault(file_hash(p), (p, "fplib"))
            for f in files:
                p = os.path.join(dirpath, f)
                if not os.path.isfile(p) or os.path.getsize(p) == 0:
                    continue
                k = classify(p)
                if k:
                    items.setdefault(file_hash(p), (p, k))
    return items


class Runner:
    def __init__(self, a):
        self.a = a
        self.lock = threading.Lock()
        self.out = open(os.path.join(a.out, "runs.jsonl"), "a")
        self.logs = os.path.join(a.out, "logs")
        os.makedirs(self.logs, exist_ok=True)
        self.n = 0
        self.deadline = time.time() + a.budget_min * 60
        self.skipped = 0

    def exec_one(self, tool, args, cwd, key):
        exe = self.a.cli if tool == "cli" else self.a.gerber
        logf = tempfile.NamedTemporaryFile(prefix="bh", suffix=".log", delete=False, dir=self.a.tmp)
        t0 = time.time()
        p = subprocess.Popen([exe] + args, stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             cwd=cwd, start_new_session=True)
        to = False
        try:
            rc = p.wait(timeout=self.a.timeout)
        except subprocess.TimeoutExpired:
            to = True
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            rc = p.wait()
        secs = round(time.time() - t0, 1)
        logf.close()
        with open(logf.name, "rb") as f:
            data = f.read()
        os.unlink(logf.name)
        san = bool(SAN_RE.search(data))
        sig = -rc if rc < 0 else (rc - 128 if 128 < rc < 160 else 0)
        log = None
        if san or to or sig:
            log = f"{key}.log"
            with open(os.path.join(self.logs, log), "wb") as f:
                f.write(f"# {exe} {' '.join(args)}\n# rc={rc} timeout={to} secs={secs}\n".encode())
                f.write(data[:KEEP_BYTES])
                if len(data) > KEEP_BYTES:
                    # 長いときは末尾も（最後の報告が欠けないように）
                    f.write(b"\n# ...cut...\n" + data[-KEEP_BYTES // 2:])
        return rc, sig, to, secs, san, log

    # ---- 1 回のコマンド ----
    def cmd(self, ctx, name, args, tool="cli", cwd=None):
        if tool == "gerber" and not self.a.gerber:
            return None
        rc, sig, to, secs, san, log = self.exec_one(tool, args, cwd or ctx["cwd"], f"{ctx['h'][:12]}-{name}")
        rec = dict(file=ctx["path"], size=ctx["size"], hash=ctx["h"], kind=ctx["kind"], ver=ctx["ver"], cmd=name,
                   rc=rc, sig=sig, timeout=to, secs=secs, san=san, log=log)
        self.write(rec)
        return rc

    def write(self, rec):
        with self.lock:
            self.out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self.out.flush()
            self.n += 1

    def silent(self, ctx, check, diffs):
        """静かな食い違いを記録する（diffs = [(種類, 抜粋)]。空なら一致として 1 行だけ）"""
        log = None
        if diffs:
            log = f"{ctx['h'][:12]}-{check}.diff"
            with open(os.path.join(self.logs, log), "w") as f:
                for k, ex in diffs[:20]:
                    f.write(f"## {k}\n{ex}\n\n")
        self.write(dict(file=ctx["path"], size=ctx["size"], hash=ctx["h"], kind=ctx["kind"], ver=ctx["ver"],
                        cmd="chk:" + check, rc=0, sig=0, timeout=False, secs=0, san=False, log=log,
                        silent=[k for k, _ in diffs[:20]]))

    def item(self, h, path, kind):
        if time.time() > self.deadline:
            with self.lock:
                self.skipped += 1
            return
        w = tempfile.mkdtemp(prefix="w", dir=self.a.tmp)
        ctx = dict(h=h, path=path, kind=kind, cwd=os.path.dirname(os.path.abspath(path)), w=w,
                   size=os.path.getsize(path) if os.path.isfile(path) else 0, ver=format_version(path))
        try:
            getattr(self, "do_" + kind)(ctx)
        except Exception as e:  # 道具の側の失敗は記録して次へ
            self.write(dict(file=path, hash=h, kind=kind, cmd="harness-error", rc=0, sig=0, timeout=False, secs=0,
                            san=False, log=None, error=repr(e)[:300]))
        finally:
            shutil.rmtree(w, ignore_errors=True)

    # ---- 基板 ----
    def board_outputs(self, ctx, board, tag, cwd):
        """比べるための出力（ガーバー・ドリル・部品表・ネット・DRC）。置き場所を返す"""
        o = f"{ctx['w']}/{tag}"
        os.makedirs(o, exist_ok=True)
        self.cmd(ctx, "gerbers", ["pcb", "export", "gerbers", "-o", f"{o}/g/", board], cwd=cwd)
        self.cmd(ctx, "drill", ["pcb", "export", "drill", "-o", f"{o}/d/", board], cwd=cwd)
        self.cmd(ctx, "pos", ["pcb", "export", "pos", "--format", "csv", "-o", f"{o}/pos.csv", board], cwd=cwd)
        self.cmd(ctx, "ipcd356", ["pcb", "export", "ipcd356", "-o", f"{o}/n.d356", board], cwd=cwd)
        self.cmd(ctx, "drc", ["pcb", "drc", "--all-track-errors", "--format", "json", "-o", f"{o}/drc.json", board], cwd=cwd)
        return o

    def gerber_same_shape(self, a, b):
        """文字で違うガーバーを、形で比べ直す（並びや D コードの番号が変わっただけなら同じ）。
        違えば kicad_gerber diff の要約、同じ・比べられないなら None"""
        if not self.a.gerber:
            return "（形で比べる道具なし）"
        try:
            p = subprocess.run([self.a.gerber, "diff", a, b], capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return None
        out = p.stdout.strip().splitlines()
        if p.returncode != 0 or any("差なし" in x for x in out):
            return None
        return "\n".join(out[-6:])

    def compare_outputs(self, ctx, a, b, check, dirs):
        diffs = []
        for d in silent.compare_dirs(f"{a}/g", f"{b}/g", dirs, "g"):
            kind, ex = d
            # ファイルの中身の違い（g.gtl: ...）は形で確かめる。ファイルの組の違いはそのまま
            f = kind.split(":")[0]
            if f.startswith("g.") and "ファイルの組" not in kind:
                name = [x for x in os.listdir(f"{a}/g") if x.endswith(f[1:])]
                shapes = [self.gerber_same_shape(f"{a}/g/{x}", f"{b}/g/{x}") for x in name if os.path.exists(f"{b}/g/{x}")]
                shapes = [x for x in shapes if x]
                if not shapes:
                    continue
                diffs.append((f"{f}: 形が違う", shapes[0]))
            else:
                diffs.append(d)
        for sub in ("d", "pos.csv", "n.d356"):
            diffs += silent.compare_dirs(f"{a}/{sub}", f"{b}/{sub}", dirs, sub)
        diffs += silent.compare_violation_counts(f"{a}/drc.json", f"{b}/drc.json", "drc")
        self.silent(ctx, check, diffs)

    def do_pcb(self, ctx, board=None):
        w = ctx["w"]
        board = board or ctx["path"]
        bdir = os.path.dirname(os.path.abspath(board))
        # 1) 落ちないか: 出力を一通り
        o1 = self.board_outputs(ctx, board, "o1", bdir)
        for name, args in [
            ("drc-fill", ["pcb", "drc", "--format", "json", "--schematic-parity", "--refill-zones", "-o", f"{w}/fill.json", board]),
            ("ipc2581", ["pcb", "export", "ipc2581", "-o", f"{w}/x.xml", board]),
            ("odb", ["pcb", "export", "odb", "-o", f"{w}/odb.zip", board]),
            ("gencad", ["pcb", "export", "gencad", "-o", f"{w}/b.cad", board]),
            ("stats", ["pcb", "export", "stats", "-o", f"{w}/s.txt", board]),
            ("svg", ["pcb", "export", "svg", "--mode-multi", "-l", "F.Cu,B.Cu,F.SilkS,Edge.Cuts", "-o", f"{w}/svg/", board]),
            ("pdf", ["pcb", "export", "pdf", "-l", "F.Cu,In1.Cu,B.Cu,F.Mask,F.Fab", "-o", f"{w}/b.pdf", board]),
            ("dxf", ["pcb", "export", "dxf", "-l", "F.Cu,Edge.Cuts", "-o", f"{w}/dxf/", board]),
            ("step", ["pcb", "export", "step", "--board-only", "--force", "-o", f"{w}/b.step", board]),
        ]:
            self.cmd(ctx, name, args, cwd=bdir)
        # 2) 同じ出力を 2 回: 同じになるはず
        self.cmd(ctx, "gerbers-again", ["pcb", "export", "gerbers", "-o", f"{w}/g2/", board], cwd=bdir)
        if os.path.isdir(f"{o1}/g"):
            self.silent(ctx, "export-twice", silent.compare_dirs(f"{o1}/g", f"{w}/g2", (), "g"))
        # 出したガーバーを読み戻す（読み込み側が落ちないか）
        gs = sorted(os.listdir(f"{o1}/g")) if os.path.isdir(f"{o1}/g") else []
        for g in [x for x in gs if "_Cu" in x][:2] + [x for x in gs if "Edge_Cuts" in x][:1]:
            self.cmd(ctx, "gerber-readback", ["info", f"{o1}/g/{g}"], tool="gerber")
        # 3) 読む→保存→読む: 出力も DRC も変わらないはず（プロジェクトの設定も一緒に写す）
        up = f"{w}/up"
        os.makedirs(up)
        stem = os.path.splitext(os.path.basename(board))[0]
        for ext in (".kicad_pro", ".kicad_dru", ".kicad_prl"):
            if os.path.exists(f"{bdir}/{stem}{ext}"):
                shutil.copy(f"{bdir}/{stem}{ext}", up)
        for t in ("fp-lib-table",):
            if os.path.exists(f"{bdir}/{t}"):
                shutil.copy(f"{bdir}/{t}", up)
        copy = f"{up}/{os.path.basename(board)}"
        shutil.copy(board, copy)
        rc = self.cmd(ctx, "pcb-upgrade", ["pcb", "upgrade", "--force", copy], cwd=up)
        saved = f"{up}/{stem}.kicad_pcb"
        if not os.path.exists(saved):
            saved = copy          # 旧形式（.brd）は同じ名前のまま新しい形式で上書きされる
        if rc != 0 or not os.path.exists(saved):
            self.silent(ctx, "upgrade-failed", [("pcb upgrade が失敗", f"rc={rc}")])
            return
        o2 = self.board_outputs(ctx, saved, "o2", up)
        # 元が今の形式なら、元と保存し直した物を比べる。古い形式は変換で変わって当然なので、
        # 1 回保存した物（今の形式）を基準に、もう 1 回保存した物と比べる
        current = format_version(board) == format_version(saved)
        if current:
            self.compare_outputs(ctx, o1, o2, "roundtrip", (bdir, up))
        # 4) 保存をもう 1 回: 中身が同じになるはず（保存の不動点）
        up2 = f"{w}/up2"
        shutil.copytree(up, up2)
        saved2 = os.path.join(up2, os.path.basename(saved))
        self.cmd(ctx, "pcb-upgrade-again", ["pcb", "upgrade", "--force", saved2], cwd=up2)
        self.silent(ctx, "save-twice", silent.compare_dirs(saved, saved2, (up, up2), "kicad_pcb"))
        if not current:
            o3 = self.board_outputs(ctx, saved2, "o3", up2)
            self.compare_outputs(ctx, o2, o3, "roundtrip", (up, up2))
        # DRC をもう 1 回（同じ物に同じ結果が出るか。違えば並列処理の揺れ）
        flaky = False
        if os.path.getsize(saved) < 3_000_000:
            self.cmd(ctx, "drc-again", ["pcb", "drc", "--all-track-errors", "--format", "json", "-o", f"{o2}/drc2.json", saved], cwd=up)
            d = silent.compare_violation_counts(f"{o2}/drc.json", f"{o2}/drc2.json", "drc-twice")
            flaky = bool(d)
            self.silent(ctx, "drc-twice", d)
        # 5) 基板ごと平行移動: DRC の違反の数（種類ごと）は変わらないはず
        tr = f"{w}/tr"
        shutil.copytree(up, tr)
        moved = os.path.join(tr, os.path.basename(saved))
        if not flaky and silent.translate_board(saved, moved):
            self.cmd(ctx, "drc-moved", ["pcb", "drc", "--all-track-errors", "--format", "json", "-o", f"{tr}/drc.json", moved], cwd=tr)
            ca, cb = silent.violation_counts(f"{o2}/drc.json"), silent.violation_counts(f"{tr}/drc.json")
            diffs = []
            if ca is not None and cb is not None and ca != cb:
                keys = sorted(set(ca) | set(cb), key=str)
                ch = [f"{k}:{ca.get(k, 0)}→{cb.get(k, 0)}" for k in keys if ca.get(k, 0) != cb.get(k, 0)]
                diffs.append((f"move: DRC の数が変わる [{','.join(sorted(k.split(':')[0] for k in ch)[:4])}]", " ".join(ch)))
            self.silent(ctx, "move", diffs)

    def do_import(self, ctx):
        out = f"{ctx['w']}/imp/imported.kicad_pcb"
        os.makedirs(os.path.dirname(out))
        self.cmd(ctx, "import", ["pcb", "import", "--report-format", "text", "--report-file", f"{ctx['w']}/rep.txt",
                                 "-o", out, ctx["path"]])
        if os.path.exists(out):
            # 取り込んだ物は、そのまま読み直せるはず
            rc = self.cmd(ctx, "import-reload", ["pcb", "export", "stats", "-o", f"{ctx['w']}/st.txt", out], cwd=os.path.dirname(out))
            if rc != 0:
                self.silent(ctx, "import-reload", [("取り込んだ基板が読み直せない", f"rc={rc}")])
                return
            self.do_pcb(ctx, out)

    # ---- 回路図 ----
    def sch_outputs(self, ctx, sch, tag, cwd):
        o = f"{ctx['w']}/{tag}"
        os.makedirs(o, exist_ok=True)
        self.cmd(ctx, "netlist", ["sch", "export", "netlist", "-o", f"{o}/n.net", sch], cwd=cwd)
        self.cmd(ctx, "bom", ["sch", "export", "bom", "-o", f"{o}/bom.csv", sch], cwd=cwd)
        self.cmd(ctx, "erc", ["sch", "erc", "--format", "json", "-o", f"{o}/erc.json", sch], cwd=cwd)
        return o

    def do_sch(self, ctx):
        w, path = ctx["w"], ctx["path"]
        sdir = ctx["cwd"]
        o1 = self.sch_outputs(ctx, path, "o1", sdir)
        for name, args in [
            ("netlist-spice", ["sch", "export", "netlist", "--format", "spice", "-o", f"{w}/n.cir", path]),
            ("netlist-xml", ["sch", "export", "netlist", "--format", "kicadxml", "-o", f"{w}/n.xml", path]),
            ("sch-pdf", ["sch", "export", "pdf", "-o", f"{w}/s.pdf", path]),
            ("sch-svg", ["sch", "export", "svg", "-o", f"{w}/ssvg/", path]),
        ]:
            self.cmd(ctx, name, args)
        # 読む→保存→読む（階層のため、回路図のフォルダの中の設計のファイルを写す。大きすぎるときは比べない）
        up = f"{w}/up"
        if not copy_design_dir(sdir, up):
            return
        copy = os.path.join(up, os.path.relpath(path, sdir))
        rc = self.cmd(ctx, "sch-upgrade", ["sch", "upgrade", "--force", copy], cwd=os.path.dirname(copy))
        saved = os.path.splitext(copy)[0] + ".kicad_sch"
        if rc != 0 or not os.path.exists(saved):
            self.silent(ctx, "upgrade-failed", [("sch upgrade が失敗", f"rc={rc}")])
            return
        o2 = self.sch_outputs(ctx, saved, "o2", os.path.dirname(saved))
        current = format_version(path) == format_version(saved)
        if current:
            self.silent(ctx, "roundtrip", sch_diffs(o1, o2, (sdir, up)))
        up2 = f"{w}/up2"
        shutil.copytree(up, up2)
        copy2 = os.path.join(up2, os.path.relpath(saved, up))
        self.cmd(ctx, "sch-upgrade-again", ["sch", "upgrade", "--force", copy2], cwd=os.path.dirname(copy2))
        self.silent(ctx, "save-twice", silent.compare_dirs(saved, copy2, (up, up2), "kicad_sch"))
        if not current:
            o3 = self.sch_outputs(ctx, copy2, "o3", os.path.dirname(copy2))
            self.silent(ctx, "roundtrip", sch_diffs(o2, o3, (up, up2)))

    # ---- ライブラリ・ガーバー ----
    def do_symlib(self, ctx):
        out = f"{ctx['w']}/out.kicad_sym"
        self.cmd(ctx, "sym-upgrade", ["sym", "upgrade", "--force", "-o", out, ctx["path"]])
        if os.path.exists(out):
            self.cmd(ctx, "sym-svg", ["sym", "export", "svg", "-o", f"{ctx['w']}/symsvg/", out])
            out2 = f"{ctx['w']}/out2.kicad_sym"
            self.cmd(ctx, "sym-upgrade-again", ["sym", "upgrade", "--force", "-o", out2, out])
            self.silent(ctx, "save-twice", silent.compare_dirs(out, out2, (), "kicad_sym"))

    def do_fplib(self, ctx):
        out = f"{ctx['w']}/out.pretty"
        self.cmd(ctx, "fp-upgrade", ["fp", "upgrade", "--force", "-o", out, ctx["path"]])
        if os.path.isdir(out):
            self.cmd(ctx, "fp-svg", ["fp", "export", "svg", "-o", f"{ctx['w']}/fpsvg/", out])
            out2 = f"{ctx['w']}/out2.pretty"
            self.cmd(ctx, "fp-upgrade-again", ["fp", "upgrade", "--force", "-o", out2, out])
            self.silent(ctx, "save-twice", silent.compare_dirs(out, out2, (), "kicad_mod"))

    def do_lbr(self, ctx):
        self.do_symlib(ctx)
        self.do_fplib(ctx)

    def do_gerber(self, ctx):
        self.cmd(ctx, "gerber-info", ["info", ctx["path"]], tool="gerber")


def sch_diffs(a, b, dirs):
    # ネットリストは行の順と net の番号（code）と Sheetfile の場所を見ない
    diffs = silent.compare_dirs(f"{a}/n.net", f"{b}/n.net", dirs, "netlist", unordered=True,
                                drop=r'^\s*\(code "\d+"\)$|Sheetfile|^\s*\(value "[^"]*\.(kicad_)?sch"\)$')
    diffs += silent.compare_dirs(f"{a}/bom.csv", f"{b}/bom.csv", dirs, "bom", unordered=True)
    diffs += silent.compare_violations(f"{a}/erc.json", f"{b}/erc.json", "erc")
    return diffs


DESIGN_EXT = (".kicad_sch", ".sch", ".kicad_pro", ".kicad_prl", ".kicad_sym", ".lib", ".dcm", ".kicad_dbl")


def copy_design_dir(src, dst, max_files=400, max_bytes=60_000_000):
    """回路図のフォルダから、設計のファイルだけを（下のフォルダも）写す"""
    files, total = [], 0
    for root, dirs, fs in os.walk(src):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in fs:
            if f.endswith(DESIGN_EXT) or f in ("sym-lib-table", "fp-lib-table"):
                p = os.path.join(root, f)
                files.append(p)
                total += os.path.getsize(p)
                if len(files) > max_files or total > max_bytes:
                    return False
    for p in files:
        q = os.path.join(dst, os.path.relpath(p, src))
        os.makedirs(os.path.dirname(q), exist_ok=True)
        shutil.copy(p, q)
    return True


def format_version(path):
    """形式の版（食い違いを寄せるときに古い形式と分けるため）"""
    if os.path.isdir(path):
        return ""
    h = head(path, 300)
    m = re.search(rb"\(version (\d+)", h)
    if m:
        return "v" + m.group(1).decode()
    if h.startswith(b"PCBNEW-BOARD") or h.startswith(b"EESchema"):
        return "legacy"
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--cli", required=True)
    ap.add_argument("--gerber")
    ap.add_argument("--out", required=True)
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--budget-min", type=float, default=300)
    ap.add_argument("--limit", type=int, default=0, help="試しに先頭 N 件だけ")
    ap.add_argument("--kinds", default="", help="この種類だけ（, 区切り）")
    ap.add_argument("--tmp", default=None)
    a = ap.parse_args()
    a.cli = os.path.abspath(a.cli)
    a.gerber = os.path.abspath(a.gerber) if a.gerber else None
    os.makedirs(a.out, exist_ok=True)
    a.tmp = a.tmp or tempfile.mkdtemp(prefix="bughunt")
    i, n = map(int, a.shard.split("/"))

    items = enumerate_items(a.roots)
    allcount = {}
    for p, k in items.values():
        allcount[k] = allcount.get(k, 0) + 1
    mine = [(h, p, k) for h, (p, k) in items.items() if int(h, 16) % n == i]
    if a.kinds:
        ks = set(a.kinds.split(","))
        mine = [x for x in mine if x[2] in ks]
    random.Random(1).shuffle(mine)       # 種類が偏らないように（時間切れで後ろが落ちても）
    if a.limit:
        mine = mine[:a.limit]
    print(f"材料（重複なし）: {len(items)} {allcount}  このシャード {i}/{n}: {len(mine)}", flush=True)

    r = Runner(a)
    t0 = time.time()
    done = [0]

    def work(x):
        r.item(*x)
        with r.lock:
            done[0] += 1
            if done[0] % 50 == 0:
                print(f"  {done[0]}/{len(mine)} 件 {int(time.time() - t0)}s", flush=True)

    with ThreadPoolExecutor(a.jobs) as ex:
        list(ex.map(work, mine))
    summ = dict(shard=a.shard, items_total=len(items), kinds_total=allcount, items_shard=len(mine),
                items_skipped_budget=r.skipped, runs=r.n, secs=int(time.time() - t0))
    with open(os.path.join(a.out, "summary.json"), "w") as f:
        json.dump(summ, f, ensure_ascii=False, indent=1)
    print(json.dumps(summ, ensure_ascii=False))
    shutil.rmtree(a.tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
