#!/usr/bin/env python3
"""KiCad の単体試験（qa_*）を、試験の組（一番上の suite）ごとに別のプロセスで流す。
1 つの組がサニタイザで止まっても、残りは流れる。結果は run.py と同じ形（runs.jsonl・logs/）で triage.py に渡せる。

使い方: run_qa.py --out out/ --jobs 4 --timeout 1800 qa_pcbnew qa_eeschema ...（画面が要る物があるので xvfb-run の下で）
"""
import argparse, json, os, re, subprocess, sys, tempfile, threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import SAN_RE, KEEP_BYTES  # noqa: E402


def suites(exe):
    p = subprocess.run([exe, "--list_content"], capture_output=True, text=True, timeout=300)
    names = []
    for ln in (p.stdout + p.stderr).splitlines():
        m = re.match(r"^(\w+)\*?$", ln.rstrip())   # 字下げの無い行 = 一番上の組（* は既定で流れる物）
        if m and ln.rstrip().endswith("*"):
            names.append(m.group(1))
    return names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exes", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=1800)
    a = ap.parse_args()
    logs = os.path.join(a.out, "logs")
    os.makedirs(logs, exist_ok=True)
    out = open(os.path.join(a.out, "runs.jsonl"), "a")
    lock = threading.Lock()
    work = []
    for exe in a.exes:
        ss = suites(exe)
        print(f"{os.path.basename(exe)}: {len(ss)} 組", flush=True)
        work += [(exe, s) for s in ss] or [(exe, None)]

    def one(x):
        exe, s = x
        name = os.path.basename(exe) + (f":{s}" if s else "")
        args = [exe, "--log_level=error", "--report_level=short"] + ([f"--run_test={s}"] if s else [])
        with tempfile.TemporaryFile() as f:
            p = subprocess.Popen(args, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                 cwd=os.path.dirname(exe), start_new_session=True)
            to = False
            try:
                rc = p.wait(timeout=a.timeout)
            except subprocess.TimeoutExpired:
                to = True
                os.killpg(p.pid, 9)
                rc = p.wait()
            f.seek(0)
            data = f.read()
        san = bool(SAN_RE.search(data))
        sig = -rc if rc < 0 else (rc - 128 if 128 < rc < 160 else 0)   # boost の 200/201 は試験の失敗
        log = None
        if san or to or sig or rc != 0:
            log = re.sub(r"[^\w.-]", "_", name) + ".log"
            with open(os.path.join(logs, log), "wb") as g:
                g.write(f"# {' '.join(args)}\n# rc={rc} timeout={to}\n".encode())
                g.write(data[:KEEP_BYTES])
                if len(data) > KEEP_BYTES:
                    g.write(b"\n# ...cut...\n" + data[-KEEP_BYTES // 2:])
        # 試験の失敗（rc=201 など）は sig に数えない。ただし落ちた試験の名前は残す
        failed = re.findall(rb"error: in \"([^\"]+)\"", data)[:20]
        rec = dict(file=name, size=0, kind="qa", cmd=name, rc=rc, sig=sig, timeout=to, san=san,
                   log=log, failed=[x.decode(errors="replace") for x in failed])
        with lock:
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            print(f"  {name}: rc={rc}{' 時間切れ' if to else ''}{' サニタイザ' if san else ''}", flush=True)

    with ThreadPoolExecutor(a.jobs) as ex:
        list(ex.map(one, work))


if __name__ == "__main__":
    main()
